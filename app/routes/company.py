from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.services.company_service import (
    obter_configuracao_empresa, 
    atualizar_configuracao_empresa,
    listar_empresas,
    cadastrar_empresa,
    deletar_empresa
)
from app.services.security_service import verificar_admin_api_key

router = APIRouter(
    prefix="/empresa",
    tags=["Empresa"]
)


class EmpresaCreateRequest(BaseModel):
    nome: str = Field(..., min_length=2, description="Nome da empresa ou ambiente")
    cnpj_ou_identificador: str | None = Field(default=None, description="CNPJ ou identificador único")



class ConfiguracaoSuporteRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    numero_suporte_humano: str = Field(..., description="Telefone ou WhatsApp do suporte humano")
    mensagem_suporte: str = Field(..., description="Mensagem de orientação de suporte")


@router.get("/configuracoes")
def consultar_configuracoes(empresa_id: int = 1):
    """
    Retorna as configurações atuais de suporte da empresa.
    """
    config = obter_configuracao_empresa(empresa_id=empresa_id)
    return config


@router.put("/configuracoes", dependencies=[Depends(verificar_admin_api_key)])
def alterar_configuracoes(dados: ConfiguracaoSuporteRequest):
    """
    Atualiza as configurações de suporte humano da empresa (exige X-Admin-API-Key).
    """
    if not dados.numero_suporte_humano.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O número de suporte humano não pode estar vazio."
        )

    config_atualizada = atualizar_configuracao_empresa(
        empresa_id=dados.empresa_id,
        numero_suporte_humano=dados.numero_suporte_humano.strip(),
        mensagem_suporte=dados.mensagem_suporte.strip()
    )

    return {
        "mensagem": "Configurações de suporte atualizadas com sucesso!",
        "configuracao": config_atualizada
    }


@router.get("/")
def listar_todas_empresas():
    """
    Retorna a lista de todas as empresas/ambientes cadastrados.
    """
    empresas = listar_empresas()
    return {
        "total": len(empresas),
        "empresas": empresas
    }


@router.post("/", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_admin_api_key)])
def criar_nova_empresa(dados: EmpresaCreateRequest):
    """
    Cadastra uma nova empresa/ambiente no sistema (exige X-Admin-API-Key).
    """
    resultado = cadastrar_empresa(
        nome=dados.nome.strip(),
        cnpj_ou_identificador=dados.cnpj_ou_identificador.strip() if dados.cnpj_ou_identificador else None
    )

    if not resultado["sucesso"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado["erro"]
        )

    return {
        "mensagem": "Empresa cadastrada com sucesso!",
        "empresa": resultado["empresa"]
    }


@router.delete("/{empresa_id}", dependencies=[Depends(verificar_admin_api_key)])
def remover_empresa(empresa_id: int):
    """
    Remove uma empresa/ambiente pelo ID (exceto a empresa padrão ID 1).
    """
    if empresa_id == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A empresa padrão (ID 1) não pode ser excluída."
        )

    sucesso = deletar_empresa(empresa_id)
    if not sucesso:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Empresa não encontrada para exclusão."
        )

    return {
        "mensagem": f"Empresa {empresa_id} removida com sucesso."
    }


