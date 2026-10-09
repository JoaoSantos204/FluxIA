import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.database.database import conectar, _cursor, _placeholder
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


def gerar_mensagem_followup(
    cliente_nome: str,
    produto_nome: Optional[str],
    estagio: str,
    modelo_custom: Optional[str] = None,
    diretriz_ia: Optional[str] = None
) -> str:
    """
    Gera mensagem de reengajamento contextualizada.
    Se modelo_custom contiver placeholders {nome}, {produto}, {estagio}, realiza interpolação direta.
    Caso contrário, gera via IA considerando as diretrizes configuradas.
    """
    prod_nome = produto_nome or "nossos produtos e soluções"
    est_nome = estagio or "em andamento"

    if modelo_custom and modelo_custom.strip():
        txt = modelo_custom.strip()
        if "{nome}" in txt or "{produto}" in txt or "{estagio}" in txt:
            return txt.replace("{nome}", cliente_nome).replace("{produto}", prod_nome).replace("{estagio}", est_nome)

    try:
        from google import genai
        import os
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            client = genai.Client(api_key=api_key)
            prompt_base = diretriz_ia or "Você é um consultor comercial proativo e atencioso da empresa."
            prompt = (
                f"{prompt_base}\n"
                f"Gere uma mensagem curta (máximo 2 a 3 frases), amigável, humanizada e não invasiva de follow-up para reengajar o cliente {cliente_nome}. "
                f"Ele está no estágio comercial '{est_nome}' referente ao produto '{prod_nome}'. "
                f"Em Português do Brasil. Sem saudações robóticas. "
                f"Pergunte educadamente se restou alguma dúvida ou se gostaria de avançar."
            )
            resp = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt
            )
            texto = resp.text.strip() if resp and resp.text else ""
            if texto:
                return texto
    except Exception as e:
        logger.warning(f"[Followup] Falha ao gerar texto personalizado com IA: {e}")

    prod_txt = f" sobre {produto_nome}" if produto_nome else ""
    return (
        f"Olá, {cliente_nome}! Passando para saber se ficou alguma dúvida pendente{prod_txt}. "
        f"Nossa equipe está à disposição para ajudar você a avançar! Posso ajudar em algo mais?"
    )


def executar_followup_automatico(
    horas_inatividade: Optional[int] = None,
    empresa_id: Optional[int] = None,
    forcar: bool = False
) -> dict:
    """
    Localiza leads e negócios com inatividade comercial superior ao limite configurado
    e envia mensagem automática de aquecimento/reengajamento via Telegram,
    respeitando as regras e modelos configurados por cada empresa.
    """
    from app.routes.telegram import enviar_mensagem_telegram
    from app.services.company_service import obter_configuracao_empresa

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    total_processados = 0
    enviados = []

    try:
        filtros = [
            "n.estagio NOT IN ('fechado', 'perdido')",
            "c.telegram_chat_id IS NOT NULL",
            "c.telegram_chat_id != ''"
        ]
        params = []
        if empresa_id:
            filtros.append(f"n.empresa_id = {ph}")
            params.append(empresa_id)

        where_sql = " AND ".join(filtros)
        cursor.execute(f"""
            SELECT 
                n.id AS negocio_id, n.empresa_id, n.estagio, n.valor_estimado,
                n.ultima_interacao_em, n.ultimo_followup_em,
                c.id AS cliente_id, c.nome AS cliente_nome, c.telegram_chat_id,
                p.nome AS produto_nome
            FROM negocios n
            JOIN clientes c ON n.cliente_id = c.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            WHERE {where_sql}
        """, tuple(params))

        negocios = cursor.fetchall()
        agora = datetime.now()

        # Cache de configurações por empresa
        cache_configs = {}

        for neg in negocios:
            chat_id = neg.get("telegram_chat_id")
            emp_id = neg.get("empresa_id") or 1
            if not chat_id:
                continue

            if emp_id not in cache_configs:
                cache_configs[emp_id] = obter_configuracao_empresa(emp_id)
            cfg = cache_configs[emp_id]

            # Se follow-up estiver desativado para a empresa e não for execução forçada/teste
            if not forcar and not cfg.get("followup_ativo", True):
                continue

            limite_horas = horas_inatividade if horas_inatividade is not None else cfg.get("followup_horas_inatividade", 24)
            if forcar:
                limite_horas = 0

            # Parsing tolerante de data da última interação
            data_interacao = neg.get("ultima_interacao_em")
            if isinstance(data_interacao, str):
                try:
                    data_interacao = datetime.fromisoformat(data_interacao.replace("Z", ""))
                except Exception:
                    data_interacao = None

            if data_interacao and not forcar:
                diff_interacao = (agora - data_interacao.replace(tzinfo=None)).total_seconds() / 3600.0
                if diff_interacao < limite_horas:
                    continue

            # Checa se já houve follow-up recente
            data_followup = neg.get("ultimo_followup_em")
            if data_followup and not forcar:
                if isinstance(data_followup, str):
                    try:
                        data_followup = datetime.fromisoformat(data_followup.replace("Z", ""))
                    except Exception:
                        pass
                if isinstance(data_followup, datetime):
                    diff_follow = (agora - data_followup.replace(tzinfo=None)).total_seconds() / 3600.0
                    if diff_follow < limite_horas:
                        continue

            # Gera a mensagem de follow-up personalizada da empresa
            cliente_nome = neg.get("cliente_nome") or "Cliente"
            produto_nome = neg.get("produto_nome")
            estagio = neg.get("estagio") or "novo"

            modelo_msg = cfg.get("followup_mensagem_personalizada")
            prompt_dir = cfg.get("ia_prompt_sistema")

            msg_reengajamento = gerar_mensagem_followup(
                cliente_nome=cliente_nome,
                produto_nome=produto_nome,
                estagio=estagio,
                modelo_custom=modelo_msg,
                diretriz_ia=prompt_dir
            )

            # Envia pelo Telegram da respectiva empresa
            enviar_mensagem_telegram(chat_id, f"👋 {msg_reengajamento}", empresa_id=emp_id)

            # Registra no histórico de conversas da respectiva empresa
            cursor.execute(f"""
                INSERT INTO historico_conversas (telegram_chat_id, mensagem_usuario, resposta_ia, empresa_id)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (chat_id, "[Follow-up automático do sistema]", f"[Follow-up automático]: {msg_reengajamento}", emp_id))

            # Atualiza último follow-up do negócio
            neg_id = neg["negocio_id"]
            cursor.execute(f"""
                UPDATE negocios SET ultimo_followup_em = CURRENT_TIMESTAMP WHERE id = {ph}
            """, (neg_id,))

            total_processados += 1
            enviados.append({
                "negocio_id": neg_id,
                "empresa_id": emp_id,
                "cliente": cliente_nome,
                "chat_id": chat_id,
                "mensagem": msg_reengajamento
            })

        conexao.commit()
        logger.info(f"[Follow-up] Processamento concluído. Total de mensagens enviadas: {total_processados}")
        return {
            "status": "sucesso",
            "total_enviados": total_processados,
            "detalhes": enviados
        }

    except Exception as e:
        conexao.rollback()
        logger.error(f"[Follow-up] Erro durante a execução automática: {e}")
        return {"status": "erro", "detalhe": str(e)}
    finally:
        conexao.close()
