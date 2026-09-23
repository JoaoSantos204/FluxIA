from datetime import datetime
import hashlib
from app.database.database import conectar, _cursor, _placeholder

def gerar_hash_senha(senha: str) -> str:
    """Gera um hash SHA-256 simples para a senha."""
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()

def cadastrar_usuario(empresa_id: int, nome: str, email: str, senha: str, perfil: str = 'cliente'):
    """Cadastra um novo usuário no sistema (Padrão: cliente)."""
    if perfil not in ['admin', 'funcionario', 'cliente']:
        return {"sucesso": False, "erro": "Perfil inválido. Use 'admin', 'funcionario' ou 'cliente'."}

    conexao = conectar()
    cursor = _cursor(conexao)
    senha_hash = gerar_hash_senha(senha)
    data_criacao = datetime.now().isoformat()
    ph = _placeholder()

    try:
        cursor.execute(f"""
            INSERT INTO usuarios (empresa_id, nome, email, senha_hash, perfil, data_criacao)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (empresa_id, nome, email, senha_hash, perfil, data_criacao))

        conexao.commit()

        # lastrowid funciona em SQLite; no Postgres usamos RETURNING via fetchone
        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            usuario_id = cursor.lastrowid
        else:
            # Busca o id recém inserido
            cursor.execute(f"SELECT id FROM usuarios WHERE email = {ph}", (email,))
            row = cursor.fetchone()
            usuario_id = row["id"] if row else None

        return {"sucesso": True, "usuario_id": usuario_id}
    except Exception as e:
        erro = str(e)
        if "unique" in erro.lower() or "duplicate" in erro.lower():
            return {"sucesso": False, "erro": "E-mail já cadastrado."}
        return {"sucesso": False, "erro": erro}
    finally:
        conexao.close()

def vincular_telegram_chat_id(email: str, telegram_chat_id: str) -> bool:
    """Vincula o chat_id do Telegram ao usuário cadastrado."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    chat_id_str = str(telegram_chat_id)
    email_str = str(email).lower()

    cursor.execute(f"""
        UPDATE usuarios
        SET telegram_chat_id = {ph}
        WHERE LOWER(email) = {ph} AND status = 'ativo'
    """, (chat_id_str, email_str))

    conexao.commit()
    linhas_afetadas = cursor.rowcount
    conexao.close()
    return linhas_afetadas > 0

def buscar_usuario_por_telegram(telegram_chat_id: str):
    """Busca o perfil e empresa do usuário a partir do chat_id do Telegram."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    cursor.execute(f"""
        SELECT id, empresa_id, nome, email, perfil, status
        FROM usuarios
        WHERE telegram_chat_id = {ph} AND status = 'ativo'
    """, (str(telegram_chat_id),))

    usuario = cursor.fetchone()
    conexao.close()

    if not usuario:
        return None

    dados = dict(usuario)
    if not dados.get("perfil"):
        dados["perfil"] = "cliente"
    return dados

def alterar_perfil_usuario(usuario_id: int, novo_perfil: str):
    """Altera o perfil do usuário ('admin', 'funcionario' ou 'cliente')."""
    if novo_perfil not in ['admin', 'funcionario', 'cliente']:
        return {"sucesso": False, "erro": "Perfil inválido."}

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    cursor.execute(f"""
        UPDATE usuarios
        SET perfil = {ph}
        WHERE id = {ph}
    """, (novo_perfil, usuario_id))

    conexao.commit()
    conexao.close()
    return {"sucesso": True}

def alterar_role_usuario(usuario_id: int, nova_role: str):
    """Alias retrocompatível para alterar_perfil_usuario."""
    return alterar_perfil_usuario(usuario_id, nova_role)

def listar_usuarios(empresa_id: int | None = None) -> list[dict]:
    """Lista todos os usuários, opcionalmente filtrando por empresa."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        if empresa_id:
            cursor.execute(f"""
                SELECT id, empresa_id, nome, email, perfil, telegram_chat_id, status, data_criacao
                FROM usuarios
                WHERE empresa_id = {ph}
                ORDER BY id ASC
            """, (empresa_id,))
        else:
            cursor.execute("""
                SELECT id, empresa_id, nome, email, perfil, telegram_chat_id, status, data_criacao
                FROM usuarios
                ORDER BY id ASC
            """)
        linhas = cursor.fetchall()
        return [dict(linha) for linha in linhas]
    finally:
        conexao.close()

def obter_usuario_por_id(usuario_id: int) -> dict | None:
    """Obtém os dados públicos do usuário por ID."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome, email, perfil, telegram_chat_id, status, data_criacao
            FROM usuarios
            WHERE id = {ph}
        """, (usuario_id,))
        linha = cursor.fetchone()
        return dict(linha) if linha else None
    finally:
        conexao.close()

def alterar_status_usuario(usuario_id: int, novo_status: str) -> dict:
    """Altera o status do usuário ('ativo' ou 'inativo')."""
    if novo_status not in ['ativo', 'inativo']:
        return {"sucesso": False, "erro": "Status inválido. Use 'ativo' ou 'inativo'."}

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            UPDATE usuarios
            SET status = {ph}
            WHERE id = {ph}
        """, (novo_status, usuario_id))
        conexao.commit()
        if cursor.rowcount == 0:
            return {"sucesso": False, "erro": "Usuário não encontrado."}
        return {"sucesso": True}
    finally:
        conexao.close()

def deletar_usuario(usuario_id: int) -> bool:
    """Exclui um usuário do sistema."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"DELETE FROM usuarios WHERE id = {ph}", (usuario_id,))
        conexao.commit()
        return cursor.rowcount > 0
    finally:
        conexao.close()