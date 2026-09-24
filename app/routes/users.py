from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.services.user_service import (
    cadastrar_usuario,
    listar_usuarios,
    obter_usuario_por_id,
    alterar_perfil_usuario,
    alterar_status_usuario,
    deletar_usuario
)
from app.services.security_service import verificar_admin_api_key


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


class PerfilUpdateRequest(BaseModel):
    perfil: str = Field(..., description="Novo perfil: 'master', 'admin', 'funcionario' ou 'cliente'")


class StatusUpdateRequest(BaseModel):
    status: str = Field(..., description="Novo status: 'ativo' ou 'inativo'")


@router.get("/equipe")
def listar_equipe(empresa_id: int):
    """
    Lista todos os colaboradores da empresa específica para o administrador do tenant.
    """
    usuarios = listar_usuarios(empresa_id=empresa_id)
    return {
        "total": len(usuarios),
        "usuarios": usuarios
    }


@router.post("/equipe", status_code=status.HTTP_201_CREATED)
def criar_membro_equipe(dados: EquipeUsuarioCreateRequest):
    """
    Cadastra um colaborador na empresa do admin.
    Regra estrita: Administradores de empresa NÃO podem criar perfil 'master'.
    Clientes não são usuários de acesso à plataforma (apenas CRM).
    """
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
        "mensagem": "Colaborador adicionado à equipe com sucesso!",
        "usuario_id": resultado["usuario_id"],
        "email": dados.email.strip().lower(),
        "perfil": perfil_normalizado
    }


@router.delete("/equipe/{usuario_id}")
def remover_membro_equipe(usuario_id: int, empresa_id: int):
    """
    Remove um membro da equipe garantindo que pertença à empresa informada e não seja master.
    """
    usuario = obter_usuario_por_id(usuario_id)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado."
        )

    if usuario["empresa_id"] != empresa_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para remover usuários de outra empresa."
        )

    if usuario["perfil"] == "master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Não é permitido remover um usuário Master."
        )

    sucesso = deletar_usuario(usuario_id=usuario_id)
    if not sucesso:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível remover o colaborador."
        )

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

