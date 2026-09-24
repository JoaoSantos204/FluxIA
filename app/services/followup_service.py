import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.database.database import conectar, _cursor, _placeholder
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


def gerar_mensagem_followup(cliente_nome: str, produto_nome: Optional[str], estagio: str) -> str:
    """Gera mensagem de reengajamento contextualizada via Gemini."""
    try:
        from google import genai
        import os
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        prod_desc = f"referente a '{produto_nome}'" if produto_nome else "sobre os serviços da empresa"
        prompt = (
            f"Você é um consultor comercial proativo da FluxIA. "
            f"Gere uma mensagem curta, amigável, humanizada e não invasiva de follow-up para reengajar o cliente {cliente_nome}, "
            f"cujo atendimento está no estágio comercial '{estagio}' {prod_desc}. "
            f"Diretrizes: Máximo de 2 a 3 frases. Em Português do Brasil. Sem saudações robóticas. "
            f"Pergunte educadamente se ainda possui alguma dúvida ou se gostaria de dar andamento."
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
        f"Olá! Passando para saber se ficou alguma dúvida pendente{prod_txt}. "
        f"Nossa equipe está à disposição para ajudar você a avançar! Posso ajudar em algo mais?"
    )


def executar_followup_automatico(horas_inatividade: int = 24) -> dict:
    """
    Localiza leads e negócios com inatividade comercial superior ao limite configurado
    e envia mensagem automática de aquecimento/reengajamento via Telegram.
    """
    from app.routes.telegram import enviar_mensagem_telegram

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    total_processados = 0
    enviados = []

    try:
        cursor.execute("""
            SELECT 
                n.id AS negocio_id, n.empresa_id, n.estagio, n.valor_estimado,
                n.ultima_interacao_em, n.ultimo_followup_em,
                c.id AS cliente_id, c.nome AS cliente_nome, c.telegram_chat_id,
                p.nome AS produto_nome
            FROM negocios n
            JOIN clientes c ON n.cliente_id = c.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            WHERE n.estagio NOT IN ('fechado', 'perdido')
              AND c.telegram_chat_id IS NOT NULL
        """)

        negocios = cursor.fetchall()
        agora = datetime.now()

        for neg in negocios:
            chat_id = neg.get("telegram_chat_id")
            if not chat_id:
                continue

            # Parsing tolerante de data da última interação
            data_interacao = neg.get("ultima_interacao_em")
            if isinstance(data_interacao, str):
                try:
                    data_interacao = datetime.fromisoformat(data_interacao.replace("Z", ""))
                except Exception:
                    continue

            if not data_interacao:
                continue

            # Diferença de horas
            diff_interacao = (agora - data_interacao.replace(tzinfo=None)).total_seconds() / 3600.0
            if diff_interacao < horas_inatividade:
                continue

            # Checa se já houve follow-up recente
            data_followup = neg.get("ultimo_followup_em")
            if data_followup:
                if isinstance(data_followup, str):
                    try:
                        data_followup = datetime.fromisoformat(data_followup.replace("Z", ""))
                    except Exception:
                        pass
                if isinstance(data_followup, datetime):
                    diff_follow = (agora - data_followup.replace(tzinfo=None)).total_seconds() / 3600.0
                    if diff_follow < horas_inatividade:
                        continue

            # Gera a mensagem de follow-up
            cliente_nome = neg.get("cliente_nome") or "Cliente"
            produto_nome = neg.get("produto_nome")
            estagio = neg.get("estagio") or "novo"

            msg_reengajamento = gerar_mensagem_followup(cliente_nome, produto_nome, estagio)

            # Envia pelo Telegram
            enviar_mensagem_telegram(chat_id, f"👋 {msg_reengajamento}")

            # Registra no histórico de conversas
            cursor.execute(f"""
                INSERT INTO historico_conversas (telegram_chat_id, mensagem_usuario, resposta_ia)
                VALUES ({ph}, {ph}, {ph})
            """, (chat_id, "[Follow-up automático do sistema]", f"[Follow-up automático]: {msg_reengajamento}"))

            # Atualiza último follow-up do negócio
            neg_id = neg["negocio_id"]
            cursor.execute(f"""
                UPDATE negocios SET ultimo_followup_em = CURRENT_TIMESTAMP WHERE id = {ph}
            """, (neg_id,))

            total_processados += 1
            enviados.append({
                "negocio_id": neg_id,
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
