import logging
from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)

CONFIGURACAO_PADRAO = {
    "empresa_id": 1,
    "numero_suporte_humano": "(11) 99999-9999",
    "mensagem_suporte": "Por favor, entre em contato com nossa equipe de atendimento.",
    "gemini_api_key": None
}


def mascarar_api_key(chave: str | None) -> str:
    """Mascara a chave de API para exibição segura na interface (ex: AIzaSy...****)."""
    if not chave or not chave.strip():
        return ""
    chave_limpa = chave.strip()
    if len(chave_limpa) <= 8:
        return "********"
    return f"{chave_limpa[:6]}...{chave_limpa[-4:]}"


def obter_configuracao_empresa(empresa_id: int = 1) -> dict:
    """
    Busca as configurações da empresa (como telefone, mensagem de suporte e chave BYOK).
    Caso não exista configuração cadastrada, retorna os valores padrão do sistema.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT empresa_id, numero_suporte_humano, mensagem_suporte, gemini_api_key
            FROM configuracoes_empresa
            WHERE empresa_id = {ph}
            ORDER BY id ASC
            LIMIT 1
        """, (empresa_id,))
        linha = cursor.fetchone()

        if linha:
            chave_real = linha["gemini_api_key"] if "gemini_api_key" in linha.keys() else None
            return {
                "empresa_id": linha["empresa_id"],
                "numero_suporte_humano": linha["numero_suporte_humano"] or CONFIGURACAO_PADRAO["numero_suporte_humano"],
                "mensagem_suporte": linha["mensagem_suporte"] or CONFIGURACAO_PADRAO["mensagem_suporte"],
                "gemini_api_key": chave_real,
                "gemini_api_key_mascarada": mascarar_api_key(chave_real),
                "possui_chave_propria": bool(chave_real and chave_real.strip())
            }

        padrao = CONFIGURACAO_PADRAO.copy()
        padrao["gemini_api_key_mascarada"] = ""
        padrao["possui_chave_propria"] = False
        return padrao
    except Exception as e:
        logger.error(f"[CompanyService] Erro ao buscar configurações da empresa {empresa_id}: {e}")
        padrao = CONFIGURACAO_PADRAO.copy()
        padrao["gemini_api_key_mascarada"] = ""
        padrao["possui_chave_propria"] = False
        return padrao
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


def atualizar_gemini_api_key(empresa_id: int, gemini_api_key: str | None) -> dict:
    """
    Atualiza ou cadastra a chave Google Gemini própria (BYOK) da empresa.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    chave_limpa = gemini_api_key.strip() if gemini_api_key and gemini_api_key.strip() else None

    try:
        cursor.execute(f"SELECT id FROM configuracoes_empresa WHERE empresa_id = {ph}", (empresa_id,))
        existente = cursor.fetchone()

        if existente:
            cursor.execute(f"""
                UPDATE configuracoes_empresa
                SET gemini_api_key = {ph}
                WHERE empresa_id = {ph}
            """, (chave_limpa, empresa_id))
        else:
            cursor.execute(f"""
                INSERT INTO configuracoes_empresa (empresa_id, gemini_api_key)
                VALUES ({ph}, {ph})
            """, (empresa_id, chave_limpa))

        conexao.commit()
        return {
            "empresa_id": empresa_id,
            "gemini_api_key_mascarada": mascarar_api_key(chave_limpa),
            "possui_chave_propria": bool(chave_limpa)
        }
    except Exception as e:
        conexao.rollback()
        logger.error(f"[CompanyService] Erro ao salvar chave Gemini da empresa {empresa_id}: {e}")
        raise e
    finally:
        conexao.close()


def listar_empresas() -> list:
    """
    Retorna a lista de todas as empresas/ambientes cadastrados com totais de usuários e documentos.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    try:
        cursor.execute("""
            SELECT 
                e.id, 
                e.nome, 
                e.cnpj_ou_identificador, 
                e.data_criacao,
                COUNT(DISTINCT u.id) AS total_usuarios,
                COUNT(DISTINCT d.id) AS total_documentos
            FROM empresas e
            LEFT JOIN usuarios u ON u.empresa_id = e.id
            LEFT JOIN documentos d ON d.empresa_id = e.id
            GROUP BY e.id, e.nome, e.cnpj_ou_identificador, e.data_criacao
            ORDER BY e.id ASC
        """)
        linhas = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "nome": r["nome"],
                "cnpj_ou_identificador": r["cnpj_ou_identificador"] or "",
                "data_criacao": str(r["data_criacao"]),
                "total_usuarios": r["total_usuarios"],
                "total_documentos": r["total_documentos"]
            }
            for r in linhas
        ]
    finally:
        conexao.close()


def cadastrar_empresa(nome: str, cnpj_ou_identificador: str | None = None) -> dict:
    """
    Cadastra uma nova empresa/ambiente no sistema e cria suas configurações padrão.
    """
    from datetime import datetime
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    data_criacao = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        cursor.execute(f"""
            INSERT INTO empresas (nome, cnpj_ou_identificador, data_criacao)
            VALUES ({ph}, {ph}, {ph})
        """, (nome, cnpj_ou_identificador or None, data_criacao))
        conexao.commit()

        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            empresa_id = cursor.lastrowid
        else:
            cursor.execute(f"SELECT id FROM empresas WHERE nome = {ph} ORDER BY id DESC LIMIT 1", (nome,))
            row = cursor.fetchone()
            empresa_id = row["id"] if row else None

        # Cria configuração padrão de suporte
        if empresa_id:
            cursor.execute(f"""
                INSERT INTO configuracoes_empresa (empresa_id, numero_suporte_humano, mensagem_suporte)
                VALUES ({ph}, {ph}, {ph})
            """, (empresa_id, CONFIGURACAO_PADRAO["numero_suporte_humano"], CONFIGURACAO_PADRAO["mensagem_suporte"]))
            conexao.commit()

        return {
            "sucesso": True,
            "empresa": {
                "id": empresa_id,
                "nome": nome,
                "cnpj_ou_identificador": cnpj_ou_identificador or "",
                "data_criacao": data_criacao
            }
        }
    except Exception as e:
        conexao.rollback()
        erro = str(e)
        if "unique" in erro.lower() or "duplicate" in erro.lower():
            return {"sucesso": False, "erro": "CNPJ ou identificador já cadastrado para outra empresa."}
        return {"sucesso": False, "erro": erro}
    finally:
        conexao.close()


def deletar_empresa(empresa_id: int) -> bool:
    """
    Remove uma empresa/ambiente pelo ID (exceto a empresa padrão ID 1).
    """
    if empresa_id == 1:
        return False

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"DELETE FROM configuracoes_empresa WHERE empresa_id = {ph}", (empresa_id,))
        cursor.execute(f"DELETE FROM usuarios WHERE empresa_id = {ph}", (empresa_id,))
        cursor.execute(f"DELETE FROM documentos WHERE empresa_id = {ph}", (empresa_id,))
        cursor.execute(f"DELETE FROM empresas WHERE id = {ph}", (empresa_id,))
        conexao.commit()
        return True
    finally:
        conexao.close()

