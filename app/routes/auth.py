import os
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.services.user_service import autenticar_usuario, redefinir_senha_usuario

router = APIRouter(
    prefix="/auth",
    tags=["Autenticação"]
)


class LoginRequest(BaseModel):
    email: str = Field(..., description="E-mail de acesso")
    senha: str = Field(..., min_length=1, description="Senha de acesso")


class RedefinirSenhaRequest(BaseModel):
    email: str = Field(..., description="E-mail do usuário")
    nova_senha: str = Field(..., min_length=4, description="Nova senha de acesso")
    admin_key: str = Field(..., description="Chave administrativa (ADMIN_API_KEY) para validação")


@router.post("/login")
def login(dados: LoginRequest):
    """
    Autentica o usuário no sistema. Valida email, senha_hash e se está ativo.
    """
    resultado = autenticar_usuario(email=dados.email, senha=dados.senha)

    if not resultado["sucesso"]:
        status_code = status.HTTP_403_FORBIDDEN if "desativada" in resultado["erro"] else status.HTTP_401_UNAUTHORIZED
        raise HTTPException(
            status_code=status_code,
            detail=resultado["erro"]
        )

    return {
        "sucesso": True,
        "mensagem": "Login realizado com sucesso!",
        "usuario": resultado["usuario"]
    }


@router.post("/redefinir-senha")
def redefinir_senha(dados: RedefinirSenhaRequest):
    """
    Permite redefinir a senha de um usuário mediante a apresentação da ADMIN_API_KEY.
    """
    chave_esperada = os.getenv("ADMIN_API_KEY", "fluxia-admin-secret-key-2026")
    if dados.admin_key != chave_esperada:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave administrativa inválida."
        )

    sucesso = redefinir_senha_usuario(email=dados.email, nova_senha=dados.nova_senha)
    if not sucesso:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário com esse e-mail não foi encontrado."
        )

    return {
        "sucesso": True,
        "mensagem": f"Senha do usuário {dados.email} redefinida com sucesso!"
    }
