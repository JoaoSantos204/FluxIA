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
    tags=["Usuários"],
    dependencies=[Depends(verificar_admin_api_key)]
)


class UsuarioCreateRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa associada")
    nome: str = Field(..., min_length=2, description="Nome completo do usuário")
    email: str = Field(..., description="E-mail único corporativo")
    senha: str = Field(..., min_length=4, description="Senha de acesso")
    perfil: str = Field(default="cliente", description="Perfil de acesso: 'admin', 'funcionario' ou 'cliente'")


class PerfilUpdateRequest(BaseModel):
    perfil: str = Field(..., description="Novo perfil: 'admin', 'funcionario' ou 'cliente'")


class StatusUpdateRequest(BaseModel):
    status: str = Field(..., description="Novo status: 'ativo' ou 'inativo'")


@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_novo_usuario(dados: UsuarioCreateRequest):
    """
    Cadastra um novo usuário no sistema (exige X-Admin-API-Key).
    """
    perfil_normalizado = dados.perfil.strip().lower()
    if perfil_normalizado not in ["admin", "funcionario", "cliente"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Perfil inválido. Escolha 'admin', 'funcionario' ou 'cliente'."
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


@router.get("/")
def listar_todos_usuarios(empresa_id: int | None = None):
    """
    Lista todos os usuários cadastrados, opcionalmente filtrando por empresa.
    """
    usuarios = listar_usuarios(empresa_id=empresa_id)
    return {
        "total": len(usuarios),
        "usuarios": usuarios
    }


@router.get("/{usuario_id}")
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


@router.patch("/{usuario_id}/perfil")
def atualizar_perfil(usuario_id: int, dados: PerfilUpdateRequest):
    """
    Altera o nível de permissão (RBAC) do usuário ('admin', 'funcionario' ou 'cliente').
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


@router.patch("/{usuario_id}/status")
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


@router.delete("/{usuario_id}")
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

