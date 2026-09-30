import logging
from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)

CONFIGURACAO_PADRAO = {
    "empresa_id": 1,
    "numero_suporte_humano": "(11) 99999-9999",
    "mensagem_suporte": "Por favor, entre em contato com nossa equipe de atendimento.",
    "gemini_api_key": None,
    "openai_api_key": None,
    "provedor_ia_padrao": "google",
    "fuso_horario": "America/Sao_Paulo",
    "telegram_bot_token": None,
    "telegram_bot_username": None,
    "telegram_webhook_ativo": False
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
    Busca as configurações da empresa (como telefone, mensagem de suporte, fuso horário e chaves BYOK Google e OpenAI,
    além do token dedicado do bot do Telegram da empresa).
    Caso não exista configuração cadastrada, retorna os valores padrão do sistema.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT empresa_id, numero_suporte_humano, mensagem_suporte, gemini_api_key, openai_api_key, provedor_ia_padrao, fuso_horario,
                   telegram_bot_token, telegram_bot_username, telegram_webhook_ativo
            FROM configuracoes_empresa
            WHERE empresa_id = {ph}
            ORDER BY id ASC
            LIMIT 1
        """, (empresa_id,))
        linha = cursor.fetchone()

        if linha:
            chave_gemini = linha.get("gemini_api_key") if isinstance(linha, dict) else linha["gemini_api_key"]
            chave_openai = linha.get("openai_api_key") if isinstance(linha, dict) else linha["openai_api_key"]
            provedor = linha.get("provedor_ia_padrao") if isinstance(linha, dict) else linha["provedor_ia_padrao"]
            fuso = linha.get("fuso_horario") if isinstance(linha, dict) else linha["fuso_horario"]
            tg_token = linha.get("telegram_bot_token") if isinstance(linha, dict) else linha["telegram_bot_token"]
            tg_user = linha.get("telegram_bot_username") if isinstance(linha, dict) else linha["telegram_bot_username"]
            tg_ativo = bool(linha.get("telegram_webhook_ativo") if isinstance(linha, dict) else linha["telegram_webhook_ativo"])

            return {
                "empresa_id": linha["empresa_id"],
                "numero_suporte_humano": linha["numero_suporte_humano"] or CONFIGURACAO_PADRAO["numero_suporte_humano"],
                "mensagem_suporte": linha["mensagem_suporte"] or CONFIGURACAO_PADRAO["mensagem_suporte"],
                "gemini_api_key": chave_gemini,
                "gemini_api_key_mascarada": mascarar_api_key(chave_gemini),
                "openai_api_key": chave_openai,
                "openai_api_key_mascarada": mascarar_api_key(chave_openai),
                "provedor_ia_padrao": provedor or "google",
                "fuso_horario": fuso or "America/Sao_Paulo",
                "possui_chave_propria": bool((chave_gemini and chave_gemini.strip()) or (chave_openai and chave_openai.strip())),
                "telegram_bot_token": tg_token,
                "telegram_bot_token_mascarada": mascarar_api_key(tg_token),
                "telegram_bot_username": tg_user,
                "telegram_webhook_ativo": tg_ativo,
                "possui_bot_proprio": bool(tg_token and tg_token.strip())
            }

        padrao = CONFIGURACAO_PADRAO.copy()
        padrao["gemini_api_key_mascarada"] = ""
        padrao["openai_api_key_mascarada"] = ""
        padrao["possui_chave_propria"] = False
        padrao["telegram_bot_token_mascarada"] = ""
        padrao["possui_bot_proprio"] = False
        return padrao
    except Exception as e:
        logger.error(f"[CompanyService] Erro ao buscar configurações da empresa {empresa_id}: {e}")
        padrao = CONFIGURACAO_PADRAO.copy()
        padrao["gemini_api_key_mascarada"] = ""
        padrao["openai_api_key_mascarada"] = ""
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


def atualizar_configuracao_ia(
    empresa_id: int,
    provedor_ia_padrao: str = "google",
    openai_api_key: str | None = None,
    gemini_api_key: str | None = None
) -> dict:
    """
    Atualiza as preferências de IA Multi-Vendor (provedor padrão, chave Gemini e chave OpenAI) da empresa.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    chave_gemini_limpa = gemini_api_key.strip() if gemini_api_key and gemini_api_key.strip() else None
    chave_openai_limpa = openai_api_key.strip() if openai_api_key and openai_api_key.strip() else None
    provedor_limpo = (provedor_ia_padrao or "google").strip().lower()

    try:
        cursor.execute(f"SELECT id, gemini_api_key, openai_api_key FROM configuracoes_empresa WHERE empresa_id = {ph}", (empresa_id,))
        existente = cursor.fetchone()

        if existente:
            # Preserva chaves anteriores se não informadas na chamada
            gemini_final = chave_gemini_limpa if gemini_api_key is not None else existente.get("gemini_api_key")
            openai_final = chave_openai_limpa if openai_api_key is not None else existente.get("openai_api_key")

            cursor.execute(f"""
                UPDATE configuracoes_empresa
                SET provedor_ia_padrao = {ph},
                    gemini_api_key = {ph},
                    openai_api_key = {ph}
                WHERE empresa_id = {ph}
            """, (provedor_limpo, gemini_final, openai_final, empresa_id))
        else:
            cursor.execute(f"""
                INSERT INTO configuracoes_empresa (empresa_id, provedor_ia_padrao, gemini_api_key, openai_api_key)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (empresa_id, provedor_limpo, chave_gemini_limpa, chave_openai_limpa))

        conexao.commit()
        return obter_configuracao_empresa(empresa_id)
    except Exception as e:
        conexao.rollback()
        logger.error(f"[CompanyService] Erro ao atualizar configurações de IA da empresa {empresa_id}: {e}")
        raise e

def atualizar_fuso_horario(empresa_id: int, fuso_horario: str) -> dict:
    """
    Atualiza o fuso horário global da empresa (ex: 'America/Sao_Paulo', 'America/Manaus', 'auto').
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    fuso = (fuso_horario or "America/Sao_Paulo").strip()
    try:
        cursor.execute(f"SELECT id FROM configuracoes_empresa WHERE empresa_id = {ph}", (empresa_id,))
        existente = cursor.fetchone()

        if existente:
            cursor.execute(f"""
                UPDATE configuracoes_empresa
                SET fuso_horario = {ph}
                WHERE empresa_id = {ph}
            """, (fuso, empresa_id))
        else:
            cursor.execute(f"""
                INSERT INTO configuracoes_empresa (empresa_id, fuso_horario)
                VALUES ({ph}, {ph})
            """, (empresa_id, fuso))

        conexao.commit()
        return obter_configuracao_empresa(empresa_id)
    except Exception as e:
        conexao.rollback()
        logger.error(f"[CompanyService] Erro ao atualizar fuso horário da empresa {empresa_id}: {e}")
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


def atualizar_telegram_bot_empresa(
    empresa_id: int,
    telegram_bot_token: str | None,
    telegram_bot_username: str | None = None,
    telegram_webhook_ativo: bool = False
) -> dict:
    """
    Atualiza as configurações do bot do Telegram da empresa (Token do BotFather, username e status).
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
                SET telegram_bot_token = {ph},
                    telegram_bot_username = {ph},
                    telegram_webhook_ativo = {ph}
                WHERE empresa_id = {ph}
            """, (telegram_bot_token, telegram_bot_username, telegram_webhook_ativo, empresa_id))
        else:
            cursor.execute(f"""
                INSERT INTO configuracoes_empresa (
                    empresa_id, numero_suporte_humano, mensagem_suporte,
                    telegram_bot_token, telegram_bot_username, telegram_webhook_ativo
                )
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """, (
                empresa_id,
                CONFIGURACAO_PADRAO["numero_suporte_humano"],
                CONFIGURACAO_PADRAO["mensagem_suporte"],
                telegram_bot_token,
                telegram_bot_username,
                telegram_webhook_ativo
            ))

        conexao.commit()
        return obter_configuracao_empresa(empresa_id)
    except Exception as e:
        conexao.rollback()
        logger.error(f"[CompanyService] Erro ao atualizar bot do Telegram da empresa {empresa_id}: {e}")
        raise e
    finally:
        conexao.close()

