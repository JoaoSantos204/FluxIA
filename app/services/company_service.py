import logging
from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)

CONFIGURACAO_PADRAO = {
    "empresa_id": 1,
    "numero_suporte_humano": "(11) 99999-9999",
    "mensagem_suporte": "Por favor, entre em contato com nossa equipe de atendimento."
}


def obter_configuracao_empresa(empresa_id: int = 1) -> dict:
    """
    Busca as configurações da empresa (como telefone e mensagem de suporte humano).
    Caso não exista configuração cadastrada, retorna os valores padrão do sistema.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT empresa_id, numero_suporte_humano, mensagem_suporte
            FROM configuracoes_empresa
            WHERE empresa_id = {ph}
            ORDER BY id ASC
            LIMIT 1
        """, (empresa_id,))
        linha = cursor.fetchone()

        if linha:
            return {
                "empresa_id": linha["empresa_id"],
                "numero_suporte_humano": linha["numero_suporte_humano"] or CONFIGURACAO_PADRAO["numero_suporte_humano"],
                "mensagem_suporte": linha["mensagem_suporte"] or CONFIGURACAO_PADRAO["mensagem_suporte"]
            }

        return CONFIGURACAO_PADRAO.copy()
    except Exception as e:
        logger.error(f"[CompanyService] Erro ao buscar configurações da empresa {empresa_id}: {e}")
        return CONFIGURACAO_PADRAO.copy()
    finally:
        conexao.close()


def atualizar_configuracao_empresa(empresa_id: int, numero_suporte_humano: str, mensagem_suporte: str) -> dict:
    """
    Atualiza ou insere as configurações de suporte humano da empresa.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"SELECT id FROM configuracoes_empresa WHERE empresa_id = {ph}", (empresa_id,))
        existente = cursor.fetchone()

        if existente:
            cursor.execute(f"""
                UPDATE configuracoes_empresa
                SET numero_suporte_humano = {ph}, mensagem_suporte = {ph}
                WHERE empresa_id = {ph}
            """, (numero_suporte_humano, mensagem_suporte, empresa_id))
        else:
            cursor.execute(f"""
                INSERT INTO configuracoes_empresa (empresa_id, numero_suporte_humano, mensagem_suporte)
                VALUES ({ph}, {ph}, {ph})
            """, (empresa_id, numero_suporte_humano, mensagem_suporte))

        conexao.commit()
        return {
            "empresa_id": empresa_id,
            "numero_suporte_humano": numero_suporte_humano,
            "mensagem_suporte": mensagem_suporte
        }
    except Exception as e:
        conexao.rollback()
        logger.error(f"[CompanyService] Erro ao atualizar configurações da empresa {empresa_id}: {e}")
        raise e
    finally:
        conexao.close()
