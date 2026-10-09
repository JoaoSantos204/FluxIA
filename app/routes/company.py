import os
import logging
import requests
from fastapi import APIRouter, Depends, HTTPException, status, Query, Header, Request
from pydantic import BaseModel, Field
from typing import Optional

logger = logging.getLogger(__name__)

from app.services.company_service import (
    obter_configuracao_empresa, 
    atualizar_configuracao_empresa,
    atualizar_gemini_api_key,
    atualizar_configuracao_ia,
    atualizar_fuso_horario,
    atualizar_telegram_bot_empresa,
    buscar_empresa_por_token_telegram,
    buscar_empresa_por_bot_username_telegram,
    listar_empresas,
    cadastrar_empresa,
    deletar_empresa,
    salvar_configuracao_ia_regras,
    salvar_configuracao_followup
)
from app.services.security_service import verificar_admin_api_key, validar_perfil_admin_ou_master

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


class GeminiApiKeyRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    gemini_api_key: Optional[str] = Field(default="", description="Chave de API do Gemini")
    usuario_id: Optional[int] = Field(default=None, description="ID do usuário que realiza a alteração")
    usuario_perfil: Optional[str] = Field(default=None, description="Perfil do usuário que realiza a alteração ('admin' ou 'master')")


class MultiVendorIARequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    provedor_ia_padrao: str = Field(default="google", description="Provedor padrão: 'google' ou 'openai'")
    gemini_api_key: Optional[str] = Field(default=None, description="Chave de API do Google Gemini (BYOK)")
    openai_api_key: Optional[str] = Field(default=None, description="Chave de API da OpenAI (BYOK)")
    usuario_id: Optional[int] = Field(default=None, description="ID do usuário")
    usuario_perfil: Optional[str] = Field(default=None, description="Perfil do usuário")


class FusoHorarioRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    fuso_horario: str = Field(default="America/Sao_Paulo", description="Identificador IANA do fuso horário (ex: 'America/Sao_Paulo', 'auto')")
    usuario_id: Optional[int] = Field(default=None, description="ID do usuário")
    usuario_perfil: Optional[str] = Field(default=None, description="Perfil do usuário")


class TelegramBotConfigRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    telegram_bot_token: Optional[str] = Field(default="", description="Token do bot gerado pelo @BotFather")
    url_webhook_base: Optional[str] = Field(default=None, description="URL pública base opcional para o webhook")
    usuario_id: Optional[int] = Field(default=None, description="ID do usuário")
    usuario_perfil: Optional[str] = Field(default=None, description="Perfil do usuário")


class IARegrasRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    ia_prompt_sistema: Optional[str] = Field(default=None, description="Diretrizes e tom de voz da IA")
    ia_coletar_dados_obrigatorio: bool = Field(default=True, description="Exigir Nome e Telefone no início do atendimento")
    usuario_id: Optional[int] = None
    usuario_perfil: Optional[str] = None


class FollowupConfigRequest(BaseModel):
    empresa_id: int = Field(default=1, description="ID da empresa")
    followup_ativo: bool = Field(default=True, description="Ativar agendador de follow-up")
    followup_horas_inatividade: int = Field(default=24, ge=1, le=720, description="Horas de inatividade antes do disparo")
    followup_mensagem_personalizada: Optional[str] = Field(default=None, description="Modelo de mensagem ou diretrizes customizadas")
    usuario_id: Optional[int] = None
    usuario_perfil: Optional[str] = None


@router.get("/configuracoes")
def consultar_configuracoes(
    empresa_id: int = 1,
    usuario_id: Optional[int] = Query(None),
    usuario_perfil: Optional[str] = Query(None),
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """
    Retorna as configurações atuais de suporte e IA da empresa.
    Exige perfil admin ou master.
    """
    if usuario_perfil:
        if usuario_perfil.strip().lower() not in ("admin", "master"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas administradores podem acessar as configurações da empresa."
            )
    else:
        uid = usuario_id or x_user_id
        if uid:
            validar_perfil_admin_ou_master(uid)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identificação do usuário (usuario_id, cabeçalho X-User-Id ou usuario_perfil) é obrigatória para acessar as configurações."
            )

    config = obter_configuracao_empresa(empresa_id=empresa_id)
    return config


@router.post("/api-key")
def salvar_api_key_empresa(
    dados: GeminiApiKeyRequest,
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """
    Cadastra ou atualiza a chave de API própria (BYOK) da empresa.
    Exige perfil admin ou master.
    """
    if dados.usuario_perfil:
        if dados.usuario_perfil.strip().lower() not in ("admin", "master"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas administradores podem alterar a chave de API da empresa."
            )
    else:
        uid = dados.usuario_id or x_user_id
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identificação do usuário (usuario_id, cabeçalho X-User-Id ou usuario_perfil) é obrigatória para salvar a chave de API."
            )
        validar_perfil_admin_ou_master(uid)

    resultado = atualizar_gemini_api_key(
        empresa_id=dados.empresa_id,
        gemini_api_key=dados.gemini_api_key
    )

    return {
        "mensagem": "Chave de API da empresa atualizada com sucesso!",
        "configuracao": resultado
    }


@router.post("/provedor-ia")
def salvar_provedor_ia_empresa(
    dados: MultiVendorIARequest,
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """
    Configura o provedor de IA padrão (Google ou OpenAI) e cadastra chaves BYOK de ambos os vendors.
    Exige perfil admin ou master.
    """
    if dados.usuario_perfil:
        if dados.usuario_perfil.strip().lower() not in ("admin", "master"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas administradores podem alterar o provedor de IA da empresa."
            )
    else:
        uid = dados.usuario_id or x_user_id
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identificação do usuário é obrigatória."
            )
        validar_perfil_admin_ou_master(uid)

    resultado = atualizar_configuracao_ia(
        empresa_id=dados.empresa_id,
        provedor_ia_padrao=dados.provedor_ia_padrao,
        openai_api_key=dados.openai_api_key,
        gemini_api_key=dados.gemini_api_key
    )

    return {
        "mensagem": "Configurações de IA Multi-Vendor atualizadas com sucesso!",
        "configuracao": resultado
    }


@router.post("/fuso-horario")
def salvar_fuso_horario_empresa(
    dados: FusoHorarioRequest,
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """
    Configura o fuso horário global da empresa (ex: 'America/Sao_Paulo', 'America/Manaus', 'auto').
    Exige perfil admin ou master.
    """
    if dados.usuario_perfil:
        if dados.usuario_perfil.strip().lower() not in ("admin", "master"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas administradores podem alterar o fuso horário global da empresa."
            )
    else:
        uid = dados.usuario_id or x_user_id
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identificação do usuário é obrigatória para alterar o fuso horário."
            )
        validar_perfil_admin_ou_master(uid)

    resultado = atualizar_fuso_horario(
        empresa_id=dados.empresa_id,
        fuso_horario=dados.fuso_horario
    )

    return {
        "mensagem": f"Fuso horário global atualizado para {dados.fuso_horario} com sucesso!",
        "configuracao": resultado
    }


@router.post("/telegram-bot")
def salvar_telegram_bot_empresa(
    dados: TelegramBotConfigRequest,
    request: Request = None,
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """
    Configura o bot do Telegram dedicado para a empresa:
    - Valida o token com a API do Telegram (getMe)
    - Recupera o @username e nome do bot
    - Registra automaticamente o webhook no Telegram para {base_url}/telegram/webhook/{empresa_id}
    - Salva na tabela configuracoes_empresa
    """
    if dados.usuario_perfil:
        if dados.usuario_perfil.strip().lower() not in ("admin", "master"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas administradores podem configurar o bot do Telegram da empresa."
            )
    else:
        uid = dados.usuario_id or x_user_id
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identificação do usuário é obrigatória para configurar o bot do Telegram."
            )
        validar_perfil_admin_ou_master(uid)

    token = (dados.telegram_bot_token or "").strip()
    if not token:
        # Busca token antigo para desconectar webhook no Telegram se existente
        try:
            cfg_antiga = obter_configuracao_empresa(dados.empresa_id)
            token_antigo = cfg_antiga.get("telegram_bot_token")
            if token_antigo:
                requests.post(f"https://api.telegram.org/bot{token_antigo}/deleteWebhook", timeout=5)
        except Exception as e:
            logger.warning(f"[TelegramBot] Aviso ao remover webhook no Telegram para empresa {dados.empresa_id}: {e}")

        # Remoção do bot dedicado (retorna para o bot compartilhado/padrão)
        resultado = atualizar_telegram_bot_empresa(
            empresa_id=dados.empresa_id,
            telegram_bot_token=None,
            telegram_bot_username=None,
            telegram_webhook_ativo=False
        )
        return {
            "sucesso": True,
            "mensagem": "Bot dedicado desconectado com sucesso. A empresa não possui mais bot próprio ativo.",
            "configuracao": resultado
        }

    # 1. Validação prévia de Unicidade: impede reutilização de token cadastrado em outra empresa
    conflito_token = buscar_empresa_por_token_telegram(token, excluir_empresa_id=dados.empresa_id)
    if conflito_token:
        nome_outra = conflito_token.get("empresa_nome") or f"Empresa #{conflito_token.get('empresa_id')}"
        id_outra = conflito_token.get("empresa_id")
        user_outra = conflito_token.get("telegram_bot_username")
        detalhe_user = f" (@{user_outra})" if user_outra else ""
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Este token do Telegram{detalhe_user} já está cadastrado na empresa '{nome_outra}' (ID #{id_outra}). "
                f"O sistema não aceita tokens duplicados. Cada empresa deve possuir seu próprio bot exclusivo. "
                f"Para utilizá-lo nesta empresa, primeiro desconecte o bot na empresa original."
            )
        )

    # 2. Validação do Token junto à API oficial do Telegram
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        res_json = resp.json()
        if not res_json.get("ok"):
            desc = res_json.get("description", "Token inválido")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Token do Telegram rejeitado pelo Telegram: {desc}"
            )
        bot_info = res_json.get("result", {})
        bot_username = bot_info.get("username", "")
        bot_first_name = bot_info.get("first_name", "")
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro de conexão com o Telegram: {e}"
        )

    # 3. Validação de Unicidade por Username: garante que o mesmo @username de bot não seja vinculado a empresas distintas
    if bot_username:
        conflito_user = buscar_empresa_por_bot_username_telegram(bot_username, excluir_empresa_id=dados.empresa_id)
        if conflito_user:
            nome_outra = conflito_user.get("empresa_nome") or f"Empresa #{conflito_user.get('empresa_id')}"
            id_outra = conflito_user.get("empresa_id")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"O bot @{bot_username} já está cadastrado para a empresa '{nome_outra}' (ID #{id_outra}). "
                    f"Cada empresa deve possuir seu próprio bot exclusivo. "
                    f"Para utilizá-lo nesta empresa, primeiro desconecte o bot na empresa original."
                )
            )

    # Determina a URL base pública para o webhook
    base_url = (
        dados.url_webhook_base or 
        os.getenv("RENDER_EXTERNAL_URL") or 
        (str(request.base_url).rstrip("/") if request else None) or
        "http://localhost:8000"
    ).rstrip("/")
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = f"https://{base_url}"

    webhook_url = f"{base_url}/telegram/webhook/{dados.empresa_id}"
    webhook_ativo = False
    webhook_detalhes = None

    # Tenta registrar o webhook oficial no Telegram para esta empresa
    try:
        set_resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": webhook_url},
            timeout=10
        )
        set_json = set_resp.json()
        if set_json.get("ok"):
            webhook_ativo = True
            webhook_detalhes = set_json.get("description", "Webhook registrado com sucesso")
        else:
            logger.warning(f"[TelegramBot] Aviso ao registrar webhook: {set_json}")
            webhook_detalhes = set_json.get("description")
    except Exception as e:
        logger.warning(f"[TelegramBot] Erro ao registrar webhook para empresa {dados.empresa_id}: {e}")
        webhook_detalhes = str(e)

    # Registra comandos padrão do bot
    try:
        comandos = [
            {"command": "start", "description": "Iniciar atendimento com a IA"},
            {"command": "ajuda", "description": "Instruções de como utilizar o assistente"},
            {"command": "suporte", "description": "Contato da equipe humana de suporte"}
        ]
        requests.post(f"https://api.telegram.org/bot{token}/setMyCommands", json={"commands": comandos}, timeout=5)
    except Exception as e:
        logger.warning(f"[TelegramBot] Falha ao registrar comandos padrão do bot: {e}")

    # Atualiza banco de dados com tratamento de erro
    try:
        resultado = atualizar_telegram_bot_empresa(
            empresa_id=dados.empresa_id,
            telegram_bot_token=token,
            telegram_bot_username=bot_username,
            telegram_webhook_ativo=webhook_ativo
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )

    return {
        "sucesso": True,
        "mensagem": f"Bot @{bot_username} ({bot_first_name}) conectado com sucesso para a empresa!",
        "bot_username": bot_username,
        "bot_first_name": bot_first_name,
        "webhook_url": webhook_url,
        "webhook_ativo": webhook_ativo,
        "webhook_detalhes": webhook_detalhes,
        "configuracao": resultado
    }


@router.post("/ia-regras")
def configurar_ia_regras(dados: IARegrasRequest, request: Request = None):
    """Configura o comportamento e as regras de orquestração do Agente de IA."""
    uid = dados.usuario_id or (int(request.headers.get("X-User-Id")) if request and request.headers.get("X-User-Id") and request.headers.get("X-User-Id").isdigit() else None)
    if uid:
        validar_perfil_admin_ou_master(uid)
    res = salvar_configuracao_ia_regras(
        empresa_id=dados.empresa_id,
        ia_prompt_sistema=dados.ia_prompt_sistema,
        ia_coletar_dados_obrigatorio=dados.ia_coletar_dados_obrigatorio
    )
    return {"sucesso": True, "mensagem": "Regras do Agente de IA salvas com sucesso!", "configuracao": res}


@router.post("/followup-config")
def configurar_followup(dados: FollowupConfigRequest, request: Request = None):
    """Configura o agendador e os critérios de reengajamento automático (Follow-up)."""
    uid = dados.usuario_id or (int(request.headers.get("X-User-Id")) if request and request.headers.get("X-User-Id") and request.headers.get("X-User-Id").isdigit() else None)
    if uid:
        validar_perfil_admin_ou_master(uid)
    res = salvar_configuracao_followup(
        empresa_id=dados.empresa_id,
        followup_ativo=dados.followup_ativo,
        followup_horas_inatividade=dados.followup_horas_inatividade,
        followup_mensagem_personalizada=dados.followup_mensagem_personalizada
    )
    return {"sucesso": True, "mensagem": "Configurações do Agendador de Follow-up salvas com sucesso!", "configuracao": res}


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


