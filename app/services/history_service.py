import logging
from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)


def salvar_interacao(telegram_chat_id: str, mensagem_usuario: str, resposta_ia: str, lida: bool = False, empresa_id: int = 1) -> int | None:
    """
    Grava uma interação (pergunta do usuário e resposta da IA) no histórico de conversas com escopo de empresa.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            INSERT INTO historico_conversas (telegram_chat_id, mensagem_usuario, resposta_ia, lida, empresa_id)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
        """, (str(telegram_chat_id), mensagem_usuario, resposta_ia, lida, empresa_id))
        row = cursor.fetchone()
        conexao.commit()
        return row["id"] if row else None
    except Exception as e:
        conexao.rollback()
        logger.error(f"[HistoryService] Erro ao salvar histórico de conversa: {e}")
        return None
    finally:
        conexao.close()


def marcar_interacoes_como_lidas(telegram_chat_id: str) -> int:
    """
    Marca todas as mensagens de um determinado chat_id como lidas pelo atendente.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            UPDATE historico_conversas
            SET lida = {ph}
            WHERE telegram_chat_id = {ph} AND (lida = {ph} OR lida IS NULL)
        """, (True, str(telegram_chat_id), False))
        afetados = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
        conexao.commit()
        return afetados
    except Exception as e:
        conexao.rollback()
        logger.error(f"[HistoryService] Erro ao marcar mensagens como lidas para {telegram_chat_id}: {e}")
        return 0
    finally:
        conexao.close()


def obter_ultimas_interacoes(telegram_chat_id: str, limite: int = 3, empresa_id: int | None = None) -> list[dict]:
    """
    Recupera as últimas interações de um determinado chat_id no Telegram,
    retornando em ordem cronológica (da mais antiga para a mais recente) filtrada por empresa.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        if empresa_id:
            cursor.execute(f"""
                SELECT mensagem_usuario, resposta_ia, data_interacao
                FROM historico_conversas
                WHERE telegram_chat_id = {ph} AND (empresa_id = {ph} OR empresa_id IS NULL)
                ORDER BY id DESC
                LIMIT {ph}
            """, (str(telegram_chat_id), empresa_id, limite))
        else:
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
