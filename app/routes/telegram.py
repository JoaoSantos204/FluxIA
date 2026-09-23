from fastapi import APIRouter, Request
import requests
import os
import logging
import json
import math

from app.services.ai_service import AIService
from app.services.user_service import buscar_usuario_por_telegram, vincular_telegram_chat_id
from app.services.embedding_service import gerar_embedding
from app.services.history_service import salvar_interacao, obter_ultimas_interacoes
from app.services.company_service import obter_configuracao_empresa
from app.database.database import conectar, _cursor, _placeholder

# Configura logs para monitorar no terminal
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["Telegram"])

# Instância única do serviço de IA
ai_service = AIService()


def calcular_similaridade_cosseno(vec1, vec2):
    """Calcula a similaridade de cosseno entre dois vetores de embeddings."""
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = math.sqrt(sum(a * a for a in vec1))
    magnitude2 = math.sqrt(sum(b * b for b in vec2))
    if not magnitude1 or not magnitude2:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)


def buscar_contexto_relevante(pergunta: str, perfil_usuario: str = "cliente", empresa_id: int = 1, top_k: int = 3) -> str:
    """
    Gera o embedding da pergunta e busca os chunks mais similares no banco.
    Aplica filtro por Empresa (Multi-tenancy) e por RBAC:
    - 'admin' e 'funcionario' acessam todos os documentos da sua empresa.
    - 'cliente' acessa estritamente documentos da sua empresa com nivel_acesso = 'publico'.
    """
    try:
        # 1. Gera o embedding da pergunta feita no Telegram
        embedding_pergunta = gerar_embedding(pergunta)

        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()

        # 2. Busca chunks no banco aplicando filtro de empresa e perfil
        if perfil_usuario in ["admin", "funcionario"]:
            cursor.execute(f"""
                SELECT c.conteudo, c.embedding
                FROM chunks c
                JOIN documentos d ON c.documento_id = d.id
                WHERE d.empresa_id = {ph}
            """, (empresa_id,))
        else:
            cursor.execute(f"""
                SELECT c.conteudo, c.embedding
                FROM chunks c
                JOIN documentos d ON c.documento_id = d.id
                WHERE d.empresa_id = {ph} AND COALESCE(d.nivel_acesso, 'publico') = 'publico'
            """, (empresa_id,))

        linhas = cursor.fetchall()
        conexao.close()

        if not linhas:
            return ""

        resultados = []
        for linha in linhas:
            conteudo = linha["conteudo"]
            embedding_chunk = json.loads(linha["embedding"])

            # 3. Compara a pergunta com o chunk
            similaridade = calcular_similaridade_cosseno(embedding_pergunta, embedding_chunk)
            resultados.append((similaridade, conteudo))

        # 4. Ordena do chunk mais similar para o menos similar
        resultados.sort(key=lambda x: x[0], reverse=True)

        # Filtra os top_k com um corte mínimo de relevância (ex: > 0.25)
        melhores_chunks = [item[1] for item in resultados[:top_k] if item[0] > 0.25]

        return "\n\n".join(melhores_chunks)

    except Exception as e:
        logger.error(f"[RAG Error] Falha ao buscar contexto: {e}")
        return ""



def configurar_comandos_bot_telegram():
    """Registra a lista de comandos no menu oficial do Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    comandos = [
        {"command": "start", "description": "Iniciar o assistente"},
        {"command": "ajuda", "description": "Como usar o assistente"},
        {"command": "perfil", "description": "Meu perfil e nível de acesso"},
        {"command": "suporte", "description": "Canais de suporte humano"}
    ]
    try:
        requests.post(url, json={"commands": comandos}, timeout=10)
    except Exception as e:
        logger.error(f"[Telegram] Erro ao registrar comandos no bot: {e}")


def enviar_mensagem_telegram(chat_id: str, texto: str):
    """Obtém o token atualizado e envia a mensagem com tratamento de erros."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("[Telegram] TELEGRAM_BOT_TOKEN não configurado no .env")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        # Se falhar por erro de sintaxe do Markdown, tenta reenviar como texto puro
        if response.status_code == 400 and "parse" in response.text.lower():
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"[Telegram] Erro ao enviar mensagem para o chat {chat_id}: {e}")


@router.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        dados = await request.json()
    except Exception:
        return {"status": "error", "message": "JSON inválido"}

    # Ignora atualizações que não sejam mensagens de texto
    if "message" not in dados or "text" not in dados["message"]:
        return {"status": "ok"}

    mensagem = dados["message"]
    chat_id = str(mensagem["chat"]["id"])
    texto_recebido = str(mensagem.get("text", "")).strip()

    # 1. Verifica se o chat_id já está vinculado a um usuário ativo no banco
    usuario = buscar_usuario_por_telegram(chat_id)

    # --- FLUXO PARA USUÁRIO NÃO AUTENTICADO ---
    if not usuario:
        if "@" not in texto_recebido:
            enviar_mensagem_telegram(
                chat_id,
                "👋 Olá! Bem-vindo ao assistente virtual da **FluxIA**.\n\n"
                "Para acessar o sistema, por favor **envie o seu e-mail corporativo** cadastrado."
            )
            return {"status": "ok"}

        sucesso = vincular_telegram_chat_id(email=texto_recebido.lower(), telegram_chat_id=chat_id)

        if sucesso:
            enviar_mensagem_telegram(
                chat_id,
                "✅ **Conta vinculada com sucesso!**\nAgora você pode fazer perguntas sobre a base de conhecimento da sua empresa."
            )
        else:
            enviar_mensagem_telegram(
                chat_id,
                "❌ **E-mail não encontrado ou inativo.**\nSolicite ao seu Administrador o cadastro na plataforma antes de continuar."
            )
        return {"status": "ok"}

    # --- COMANDOS RÁPIDOS PARA USUÁRIOS AUTENTICADOS ---
    comando = texto_recebido.strip().lower()

    if comando in ["/start", "start"]:
        msg_start = (
            f"Olá, **{usuario['nome']}**! Como posso ajudar você hoje?\n\n"
            "💡 Você pode me fazer qualquer pergunta sobre nossos documentos e procedimentos corporativos.\n\n"
            "📌 **Comandos úteis:**\n"
            "• `/ajuda` - Como usar o assistente\n"
            "• `/perfil` - Seu perfil e permissões de acesso\n"
            "• `/suporte` - Falar com o atendimento humano"
        )
        enviar_mensagem_telegram(chat_id, msg_start)
        return {"status": "ok"}

    if comando in ["/ajuda", "/help", "ajuda", "help"]:
        msg_ajuda = (
            "📖 **Guia de Uso do Assistente FluxIA**\n\n"
            "1. **Perguntas diretas:** Envie suas dúvidas em linguagem natural como se estivesse conversando com um colega.\n"
            "2. **Base de Conhecimento:** Minhas respostas são estritamente fundamentadas nos documentos oficiais da empresa.\n"
            "3. **Suporte Humano:** Caso precise de ajuda especializada ou o assunto não conste nos documentos, posso te encaminhar para nossa equipe.\n\n"
            "📌 **Comandos disponíveis:**\n"
            "• `/perfil` - Consulta seu nível de permissão e dados da conta\n"
            "• `/suporte` - Contato de suporte da empresa\n"
            "• `/start` - Reinicia a apresentação do assistente"
        )
        enviar_mensagem_telegram(chat_id, msg_ajuda)
        return {"status": "ok"}

    if comando in ["/suporte", "/support", "suporte", "support"]:
        config_suporte = obter_configuracao_empresa(empresa_id=usuario.get("empresa_id", 1))
        telefone = config_suporte.get("numero_suporte_humano", "(11) 99999-9999")
        orientacao = config_suporte.get("mensagem_suporte", "Por favor, entre em contato com nossa equipe de atendimento.")

        msg_suporte = (
            "📞 **Canais de Suporte Humano**\n\n"
            f"{orientacao}\n\n"
            f"📱 **Telefone / WhatsApp:** `{telefone}`"
        )
        enviar_mensagem_telegram(chat_id, msg_suporte)
        return {"status": "ok"}

    if comando in ["/perfil", "/status", "/me", "perfil"]:
        perfil_desc = {
            "admin": "👑 **Administrador:** Acesso total à base de conhecimento (documentos públicos e internos) e gestão do sistema.",
            "funcionario": "💼 **Funcionário:** Acesso à base de conhecimento completa (documentos públicos e processos internos).",
            "cliente": "👤 **Cliente:** Acesso restrito a informações e documentos públicos (preços, suporte básico, horários)."
        }
        descricao = perfil_desc.get(usuario.get("perfil", "cliente"), "Acesso padrão do sistema.")

        msg_perfil = (
            "👤 **Seu Perfil no FluxIA**\n\n"
            f"• **Nome:** {usuario['nome']}\n"
            f"• **E-mail:** `{usuario['email']}`\n"
            f"• **Nível de Acesso:** `{usuario.get('perfil', 'cliente').upper()}`\n"
            f"• **Status:** `{usuario.get('status', 'ativo')}`\n\n"
            f"ℹ️ {descricao}"
        )
        enviar_mensagem_telegram(chat_id, msg_perfil)
        return {"status": "ok"}

    perfil = usuario.get("perfil", "cliente")
    empresa_id = usuario.get("empresa_id", 1)

    # 2. Recupera o histórico recente de conversas (memória das últimas 3 interações)
    historico_recente = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=3)

    # 3. Recupera as configurações de suporte humano da empresa
    config_suporte = obter_configuracao_empresa(empresa_id=empresa_id)

    # 4. Busca os trechos mais relevantes no SQLite respeitando o RBAC e o isolamento de empresa
    contexto_real = buscar_contexto_relevante(
        pergunta=texto_recebido,
        perfil_usuario=perfil,
        empresa_id=empresa_id
    )

    # 5. Envia para a IA responder com o contexto extraído, histórico e suporte humano
    resposta_ia = ai_service.gerar_resposta(
        pergunta=texto_recebido,
        contexto=contexto_real,
        historico=historico_recente,
        config_suporte=config_suporte
    )

    # 6. Envia a resposta final para o chat do Telegram
    enviar_mensagem_telegram(chat_id, resposta_ia)

    # 7. Grava a interação no histórico para alimentar a memória futura
    salvar_interacao(
        telegram_chat_id=chat_id,
        mensagem_usuario=texto_recebido,
        resposta_ia=resposta_ia
    )

    return {"status": "ok"}