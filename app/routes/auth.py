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


class CadastroEmpresaRequest(BaseModel):
    nome_empresa: str = Field(..., min_length=2, description="Nome da empresa ou razão social")
    cnpj: str = Field(..., min_length=11, description="CNPJ da empresa (obrigatório)")
    nome_responsavel: str = Field(..., min_length=2, description="Nome completo do administrador responsável")
    email: str = Field(..., description="E-mail de acesso corporativo")
    senha: str = Field(..., min_length=4, description="Senha inicial de acesso (mínimo 4 caracteres)")


@router.post("/cadastro-empresa", status_code=status.HTTP_201_CREATED)
def cadastrar_nova_empresa_self_service(dados: CadastroEmpresaRequest):
    """
    Auto-cadastro de nova empresa (SaaS Self-Service Onboarding).
    Cria a empresa com CNPJ obrigatório e o primeiro usuário com perfil 'admin'.
    """
    from app.services.user_service import cadastrar_empresa_com_admin

    resultado = cadastrar_empresa_com_admin(
        nome_empresa=dados.nome_empresa,
        cnpj=dados.cnpj,
        nome_responsavel=dados.nome_responsavel,
        email=dados.email,
        senha=dados.senha
    )

    if not resultado["sucesso"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado["erro"]
        )

    return {
        "sucesso": True,
        "mensagem": "Empresa e usuário administrador criados com sucesso!",
        "usuario": resultado["usuario"],
        "empresa": resultado["empresa"]
    }

