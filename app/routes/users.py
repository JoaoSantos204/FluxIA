from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel, EmailStr, Field

from app.database.database import conectar, _cursor, _placeholder
from app.services.user_service import (
    cadastrar_usuario,
    listar_usuarios,
    obter_usuario_por_id,
    alterar_perfil_usuario,
    alterar_status_usuario,
    deletar_usuario,
    gerar_hash_senha
)
from app.services.security_service import verificar_admin_api_key, exigir_perfil


router = APIRouter(
    prefix="/usuarios",
    tags=["Usuários"]
)


class UsuarioCreateRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa associada")
    nome: str = Field(..., min_length=2, description="Nome completo do usuário")
    email: str = Field(..., description="E-mail único corporativo")
    senha: str = Field(..., min_length=4, description="Senha de acesso")
    perfil: str = Field(default="cliente", description="Perfil de acesso: 'master', 'admin', 'funcionario' ou 'cliente'")


class EquipeUsuarioCreateRequest(BaseModel):
    empresa_id: int = Field(..., description="ID da empresa do colaborador")
    nome: str = Field(..., min_length=2, description="Nome completo")
    email: str = Field(..., description="E-mail do colaborador")
    senha: str = Field(..., min_length=4, description="Senha inicial")
    perfil: str = Field(default="funcionario", description="Perfil: 'admin' ou 'funcionario'")


class EquipeUsuarioUpdateRequest(BaseModel):
    nome: str = Field(..., min_length=2, description="Nome completo")
    email: str = Field(..., description="E-mail do colaborador")
    perfil: str = Field(..., description="Perfil: 'admin' ou 'funcionario'")
    status: str = Field(..., description="Status: 'ativo' ou 'inativo'")
    senha: Optional[str] = Field(None, min_length=4, description="Nova senha opcional")


class PerfilUpdateRequest(BaseModel):
    perfil: str = Field(..., description="Novo perfil: 'master', 'admin', 'funcionario' ou 'cliente'")


class StatusUpdateRequest(BaseModel):
    status: str = Field(..., description="Novo status: 'ativo' ou 'inativo'")


@router.get("/equipe")
def listar_equipe(request: Request, empresa_id: Optional[int] = Query(None)):
    """
    Lista todos os colaboradores da empresa específica para o administrador do tenant.
    """
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)
    usuarios = listar_usuarios(empresa_id=target_empresa)
    return {
        "total": len(usuarios),
        "usuarios": usuarios
    }


@router.post("/equipe", status_code=status.HTTP_201_CREATED)
def criar_membro_equipe(dados: EquipeUsuarioCreateRequest, request: Request):
    """
    Cadastra um colaborador na empresa do admin.
    Regra estrita: Administradores de empresa NÃO podem criar perfil 'master'.
    Clientes não são usuários de acesso à plataforma (apenas CRM).
    """
    operador = exigir_perfil(request, dados.empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (dados.empresa_id or 1)

    perfil_normalizado = dados.perfil.strip().lower()
    if perfil_normalizado == "master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o desenvolvedor master pode criar usuários com perfil master."
        )

    if perfil_normalizado not in ["admin", "funcionario"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Perfil inválido. Escolha 'admin' ou 'funcionario'."
        )

    resultado = cadastrar_usuario(
        empresa_id=target_empresa,
        nome=dados.nome.strip(),
        email=dados.email.strip().lower(),
        senha=dados.senha,
        perfil=perfil_normalizado
    )

    if not resultado["sucesso"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=resultado["erro"]
        )

    return {
        "mensagem": "Colaborador adicionado à equipe com sucesso!",
        "usuario_id": resultado["usuario_id"],
        "email": dados.email.strip().lower(),
        "perfil": perfil_normalizado
    }


@router.put("/equipe/{usuario_id}")
def atualizar_membro_equipe(usuario_id: int, dados: EquipeUsuarioUpdateRequest, request: Request, empresa_id: Optional[int] = Query(None)):
    """
    Edita um membro da equipe (nome, e-mail, perfil, status e senha opcional).
    Garante unicidade de e-mail, proteção de usuário master e impede auto-desativação/rebaixamento do último admin.
    """
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    usuario = obter_usuario_por_id(usuario_id)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado."
        )

    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or usuario["empresa_id"])
    if operador.get("perfil") != "master" and usuario["empresa_id"] != target_empresa:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para alterar usuários de outra empresa."
        )

    if usuario["perfil"] == "master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Não é permitido editar um usuário Master."
        )

    novo_perfil = dados.perfil.strip().lower()
    novo_status = dados.status.strip().lower()
    if novo_perfil not in ["admin", "funcionario"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Perfil deve ser 'admin' ou 'funcionario'.")
    if novo_status not in ["ativo", "inativo"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Status deve ser 'ativo' ou 'inativo'.")

    email_limpo = dados.email.strip().lower()

    conn = conectar()
    cur = _cursor(conn)
    ph = _placeholder()
    try:
        # 1. Verifica duplicidade de e-mail em outro usuário
        cur.execute(f"SELECT id FROM usuarios WHERE LOWER(email) = {ph} AND id != {ph}", (email_limpo, usuario_id))
        if cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Já existe outro usuário cadastrado com este e-mail."
            )

        # 2. Regra do último admin ativo
        if usuario["perfil"] == "admin" and usuario["status"] == "ativo" and (novo_perfil != "admin" or novo_status != "ativo"):
            cur.execute(f"SELECT COUNT(*) as qtd FROM usuarios WHERE empresa_id = {ph} AND perfil = 'admin' AND status = 'ativo' AND id != {ph}", (usuario["empresa_id"], usuario_id))
            row = cur.fetchone()
            qtd = row["qtd"] if row else 0
            if qtd == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Não é possível inativar ou alterar o perfil do único administrador ativo da empresa."
                )

        # 3. Monta query de atualização
        updates = [f"nome = {ph}", f"email = {ph}", f"perfil = {ph}", f"status = {ph}"]
        params = [dados.nome.strip(), email_limpo, novo_perfil, novo_status]

        if dados.senha and dados.senha.strip():
            updates.append(f"senha_hash = {ph}")
            params.append(gerar_hash_senha(dados.senha.strip()))

        params.extend([usuario_id, usuario["empresa_id"]])
        set_str = ", ".join(updates)
        cur.execute(f"UPDATE usuarios SET {set_str} WHERE id = {ph} AND empresa_id = {ph}", tuple(params))
        conn.commit()

        return {
            "mensagem": "Colaborador atualizado com sucesso!",
            "usuario_id": usuario_id,
            "nome": dados.nome.strip(),
            "email": email_limpo,
            "perfil": novo_perfil,
            "status": novo_status
        }
    finally:
        conn.close()


@router.delete("/equipe/{usuario_id}")
def remover_membro_equipe(usuario_id: int, request: Request, empresa_id: Optional[int] = Query(None)):
    """
    Remove um membro da equipe garantindo permissão de admin/master, mesma empresa,
    bloqueando auto-exclusão, proteção do perfil master e proteção do último administrador ativo.
    """
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])

    if operador.get("id") == usuario_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Você não pode excluir sua própria conta de administrador."
        )

    usuario = obter_usuario_por_id(usuario_id)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado."
        )

    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or usuario["empresa_id"])
    if operador.get("perfil") != "master" and usuario["empresa_id"] != target_empresa:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para remover usuários de outra empresa."
        )

    if usuario["perfil"] == "master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Não é permitido remover um usuário Master."
        )

    # Verifica se é o último admin ativo da empresa
    if usuario["perfil"] == "admin" and usuario["status"] == "ativo":
        conn = conectar()
        cur = _cursor(conn)
        ph = _placeholder()
        try:
            cur.execute(f"SELECT COUNT(*) as qtd FROM usuarios WHERE empresa_id = {ph} AND perfil = 'admin' AND status = 'ativo' AND id != {ph}", (usuario["empresa_id"], usuario_id))
            row = cur.fetchone()
            qtd = row["qtd"] if row else 0
            if qtd == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Não é possível remover o único administrador ativo da empresa."
                )
        finally:
            conn.close()

    conn = conectar()
    cur = _cursor(conn)
    ph = _placeholder()
    try:
        cur.execute(f"DELETE FROM usuarios WHERE id = {ph} AND empresa_id = {ph}", (usuario_id, usuario["empresa_id"]))
        conn.commit()
    finally:
        conn.close()

    return {"mensagem": "Colaborador removido da equipe com sucesso."}


# ===== ROTAS MASTER / DEVELOPER (EXIGEM X-ADMIN-API-KEY) =====

@router.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_admin_api_key)])
def criar_novo_usuario(dados: UsuarioCreateRequest):
    """
    Cadastra um novo usuário no sistema (exige X-Admin-API-Key).
    """
    perfil_normalizado = dados.perfil.strip().lower()
    if perfil_normalizado not in ["master", "admin", "funcionario", "cliente"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Perfil inválido. Escolha 'master', 'admin', 'funcionario' ou 'cliente'."
        )

    resultado = cadastrar_usuario(
        empresa_id=dados.empresa_id,
        nome=dados.nome.strip(),
        email=dados.email.strip().lower(),
        senha=dados.senha,
        perfil=perfil_normalizado
    )

    if not resultado["sucesso"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=resultado["erro"]
        )

    return {
        "mensagem": "Usuário cadastrado com sucesso!",
        "usuario_id": resultado["usuario_id"],
        "email": dados.email.strip().lower(),
        "perfil": perfil_normalizado
    }


@router.get("/", dependencies=[Depends(verificar_admin_api_key)])
def listar_todos_usuarios(empresa_id: int | None = None):
    """
    Lista todos os usuários cadastrados, opcionalmente filtrando por empresa.
    """
    usuarios = listar_usuarios(empresa_id=empresa_id)
    return {
        "total": len(usuarios),
        "usuarios": usuarios
    }


@router.get("/{usuario_id}", dependencies=[Depends(verificar_admin_api_key)])
def obter_detalhes_usuario(usuario_id: int):
    """
    Retorna os detalhes de um usuário específico por ID.
    """
    usuario = obter_usuario_por_id(usuario_id)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado."
        )
    return usuario


@router.patch("/{usuario_id}/perfil", dependencies=[Depends(verificar_admin_api_key)])
def atualizar_perfil(usuario_id: int, dados: PerfilUpdateRequest):
    """
    Altera o nível de permissão (RBAC) do usuário ('master', 'admin', 'funcionario' ou 'cliente').
    """
    perfil_normalizado = dados.perfil.strip().lower()
    resultado = alterar_perfil_usuario(usuario_id=usuario_id, novo_perfil=perfil_normalizado)

    if not resultado["sucesso"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado["erro"]
        )

    return {
        "mensagem": f"Perfil do usuário {usuario_id} atualizado para '{perfil_normalizado}'.",
        "usuario_id": usuario_id,
        "novo_perfil": perfil_normalizado
    }


@router.patch("/{usuario_id}/status", dependencies=[Depends(verificar_admin_api_key)])
def atualizar_status(usuario_id: int, dados: StatusUpdateRequest):
    """
    Altera o status do usuário para 'ativo' ou 'inativo'.
    """
    status_normalizado = dados.status.strip().lower()
    resultado = alterar_status_usuario(usuario_id=usuario_id, novo_status=status_normalizado)

    if not resultado["sucesso"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado["erro"]
        )

    return {
        "mensagem": f"Status do usuário {usuario_id} alterado para '{status_normalizado}'.",
        "usuario_id": usuario_id,
        "novo_status": status_normalizado
    }


@router.delete("/{usuario_id}", dependencies=[Depends(verificar_admin_api_key)])
def remover_usuario(usuario_id: int):
    """
    Remove permanentemente um usuário pelo ID.
    """
    sucesso = deletar_usuario(usuario_id=usuario_id)
    if not sucesso:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado para exclusão."
        )

    return {
        "mensagem": f"Usuário {usuario_id} excluído com sucesso."
    }

