from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.services.company_service import obter_configuracao_empresa, atualizar_configuracao_empresa
from app.services.security_service import verificar_admin_api_key

router = APIRouter(
    prefix="/empresa",
    tags=["Empresa"]
)


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

