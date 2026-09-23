import logging
from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)


def salvar_interacao(telegram_chat_id: str, mensagem_usuario: str, resposta_ia: str) -> int | None:
    """
    Grava uma interação (pergunta do usuário e resposta da IA) no histórico de conversas.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            INSERT INTO historico_conversas (telegram_chat_id, mensagem_usuario, resposta_ia)
            VALUES ({ph}, {ph}, {ph})
        """, (str(telegram_chat_id), mensagem_usuario, resposta_ia))
        conexao.commit()

        # lastrowid no SQLite; fallback para Postgres
        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            return cursor.lastrowid

        cursor.execute(f"""
            SELECT id FROM historico_conversas
            WHERE telegram_chat_id = {ph}
            ORDER BY id DESC
            LIMIT 1
        """, (str(telegram_chat_id),))
        row = cursor.fetchone()
        return row["id"] if row else None
    except Exception as e:
        conexao.rollback()
        logger.error(f"[HistoryService] Erro ao salvar histórico de conversa: {e}")
        return None
    finally:
        conexao.close()


def obter_ultimas_interacoes(telegram_chat_id: str, limite: int = 3) -> list[dict]:
    """
    Recupera as últimas interações de um determinado chat_id no Telegram,
    retornando em ordem cronológica (da mais antiga para a mais recente).
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT mensagem_usuario, resposta_ia, data_interacao
            FROM historico_conversas
            WHERE telegram_chat_id = {ph}
            ORDER BY id DESC
            LIMIT {ph}
        """, (str(telegram_chat_id), limite))
        linhas = cursor.fetchall()

        interacoes = [
            {
                "mensagem_usuario": linha["mensagem_usuario"],
                "resposta_ia": linha["resposta_ia"],
                "data_interacao": str(linha["data_interacao"])
            }
            for linha in reversed(linhas)
        ]
        return interacoes
    except Exception as e:
        logger.error(f"[HistoryService] Erro ao recuperar histórico: {e}")
        return []
    finally:
        conexao.close()
