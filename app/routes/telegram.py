from fastapi import APIRouter, Request, HTTPException, Query, Body, Header, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import requests
import os
import logging
import json
import math
import re

from app.services.ai_service import AIService
from app.services.embedding_service import gerar_embedding
from app.services.history_service import salvar_interacao, obter_ultimas_interacoes, marcar_interacoes_como_lidas
from app.services.company_service import obter_configuracao_empresa
from app.database.database import conectar, _cursor, _placeholder
from app.routes.analytics import registrar_pergunta_historico

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["Telegram"])

ai_service = AIService()

# Ordem estrita dos estágios comerciais para impedir regressão automática
ORDEM_ESTAGIOS = {
    "novo": 1,
    "qualificado": 2,
    "proposta": 3,
    "negociacao": 4,
    "fechado": 5,
    "perdido": 6
}


def calcular_similaridade_cosseno(vec1, vec2):
    """Calcula a similaridade de cosseno entre dois vetores de embeddings."""
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = math.sqrt(sum(a * a for a in vec1))
    magnitude2 = math.sqrt(sum(b * b for b in vec2))
    if not magnitude1 or not magnitude2:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)


def buscar_contexto_relevante(pergunta: str, empresa_id: int = 1, top_k: int = 3) -> tuple[str, list]:
    """
    RAG Público Obrigatório para o Bot do Telegram:
    Força estritamente perfil 'cliente', garantindo que apenas documentos
    com nivel_acesso = 'publico' sejam pesquisados. Nunca expõe dados internos.
    Retorna (contexto_texto, lista_documentos_usados).
    """
    try:
        embedding_pergunta = gerar_embedding(pergunta)

        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()

        # Filtro estrito: apenas nível público da respectiva empresa
        cursor.execute(f"""
            SELECT c.conteudo, c.embedding, d.nome_arquivo
            FROM chunks c
            JOIN documentos d ON c.documento_id = d.id
            WHERE d.empresa_id = {ph} AND COALESCE(d.nivel_acesso, 'publico') = 'publico'
        """, (empresa_id,))

        linhas = cursor.fetchall()
        conexao.close()

        if not linhas:
            return "", []

        resultados = []
        for linha in linhas:
            conteudo = linha["conteudo"]
            nome_arq = linha["nome_arquivo"]
            embedding_chunk = json.loads(linha["embedding"])

            similaridade = calcular_similaridade_cosseno(embedding_pergunta, embedding_chunk)
            resultados.append((similaridade, conteudo, nome_arq))

        resultados.sort(key=lambda x: x[0], reverse=True)

        melhores = [item for item in resultados[:top_k] if item[0] > 0.25]
        contexto_formatado = "\n\n".join(item[1] for item in melhores)

        documentos_utilizados = [
            {"nome_arquivo": item[2], "similaridade": round(float(item[0]), 3)}
            for item in melhores
        ]

        return contexto_formatado, documentos_utilizados

    except Exception as e:
        logger.error(f"[Telegram RAG Error] Falha ao buscar contexto: {e}")
        return "", []


def configurar_comandos_bot_telegram():
    """Registra comandos oficiais do Telegram (/start, /ajuda, /suporte)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    comandos = [
        {"command": "start", "description": "Iniciar atendimento com a IA"},
        {"command": "ajuda", "description": "Instruções de como utilizar o assistente"},
        {"command": "suporte", "description": "Contato da equipe humana de suporte"}
    ]
    try:
        requests.post(url, json={"commands": comandos}, timeout=5)
    except Exception as e:
        logger.warning(f"[Telegram] Falha ao configurar comandos no bot: {e}")


def enviar_mensagem_telegram(chat_id: str, texto: str):
    """Envia uma mensagem de resposta via API do Telegram com fallback para texto puro."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning(f"[Telegram] TELEGRAM_BOT_TOKEN não configurado. Mensagem para {chat_id}: {texto}")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 400 and "parse" in response.text.lower():
            payload.pop("parse_mode")
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"[Telegram] Erro ao enviar mensagem para chat {chat_id}: {e}")


def buscar_nome_telegram_api(chat_id: str) -> str | None:
    """Consulta os dados do chat na API do Telegram para obter o nome real do usuário."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/getChat"
        resp = requests.get(url, params={"chat_id": chat_id}, timeout=4)
        if resp.status_code == 200:
            res = resp.json().get("result", {})
            first = str(res.get("first_name") or "").strip()
            last = str(res.get("last_name") or "").strip()
            username = str(res.get("username") or "").strip()
            full = " ".join([p for p in [first, last] if p])
            if full:
                return full
            if username:
                return f"@{username}"
    except Exception as e:
        logger.debug(f"[Telegram] Falha ao consultar getChat({chat_id}): {e}")
    return None


def classificar_estagio_e_produto(pergunta: str, resposta: str, produtos: list[dict], estagio_atual: str) -> dict:
    """
    Chamada adicional e leve ao Gemini para qualificação do lead no CRM.
    Classifica o estágio da conversa e tenta identificar o produto provável.
    """
    try:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"estagio": estagio_atual, "produto_provavel": None, "houve_proposta": False}

        client = genai.Client(api_key=api_key)
        nomes_produtos = [p["nome"] for p in produtos]

        prompt = f"""
        Você é um classificador de CRM para funil de vendas.
        Analise a interação entre cliente e assistente virtual:
        Mensagem do Cliente: "{pergunta}"
        Resposta do Assistente: "{resposta}"
        Estágio Atual do Lead: "{estagio_atual}"
        Produtos Disponíveis: {json.dumps(nomes_produtos, ensure_ascii=False)}

        Classifique a interação e retorne ESTRITAMENTE um JSON com as chaves:
        {{
          "estagio": "novo" | "qualificado" | "proposta" | "negociacao" | "fechado",
          "produto_provavel": "Nome exato de um dos produtos acima ou null",
          "houve_proposta": true | false
        }}

        Critérios de Estágio:
        - "novo": primeiro contato ou dúvida inicial genérica.
        - "qualificado": demonstrou interesse claro em produto, escopo, benefícios ou tirou dúvida técnica específica.
        - "proposta": perguntou sobre preços, valores, formas de contratação ou pediu orçamento/proposta comercial.
        - "negociacao": tirando dúvidas sobre fechamento, contratos, formas de pagamento, prazos de entrega.
        - "fechado": confirmou contratação, pediu formalização do contrato ou aceite.
        """

        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt
        )
        texto = resp.text.strip() if resp and resp.text else ""
        texto_limpo = re.sub(r"```json\s*|\s*```", "", texto).strip()
        dados = json.loads(texto_limpo)
        return {
            "estagio": dados.get("estagio", estagio_atual),
            "produto_provavel": dados.get("produto_provavel"),
            "houve_proposta": bool(dados.get("houve_proposta", False))
        }
    except Exception as e:
        logger.warning(f"[Classificador CRM] Falha ao classificar estágio: {e}")
        return {"estagio": estagio_atual, "produto_provavel": None, "houve_proposta": False}


# ============================================================================
# CONTEXTO E SALVAGUARDAS DE CONTATO
# ============================================================================

def eh_cortesia_ou_agradecimento(texto: str) -> bool:
    """Identifica se o texto é apenas uma cortesia, agradecimento ou saudação, evitando falsos cadastros de nome."""
    t = texto.strip().lower()
    cortesias = {
        "obrigado", "muito obrigado", "muito obrigada", "obrigada", "valeu", "valeu!", "valeu mesmo",
        "agradeço", "agradeco", "grato", "grata", "brigado", "brigada", "show", "show de bola", "beleza", "blz",
        "perfeito", "perfeita", "ótimo", "otimo", "ótima", "otima", "maravilha", "ok", "certo", "entendi",
        "tá bom", "ta bom", "tá bem", "ta bem", "tudo bem", "olá", "ola", "oi", "oie",
        "bom dia", "boa tarde", "boa noite", "sim", "não", "nao", "claro", "combinado", "falou",
        "tchau", "até mais", "ate mais", "abraço", "abraco"
    }
    t_sem_pontuacao = re.sub(r'[^\w\s]', '', t).strip()
    if t_sem_pontuacao in cortesias or t in cortesias:
        return True
    if re.search(r'^(muito\s+)?obrigad[oa]|^(muito\s+)?grato|valeu(\s+demais)?|valeu\s+mesmo', t_sem_pontuacao):
        return True
    return False


def eh_pergunta_ou_duvida(texto: str) -> bool:
    """Verifica se o texto parece ser uma pergunta ou solicitação, não um nome."""
    t = texto.strip().lower()
    if "?" in texto:
        return True
    padroes = [
        r'^(como|qual|quais|quando|onde|quem|por que|porque|quanto|quantos|quantas)\b',
        r'\b(saber|tirar dúvida|duvida|gostaria de|preciso de|me ajuda|ajudar|cadastrar|cadastro|matrícula|matricula|preço|preco|plano|serviço|servico)\b'
    ]
    for p in padroes:
        if re.search(p, t):
            return True
    return False


def extrair_dados_contato(texto: str) -> dict:
    """
    Extrai e-mail, telefone e nome explícito com salvaguardas rigorosas contra falsos positivos.
    """
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', texto)
    phone_match = re.search(r'(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?(?:9\s?)?\d{4,5}[-\s]?\d{4}', texto)

    novo_email = email_match.group(0).lower() if email_match else None
    novo_telefone = phone_match.group(0).strip() if phone_match else None

    # Procura por prefixos explícitos de nome
    match_nome_explicito = re.search(r'(?i)(?:meu nome [eé]|sou o|sou a|me chamo|nome:?)\s+([A-Za-zÀ-ÿ\s]{2,40})', texto)
    nome_extraido = None
    if match_nome_explicito:
        cand = match_nome_explicito.group(1).strip()
        if len(cand) >= 2 and not eh_cortesia_ou_agradecimento(cand) and not eh_pergunta_ou_duvida(cand):
            nome_extraido = cand
    elif novo_email or novo_telefone:
        # Se veio e-mail ou telefone, verifica se sobrou um nome válido
        texto_sem = texto
        if email_match:
            texto_sem = texto_sem.replace(email_match.group(0), "")
        if phone_match:
            texto_sem = texto_sem.replace(phone_match.group(0), "")
        texto_sem = re.sub(r'(?i)(e-mail:?|email:?|telefone:?|tel:?|cel:?|whatsapp:?)', '', texto_sem)
        texto_sem = re.sub(r'[,;\n\r\t]+', ' ', texto_sem).strip()
        palavras = texto_sem.split()
        if 1 <= len(palavras) <= 4 and len(texto_sem) >= 2 and not eh_cortesia_ou_agradecimento(texto_sem) and not eh_pergunta_ou_duvida(texto_sem):
            nome_extraido = texto_sem

    return {
        "nome": nome_extraido,
        "email": novo_email,
        "telefone": novo_telefone,
        "tem_contato": bool(nome_extraido or novo_email or novo_telefone)
    }


# ============================================================================
# WEBHOOK DO TELEGRAM
# ============================================================================

@router.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook público do Telegram:
    - Sem exigência de e-mail (atendimento aberto e autônomo).
    - Checa se o chat está em modo 'humano' antes de chamar a IA.
    - Se 'bot', consulta base pública (RAG), responde via Gemini, atualiza histórico,
      cria/evolui cliente e negócio no CRM e loga analytics com observabilidade integral.
    """
    try:
        dados = await request.json()
    except Exception:
        return {"status": "error", "message": "JSON inválido"}

    mensagem = dados.get("message") or dados.get("edited_message")
    if not mensagem or "text" not in mensagem:
        return {"status": "ok"}

    chat_id = str(mensagem["chat"]["id"])
    texto_recebido = str(mensagem.get("text", "")).strip()
    empresa_id = 1

    # Extração de nome real do usuário do Telegram (from ou chat)
    remetente = mensagem.get("from") or {}
    chat_info = mensagem.get("chat") or {}
    first_name = str(remetente.get("first_name") or chat_info.get("first_name") or "").strip()
    last_name = str(remetente.get("last_name") or chat_info.get("last_name") or "").strip()
    username = str(remetente.get("username") or chat_info.get("username") or "").strip()

    partes_nome = [p for p in [first_name, last_name] if p]
    if partes_nome:
        nome_lead = " ".join(partes_nome)
    elif username:
        nome_lead = f"@{username}"
    else:
        nome_api = buscar_nome_telegram_api(chat_id)
        nome_lead = nome_api if nome_api else f"Usuário #{chat_id[-4:] if len(chat_id) >= 4 else chat_id}"

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        # 0. Garante que o registro do cliente exista no CRM com o nome real
        cursor.execute(f"SELECT id, nome, email, telefone, aguardando_contato FROM clientes WHERE telegram_chat_id = {ph}", (chat_id,))
        cliente = cursor.fetchone()
        if not cliente:
            cursor.execute(f"""
                INSERT INTO clientes (empresa_id, nome, telegram_chat_id, origem, aguardando_contato)
                VALUES ({ph}, {ph}, {ph}, 'telegram', {ph})
            """, (empresa_id, nome_lead, chat_id, False))
            conexao.commit()
            cursor.execute(f"SELECT id, nome, email, telefone, aguardando_contato FROM clientes WHERE telegram_chat_id = {ph}", (chat_id,))
            cliente = cursor.fetchone()
        else:
            nome_atual = (cliente.get("nome") or "").strip()
            eh_generico = (
                not nome_atual or
                nome_atual.startswith("Lead ") or
                nome_atual.startswith("Usuário ") or
                nome_atual.startswith("Lead Telegram") or
                chat_id in nome_atual or
                "?" in nome_atual or
                nome_atual.lower() in ("muito obrigado", "obrigado", "valeu")
            )
            # Atualiza no banco se o nome atual for genérico e tivermos um nome real capturado
            if eh_generico and not nome_lead.startswith("Usuário #") and not nome_lead.startswith("Lead Telegram"):
                cursor.execute(f"UPDATE clientes SET nome = {ph} WHERE id = {ph}", (nome_lead, cliente["id"]))
                conexao.commit()
                cliente["nome"] = nome_lead

        cliente_id = cliente["id"]

        # 1. Checa atribuição de atendente humano (Bot vs Humano)
        cursor.execute(f"SELECT status, atendente_id FROM conversas_telegram WHERE chat_id = {ph}", (chat_id,))
        conv_status = cursor.fetchone()
        if conv_status and conv_status.get("status") == "humano":
            # Modo humano ativo: não dispara IA nem resposta automática, apenas registra
            # Também desativa qualquer estado pendente de aguardando contato
            cursor.execute(f"UPDATE clientes SET aguardando_contato = {ph} WHERE id = {ph}", (False, cliente_id))
            cursor.execute(f"""
                INSERT INTO historico_conversas (telegram_chat_id, mensagem_usuario, resposta_ia, lida)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (chat_id, texto_recebido, "", False))
            conexao.commit()
            return {"status": "ok", "modo": "humano", "mensagem": "Mensagem salva para o atendente"}

        # 2. Tratamento de Comandos Básicos
        comando = texto_recebido.strip().lower()
        if comando in ["/start", "start"]:
            primeiro_nome = first_name or (cliente.get("nome") if cliente and not str(cliente.get("nome")).startswith("Usuário #") else "")
            saudacao = f", {primeiro_nome}" if primeiro_nome and not primeiro_nome.startswith("@") else ""
            msg_start = (
                f"👋 Olá{saudacao}! Seja bem-vindo ao atendimento inteligente da **FluxIA**.\n\n"
                "Como posso te ajudar hoje? Fique à vontade para me perguntar sobre nossos produtos, planos e serviços!\n\n"
                "📌 **Comandos úteis:**\n"
                "• `/ajuda` - Como usar o assistente\n"
                "• `/suporte` - Falar com um consultor humano"
            )
            enviar_mensagem_telegram(chat_id, msg_start)
            salvar_interacao(telegram_chat_id=chat_id, mensagem_usuario=texto_recebido, resposta_ia=msg_start, lida=False)
            return {"status": "ok"}

        if comando in ["/ajuda", "/help", "ajuda", "help"]:
            msg_ajuda = (
                "📖 **Guia do Assistente Virtual FluxIA**\n\n"
                "• Envie suas dúvidas em linguagem natural.\n"
                "• Nossas respostas são fundamentadas nas informações e diretrizes oficiais da empresa.\n"
                "• Se precisar falar com um atendente humano, use o comando `/suporte`."
            )
            enviar_mensagem_telegram(chat_id, msg_ajuda)
            salvar_interacao(telegram_chat_id=chat_id, mensagem_usuario=texto_recebido, resposta_ia=msg_ajuda, lida=False)
            return {"status": "ok"}

        if comando in ["/suporte", "/support", "suporte", "support"]:
            config_suporte = obter_configuracao_empresa(empresa_id=empresa_id)
            telefone = config_suporte.get("numero_suporte_humano", "(11) 99999-9999")
            orientacao = config_suporte.get("mensagem_suporte", "Entre em contato com nossa equipe.")
            msg_suporte = f"📞 **Suporte e Atendimento:**\n\n{orientacao}\n📱 Contato: `{telefone}`"
            enviar_mensagem_telegram(chat_id, msg_suporte)
            salvar_interacao(telegram_chat_id=chat_id, mensagem_usuario=texto_recebido, resposta_ia=msg_suporte, lida=False)
            return {"status": "ok"}

        # 3. Estado 'aguardando dados de contato' com entendimento de contexto
        if cliente.get("aguardando_contato"):
            # A) Se o usuário enviou uma cortesia ou agradecimento (ex: "Muito obrigado", "Valeu")
            # NÃO altera o nome do lead! Apenas responde gentilmente e encerra o estado de espera.
            if eh_cortesia_ou_agradecimento(texto_recebido):
                cursor.execute(f"UPDATE clientes SET aguardando_contato = {ph} WHERE id = {ph}", (False, cliente_id))
                conexao.commit()
                msg_agradecimento = (
                    "De nada! Fico sempre à sua disposição. Se precisar de mais alguma informação ou desejar falar com um consultor humano, "
                    "basta me chamar ou usar o comando `/suporte`! 😊"
                )
                enviar_mensagem_telegram(chat_id, msg_agradecimento)
                salvar_interacao(telegram_chat_id=chat_id, mensagem_usuario=texto_recebido, resposta_ia=msg_agradecimento, lida=False)
                return {"status": "ok", "mensagem": "Agradecimento respondido com cortesia"}

            # B) Se o usuário enviou dados reais de contato (email, telefone ou nome explícito)
            dados_contato = extrair_dados_contato(texto_recebido)
            if dados_contato["tem_contato"]:
                novo_email = dados_contato["email"] or cliente.get("email")
                novo_telefone = dados_contato["telefone"] or cliente.get("telefone")
                nome_final = dados_contato["nome"] or cliente.get("nome") or nome_lead

                cursor.execute(f"""
                    UPDATE clientes
                    SET nome = {ph}, email = {ph}, telefone = {ph}, aguardando_contato = {ph}
                    WHERE id = {ph}
                """, (nome_final, novo_email, novo_telefone, False, cliente_id))
                conexao.commit()

                msg_confirmacao = (
                    f"Perfeito, {nome_final}! Anotei seus dados de contato com sucesso "
                    f"(E-mail: {novo_email or 'não informado'} | Telefone: {novo_telefone or 'não informado'}).\n\n"
                    "Nossa equipe de atendimento foi acionada e entrará em contato em breve para te auxiliar melhor! "
                    "Se precisar de mais informações sobre nossos serviços, estou à sua disposição."
                )
                enviar_mensagem_telegram(chat_id, msg_confirmacao)
                salvar_interacao(telegram_chat_id=chat_id, mensagem_usuario=texto_recebido, resposta_ia=msg_confirmacao, lida=False)
                return {"status": "ok", "mensagem": "Dados de contato salvos com sucesso"}
            else:
                # C) Se for uma pergunta ou comentário normal sem dados de contato,
                # apenas desativa o aguardo de contato e prossegue para responder a dúvida normalmente com IA!
                cursor.execute(f"UPDATE clientes SET aguardando_contato = {ph} WHERE id = {ph}", (False, cliente_id))
                conexao.commit()

        # 4. RAG: Busca estritamente pública
        historico_recente = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=3)
        config_suporte = obter_configuracao_empresa(empresa_id=empresa_id)

        contexto_publico, docs_usados = buscar_contexto_relevante(
            pergunta=texto_recebido,
            empresa_id=empresa_id
        )

        tem_contexto = bool(contexto_publico and len(contexto_publico.strip()) > 10)

        # 5. Geração de resposta com LangChain Multi-Vendor + Observabilidade
        telemetria_id = 0
        try:
            from app.services.ai_engine_service import ai_engine
            resposta_ia, telemetria_id = ai_engine.gerar_resposta_orquestrada(
                pergunta=texto_recebido,
                contexto=contexto_publico,
                historico=historico_recente,
                config_suporte=config_suporte,
                empresa_id=empresa_id,
                canal="telegram",
                session_id=chat_id
            )
        except Exception as e:
            logger.warning(f"[Telegram] Falha no ai_engine, usando fallback direto: {e}")
            resposta_ia = ai_service.gerar_resposta(
                pergunta=texto_recebido,
                contexto=contexto_publico,
                historico=historico_recente,
                config_suporte=config_suporte,
                empresa_id=empresa_id
            )

        # Se não houver contexto na base pública e o lead não tiver dados de contato,
        # orienta cordialmente sobre a possibilidade de deixar contato para um atendente
        tem_nome_cadastrado = cliente.get("nome") and not str(cliente["nome"]).startswith("Lead") and not str(cliente["nome"]).startswith("Usuário #")
        tem_email_cadastrado = bool(cliente.get("email") and cliente["email"].strip())
        tem_tel_cadastrado = bool(cliente.get("telefone") and cliente["telefone"].strip())

        if not tem_contexto and not (tem_nome_cadastrado and tem_email_cadastrado and tem_tel_cadastrado):
            if "contato" not in resposta_ia.lower() and "telefone" not in resposta_ia.lower():
                resposta_ia += "\n\n💡 *Caso deseje que um atendente fale diretamente com você, pode me informar seu e-mail ou telefone!*"
                cursor.execute(f"UPDATE clientes SET aguardando_contato = {ph} WHERE id = {ph}", (True, cliente_id))
                conexao.commit()

        # Dispara continuous evaluation em background (latência zero para o cliente)
        if telemetria_id:
            from app.services.ai_evaluation_service import avaliar_interacao_ia
            background_tasks.add_task(
                avaliar_interacao_ia,
                telemetria_id=telemetria_id,
                empresa_id=empresa_id,
                pergunta=texto_recebido,
                resposta=resposta_ia,
                contexto_utilizado=contexto_publico if tem_contexto else None
            )

        # 6. Envia resposta ao Telegram
        enviar_mensagem_telegram(chat_id, resposta_ia)

        # 7. Salva no histórico de conversas
        salvar_interacao(
            telegram_chat_id=chat_id,
            mensagem_usuario=texto_recebido,
            resposta_ia=resposta_ia,
            lida=False
        )

        # 8. Log no Analytics (Perguntas Histórico)
        registrar_pergunta_historico(
            canal="telegram",
            empresa_id=empresa_id,
            pergunta=texto_recebido,
            resposta=resposta_ia,
            telegram_chat_id=chat_id,
            documentos_utilizados=docs_usados,
            teve_contexto=tem_contexto,
            fonte_resposta="base_conhecimento"
        )

        # B. Busca ou cria negócio em andamento para este cliente
        cursor.execute(f"""
            SELECT id, estagio, produto_id, valor_estimado, proposta_enviada
            FROM negocios
            WHERE cliente_id = {ph} AND estagio NOT IN ('fechado', 'perdido')
            ORDER BY id DESC LIMIT 1
        """, (cliente_id,))
        negocio = cursor.fetchone()

        if not negocio:
            cursor.execute(f"""
                INSERT INTO negocios (empresa_id, cliente_id, estagio)
                VALUES ({ph}, {ph}, 'novo')
            """, (empresa_id, cliente_id))
            if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
                negocio_id = cursor.lastrowid
            else:
                cursor.execute(f"SELECT id FROM negocios WHERE cliente_id = {ph} ORDER BY id DESC LIMIT 1", (cliente_id,))
                negocio_id = cursor.fetchone()["id"]
            estagio_atual = "novo"
            produto_atual_id = None
        else:
            negocio_id = negocio["id"]
            estagio_atual = negocio["estagio"]
            produto_atual_id = negocio["produto_id"]

        # C. Qualificação Inteligente via Gemini
        cursor.execute(f"SELECT id, nome FROM produtos WHERE empresa_id = {ph} AND ativo = {ph}", (empresa_id, True))
        produtos_empresa = [dict(p) for p in cursor.fetchall()]

        classificacao = classificar_estagio_e_produto(
            pergunta=texto_recebido,
            resposta=resposta_ia,
            produtos=produtos_empresa,
            estagio_atual=estagio_atual
        )

        novo_estagio = classificacao.get("estagio", estagio_atual)
        produto_provavel_nome = classificacao.get("produto_provavel")
        houve_proposta = classificacao.get("houve_proposta", False)

        # Regra de ouro: Só AVANÇAR estágio no funil, nunca retroceder
        ordem_atual = ORDEM_ESTAGIOS.get(estagio_atual, 1)
        ordem_nova = ORDEM_ESTAGIOS.get(novo_estagio, 1)
        estagio_final = novo_estagio if ordem_nova > ordem_atual else estagio_atual

        # Identifica se produto_provavel corresponde a um produto cadastrado
        novo_produto_id = produto_atual_id
        if produto_provavel_nome:
            for p in produtos_empresa:
                if p["nome"].lower() in produto_provavel_nome.lower() or produto_provavel_nome.lower() in p["nome"].lower():
                    novo_produto_id = p["id"]
                    break

        # Atualiza o negócio
        cursor.execute(f"""
            UPDATE negocios
            SET estagio = {ph},
                produto_id = COALESCE({ph}, produto_id),
                proposta_enviada = CASE WHEN {ph} THEN TRUE ELSE proposta_enviada END,
                ultima_interacao_em = CURRENT_TIMESTAMP
            WHERE id = {ph}
        """, (estagio_final, novo_produto_id, houve_proposta, negocio_id))

        # D. Se o estágio virou 'fechado', gera automaticamente registro em 'contratos'
        if estagio_final == "fechado":
            val_est = (negocio["valor_estimado"] if negocio and negocio.get("valor_estimado") else 0.0)
            cursor.execute(f"""
                INSERT INTO contratos (negocio_id, status, valor_contrato)
                VALUES ({ph}, 'pendente', {ph})
                ON CONFLICT (negocio_id) DO NOTHING
            """, (negocio_id, val_est))

        conexao.commit()
        return {"status": "ok"}

    except Exception as e:
        conexao.rollback()
        logger.error(f"[Webhook Telegram] Falha no processamento: {e}")
        return {"status": "error", "detalhe": str(e)}
    finally:
        conexao.close()


# ============================================================================
# CONFIGURAÇÃO E STATUS REAL DO WEBHOOK
# ============================================================================

class WebhookConfigRequest(BaseModel):
    url: Optional[str] = None
    usuario_id: Optional[int] = None


@router.post("/configurar-webhook")
def configurar_webhook_telegram(dados: WebhookConfigRequest = Body(...), request: Request = None):
    """
    Registra a URL do webhook no Telegram oficial.
    Monta a URL usando RENDER_EXTERNAL_URL ou parâmetro informado.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN não configurado no .env.")

    # Prioridade de URL: 1. Informada no body | 2. RENDER_EXTERNAL_URL | 3. Base URL da requisição
    base_url = dados.url or os.getenv("RENDER_EXTERNAL_URL")
    if not base_url and request:
        base_url = str(request.base_url).rstrip("/")

    if not base_url:
        raise HTTPException(status_code=400, detail="Não foi possível determinar a URL pública da aplicação.")

    base_url = base_url.rstrip("/")
    # Garante https se não for localhost
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = f"https://{base_url}"

    webhook_url = f"{base_url}/telegram/webhook"
    telegram_api_url = f"https://api.telegram.org/bot{token}/setWebhook"

    try:
        resp = requests.post(telegram_api_url, json={"url": webhook_url}, timeout=10)
        resultado = resp.json()
        if not resultado.get("ok"):
            raise HTTPException(status_code=400, detail=f"Erro retornado pelo Telegram: {resultado.get('description')}")

        return {
            "sucesso": True,
            "url_registrada": webhook_url,
            "telegram_resposta": resultado
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Falha de conexão com a API do Telegram: {e}")


@router.get("/status-webhook")
def obter_status_webhook():
    """Consulta o status real do webhook diretamente na API do Telegram (getWebhookInfo)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return {
            "ativo": False,
            "configurado": False,
            "mensagem": "TELEGRAM_BOT_TOKEN não configurado no servidor."
        }

    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=8)
        dados = resp.json()
        if not dados.get("ok"):
            return {
                "ativo": False,
                "configurado": False,
                "erro": dados.get("description", "Erro desconhecido")
            }

        resultado = dados.get("result", {})
        url_registrada = resultado.get("url", "")
        tem_url = bool(url_registrada and url_registrada.strip())
        ultimo_erro = resultado.get("last_error_message")
        pendentes = resultado.get("pending_update_count", 0)

        return {
            "ativo": tem_url and (not ultimo_erro or pendentes == 0),
            "configurado": tem_url,
            "url_registrada": url_registrada,
            "pendentes": pendentes,
            "ultimo_erro": ultimo_erro,
            "ultima_sincronizacao": resultado.get("last_synchronization_error_date")
        }
    except Exception as e:
        logger.error(f"[Telegram] Erro ao consultar getWebhookInfo: {e}")
        return {
            "ativo": False,
            "configurado": False,
            "erro": str(e)
        }


# ============================================================================
# ATRIBUIÇÃO DE ATENDENTE HUMANO (BOT VS HUMANO)
# ============================================================================

class AssumirAtendimentoRequest(BaseModel):
    atendente_id: int


@router.post("/conversas/{chat_id}/assumir")
def assumir_conversa_atendente(chat_id: str, dados: AssumirAtendimentoRequest):
    """Passa o atendimento para o modo 'humano' e vincula o atendente responsável."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # UPSERT compatível com SQLite e Postgres
        cursor.execute(f"SELECT chat_id FROM conversas_telegram WHERE chat_id = {ph}", (str(chat_id),))
        existe = cursor.fetchone()

        if existe:
            cursor.execute(f"""
                UPDATE conversas_telegram
                SET status = 'humano', atendente_id = {ph}, atualizado_em = CURRENT_TIMESTAMP
                WHERE chat_id = {ph}
            """, (dados.atendente_id, str(chat_id)))
        else:
            cursor.execute(f"""
                INSERT INTO conversas_telegram (chat_id, status, atendente_id)
                VALUES ({ph}, 'humano', {ph})
            """, (str(chat_id), dados.atendente_id))

        conexao.commit()
        return {"sucesso": True, "status": "humano", "atendente_id": dados.atendente_id}
    finally:
        conexao.close()


@router.post("/conversas/{chat_id}/devolver")
def devolver_conversa_ao_bot(chat_id: str):
    """Devolve a conversa para o modo 'bot' (IA autônoma)."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            UPDATE conversas_telegram
            SET status = 'bot', atendente_id = NULL, atualizado_em = CURRENT_TIMESTAMP
            WHERE chat_id = {ph}
        """, (str(chat_id),))

        conexao.commit()
        return {"sucesso": True, "status": "bot"}
    finally:
        conexao.close()


# ============================================================================
# GESTÃO DE CONVERSAS NO CRM
# ============================================================================

class EnviarMensagemOperadorRequest(BaseModel):
    texto: str


@router.get("/conversas")
def listar_conversas_telegram(empresa_id: int | None = None):
    """
    Lista contatos e conversas do Telegram com status de atendimento (Bot vs Humano)
    e identificação do atendente responsável.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        query = f"""
            SELECT 
                h.telegram_chat_id,
                MAX(h.id) AS ultimo_id,
                MAX(h.data_interacao) AS data_ultima_mensagem,
                COUNT(h.id) AS total_interacoes,
                cli.nome AS cliente_nome,
                cli.telefone AS cliente_telefone,
                COALESCE(ct.status, 'bot') AS status_atendimento,
                ct.atendente_id,
                u_atend.nome AS nome_atendente
            FROM historico_conversas h
            LEFT JOIN clientes cli ON cli.telegram_chat_id = h.telegram_chat_id
            LEFT JOIN conversas_telegram ct ON ct.chat_id = h.telegram_chat_id
            LEFT JOIN usuarios u_atend ON u_atend.id = ct.atendente_id
            GROUP BY h.telegram_chat_id, cli.nome, cli.telefone, ct.status, ct.atendente_id, u_atend.nome
            ORDER BY data_ultima_mensagem DESC
        """
        cursor.execute(query)
        linhas = cursor.fetchall()

        # Consulta mensagens não lidas agrupadas por chat_id
        cursor.execute(f"""
            SELECT telegram_chat_id, COUNT(*) AS nao_lidas
            FROM historico_conversas
            WHERE (lida = {ph} OR lida IS NULL) AND mensagem_usuario != ''
            GROUP BY telegram_chat_id
        """, (False,))
        nao_lidas_map = {r["telegram_chat_id"]: r["nao_lidas"] for r in cursor.fetchall()}

        conversas = []
        for l in linhas:
            chat_id = l["telegram_chat_id"]

            # Busca última mensagem
            cursor.execute(f"""
                SELECT mensagem_usuario, resposta_ia, data_interacao
                FROM historico_conversas
                WHERE id = {ph}
            """, (l["ultimo_id"],))
            ultima_row = cursor.fetchone()
            ultima_msg = ""
            if ultima_row:
                ultima_msg = ultima_row["mensagem_usuario"] or ultima_row["resposta_ia"] or ""

            # PARTE 19: Retornar nome do cliente cadastrado ou resolver via Telegram API se genérico
            nome_cliente = l["cliente_nome"]
            eh_generico = (
                not nome_cliente or 
                str(nome_cliente).startswith("Lead ") or 
                str(nome_cliente).startswith("Usuário ") or
                str(nome_cliente).startswith("Lead Telegram") or
                chat_id in str(nome_cliente)
            )
            if eh_generico:
                nome_api = buscar_nome_telegram_api(chat_id)
                if nome_api:
                    nome_cliente = nome_api
                    try:
                        cursor.execute(f"UPDATE clientes SET nome = {ph} WHERE telegram_chat_id = {ph}", (nome_api, chat_id))
                        conexao.commit()
                    except Exception:
                        pass
                else:
                    nome_cliente = f"Usuário #{chat_id[-4:] if len(chat_id) >= 4 else chat_id}"

            nome_exibicao = nome_cliente
            qtd_nao_lidas = nao_lidas_map.get(chat_id, 0)

            # Formata timestamp como UTC ISO explícito
            dt_raw = str(l["data_ultima_mensagem"] or "")
            if dt_raw and "T" not in dt_raw:
                dt_iso = dt_raw.replace(" ", "T") + "Z"
            elif dt_raw and not dt_raw.endswith("Z") and "+" not in dt_raw:
                dt_iso = dt_raw + "Z"
            else:
                dt_iso = dt_raw

            conversas.append({
                "chat_id": chat_id,
                "nome": nome_exibicao,
                "telefone": l["cliente_telefone"] or "",
                "status_atendimento": l["status_atendimento"],
                "atendente_id": l["atendente_id"],
                "nome_atendente": l["nome_atendente"] or ("Assistente IA" if l["status_atendimento"] == "bot" else "Atendente"),
                "total_mensagens": l["total_interacoes"],
                "nao_lidas": qtd_nao_lidas,
                "ultima_mensagem": ultima_msg,
                "data_ultima_mensagem": dt_iso
            })

        total_nao_lidas = sum(c["nao_lidas"] for c in conversas)
        return {
            "total": len(conversas),
            "total_nao_lidas": total_nao_lidas,
            "conversas": conversas
        }
    except Exception as e:
        logger.error(f"[Telegram] Erro ao listar conversas: {e}")
        return {"total": 0, "total_nao_lidas": 0, "conversas": []}
    finally:
        conexao.close()


@router.patch("/conversas/{chat_id}/marcar-lida")
def marcar_conversa_lida(chat_id: str):
    """
    PARTE 20: Marca todas as mensagens desta conversa como lidas pelo atendente.
    """
    qtd = marcar_interacoes_como_lidas(str(chat_id))
    return {
        "sucesso": True,
        "chat_id": chat_id,
        "mensagens_marcadas": qtd
    }


@router.get("/conversas/{chat_id}/mensagens")
def obter_mensagens_conversa(chat_id: str):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT id, mensagem_usuario, resposta_ia, data_interacao
            FROM historico_conversas
            WHERE telegram_chat_id = {ph}
            ORDER BY id ASC
        """, (str(chat_id),))
        linhas = cursor.fetchall()

        mensagens = []
        for row in linhas:
            data_str = str(row["data_interacao"] or "")
            if data_str and "T" not in data_str:
                data_iso = data_str.replace(" ", "T") + "Z"
            elif data_str and not data_str.endswith("Z") and "+" not in data_str:
                data_iso = data_str + "Z"
            else:
                data_iso = data_str

            hora_formatada = data_str[11:16] if len(data_str) >= 16 else ""

            if row["mensagem_usuario"]:
                mensagens.append({
                    "id": f"{row['id']}_user",
                    "remetente": "usuario",
                    "texto": row["mensagem_usuario"],
                    "hora": hora_formatada,
                    "data_hora": data_iso
                })
            if row["resposta_ia"]:
                mensagens.append({
                    "id": f"{row['id']}_bot",
                    "remetente": "bot",
                    "texto": row["resposta_ia"],
                    "hora": hora_formatada,
                    "data_hora": data_iso
                })

        # Consulta também o status atual desta conversa
        cursor.execute(f"""
            SELECT ct.status, ct.atendente_id, u.nome AS nome_atendente
            FROM conversas_telegram ct
            LEFT JOIN usuarios u ON u.id = ct.atendente_id
            WHERE ct.chat_id = {ph}
        """, (str(chat_id),))
        status_info = cursor.fetchone()

        return {
            "chat_id": chat_id,
            "status_atendimento": status_info["status"] if status_info else "bot",
            "atendente_id": status_info["atendente_id"] if status_info else None,
            "nome_atendente": status_info["nome_atendente"] if status_info else "Assistente IA",
            "total": len(mensagens),
            "mensagens": mensagens
        }
    except Exception as e:
        logger.error(f"[Telegram] Erro ao obter mensagens: {e}")
        return {"chat_id": chat_id, "total": 0, "mensagens": []}
    finally:
        conexao.close()


@router.post("/conversas/{chat_id}/enviar")
def enviar_resposta_operador(chat_id: str, dados: EnviarMensagemOperadorRequest):
    texto = dados.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="O texto da mensagem não pode estar vazio.")

    enviar_mensagem_telegram(chat_id, texto)

    salvar_interacao(
        telegram_chat_id=chat_id,
        mensagem_usuario="",
        resposta_ia=f"[Operador]: {texto}"
    )

    return {
        "sucesso": True,
        "mensagem": "Mensagem enviada com sucesso ao Telegram do cliente!"
    }