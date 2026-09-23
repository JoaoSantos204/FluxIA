from datetime import datetime
import hashlib
from app.database.database import conectar, _cursor, _placeholder

def gerar_hash_senha(senha: str) -> str:
    """Gera um hash SHA-256 simples para a senha."""
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()

PERFIS_PERMITIDOS = ['master', 'admin', 'funcionario', 'cliente']

def cadastrar_usuario(empresa_id: int, nome: str, email: str, senha: str, perfil: str = 'cliente'):
    """Cadastra um novo usuário no sistema (Padrão: cliente)."""
    if perfil not in PERFIS_PERMITIDOS:
        return {"sucesso": False, "erro": f"Perfil inválido. Use um dos seguintes: {', '.join(PERFIS_PERMITIDOS)}."}

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
    """Altera o perfil do usuário ('master', 'admin', 'funcionario' ou 'cliente')."""
    if novo_perfil not in PERFIS_PERMITIDOS:
        return {"sucesso": False, "erro": f"Perfil inválido. Use um dos seguintes: {', '.join(PERFIS_PERMITIDOS)}."}

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


def autenticar_usuario(email: str, senha: str) -> dict:
    """
    Autentica um usuário conferindo email, senha_hash e se a conta está ativa.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome, email, senha_hash, perfil, status
            FROM usuarios
            WHERE LOWER(email) = {ph}
        """, (email.strip().lower(),))
        linha = cursor.fetchone()

        if not linha:
            return {"sucesso": False, "erro": "E-mail ou senha incorretos."}

        if linha["status"] != "ativo":
            return {"sucesso": False, "erro": "Esta conta foi desativada. Contate o administrador."}

        senha_hash_calculado = gerar_hash_senha(senha)
        if senha_hash_calculado != linha["senha_hash"]:
            return {"sucesso": False, "erro": "E-mail ou senha incorretos."}

        return {
            "sucesso": True,
            "usuario": {
                "id": linha["id"],
                "empresa_id": linha["empresa_id"],
                "nome": linha["nome"],
                "email": linha["email"],
                "perfil": linha["perfil"] or "cliente"
            }
        }
    finally:
        conexao.close()


def redefinir_senha_usuario(email: str, nova_senha: str) -> bool:
    """
    Redefine a senha de um usuário pelo e-mail.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    nova_hash = gerar_hash_senha(nova_senha)

    try:
        cursor.execute(f"""
            UPDATE usuarios
            SET senha_hash = {ph}
            WHERE LOWER(email) = {ph}
        """, (nova_hash, email.strip().lower()))
        conexao.commit()
        return cursor.rowcount > 0
    finally:
        conexao.close()


def cadastrar_empresa_com_admin(nome_empresa: str, cnpj: str, nome_responsavel: str, email: str, senha: str) -> dict:
    """
    Cadastra uma nova empresa no SaaS com CNPJ OBRIGATÓRIO e cria o primeiro usuário com perfil 'admin' vinculado a ela.
    """
    nome_emp = nome_empresa.strip()
    cnpj_limpo = cnpj.strip()
    nome_user = nome_responsavel.strip()
    email_user = email.strip().lower()

    if not nome_emp:
        return {"sucesso": False, "erro": "O nome da empresa é obrigatório."}

    if not cnpj_limpo:
        return {"sucesso": False, "erro": "O CNPJ é obrigatório para cadastrar a empresa."}

    if len(senha.strip()) < 4:
        return {"sucesso": False, "erro": "A senha deve ter no mínimo 4 caracteres."}

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    data_criacao = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # 1. Verifica se e-mail já existe
        cursor.execute(f"SELECT id FROM usuarios WHERE LOWER(email) = {ph}", (email_user,))
        if cursor.fetchone():
            return {"sucesso": False, "erro": "Este e-mail já está cadastrado em outra conta."}

        # 2. Verifica se CNPJ já existe
        cursor.execute(f"SELECT id FROM empresas WHERE cnpj_ou_identificador = {ph}", (cnpj_limpo,))
        if cursor.fetchone():
            return {"sucesso": False, "erro": "Este CNPJ já está cadastrado no sistema."}

        # 3. Insere a Empresa
        cursor.execute(f"""
            INSERT INTO empresas (nome, cnpj_ou_identificador, data_criacao)
            VALUES ({ph}, {ph}, {ph})
        """, (nome_emp, cnpj_limpo, data_criacao))

        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            empresa_id = cursor.lastrowid
        else:
            cursor.execute(f"SELECT id FROM empresas WHERE cnpj_ou_identificador = {ph}", (cnpj_limpo,))
            empresa_id = cursor.fetchone()["id"]

        # 4. Insere Configurações padrão de suporte para a nova empresa
        cursor.execute(f"""
            INSERT INTO configuracoes_empresa (empresa_id, numero_suporte_humano, mensagem_suporte)
            VALUES ({ph}, {ph}, {ph})
        """, (empresa_id, "(11) 99999-9999", "Por favor, entre em contato com nossa equipe de atendimento."))

        # 5. Insere o Usuário com Perfil 'admin'
        senha_hash = gerar_hash_senha(senha)
        cursor.execute(f"""
            INSERT INTO usuarios (empresa_id, nome, email, senha_hash, perfil, status, data_criacao)
            VALUES ({ph}, {ph}, {ph}, {ph}, 'admin', 'ativo', {ph})
        """, (empresa_id, nome_user, email_user, senha_hash, data_criacao))

        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            usuario_id = cursor.lastrowid
        else:
            cursor.execute(f"SELECT id FROM usuarios WHERE LOWER(email) = {ph}", (email_user,))
            usuario_id = cursor.fetchone()["id"]

        conexao.commit()

        return {
            "sucesso": True,
            "usuario": {
                "id": usuario_id,
                "empresa_id": empresa_id,
                "nome": nome_user,
                "email": email_user,
                "perfil": "admin"
            },
            "empresa": {
                "id": empresa_id,
                "nome": nome_emp,
                "cnpj": cnpj_limpo
            }
        }
    except Exception as e:
        conexao.rollback()
        return {"sucesso": False, "erro": str(e)}
    finally:
        conexao.close()
