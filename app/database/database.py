import os
import sqlite3
from pathlib import Path

# Detecta se está em produção (DATABASE_URL definida pelo Render) ou local (SQLite)
DATABASE_URL = os.getenv("DATABASE_URL")
USAR_POSTGRES = DATABASE_URL is not None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "fluxia.db"


def conectar():
    """Retorna uma conexão com o banco correto: PostgreSQL (produção) ou SQLite (local)."""
    if USAR_POSTGRES:
        import psycopg2
        import psycopg2.extras
        conexao = psycopg2.connect(DATABASE_URL)
        return conexao
    else:
        conexao = sqlite3.connect(DB_PATH)
        conexao.row_factory = sqlite3.Row
        return conexao


def _cursor(conexao):
    """Retorna cursor compatível: RealDictCursor (Postgres) ou cursor padrão do SQLite."""
    if USAR_POSTGRES:
        import psycopg2.extras
        return conexao.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        return conexao.cursor()


def _placeholder():
    """Retorna o placeholder correto: %s (Postgres) ou ? (SQLite)."""
    return "%s" if USAR_POSTGRES else "?"


def criar_banco():
    conexao = conectar()
    cursor = _cursor(conexao)

    if USAR_POSTGRES:
        _criar_banco_postgres(cursor)
    else:
        _criar_banco_sqlite(cursor)

    conexao.commit()
    conexao.close()
    print("[OK] Banco de dados inicializado com sucesso!")


# ---------------------------------------------------------------------------
# SQLite — desenvolvimento local
# ---------------------------------------------------------------------------

def _criar_banco_sqlite(cursor):
    # 1. Tabela de Empresas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS empresas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            cnpj_ou_identificador TEXT UNIQUE,
            data_criacao TEXT NOT NULL
        )
    """)

    # 2. Tabela de Usuários
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            perfil TEXT DEFAULT 'cliente',
            telegram_chat_id TEXT UNIQUE,
            status TEXT DEFAULT 'ativo',
            data_criacao TEXT NOT NULL,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 3. Tabela de Documentos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            nome_arquivo TEXT NOT NULL,
            tipo_arquivo TEXT NOT NULL,
            caminho_arquivo TEXT NOT NULL,
            conteudo_texto TEXT NOT NULL,
            hash_conteudo TEXT NOT NULL,
            nivel_acesso TEXT DEFAULT 'publico',
            data_upload DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        )
    """)

    # 4. Tabela de Instruções
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instrucoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT,
            conteudo TEXT NOT NULL,
            data_criacao TEXT NOT NULL
        )
    """)

    # 5. Tabela de Chunks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER NOT NULL,
            numero_chunk INTEGER NOT NULL,
            conteudo TEXT NOT NULL,
            embedding TEXT,
            FOREIGN KEY (documento_id) REFERENCES documentos(id) ON DELETE CASCADE
        )
    """)

    # 6. Histórico de Conversas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_conversas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_chat_id TEXT NOT NULL,
            mensagem_usuario TEXT NOT NULL,
            resposta_ia TEXT NOT NULL,
            data_interacao DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 7. Configurações da Empresa
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes_empresa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            numero_suporte_humano TEXT DEFAULT '(11) 99999-9999',
            mensagem_suporte TEXT DEFAULT 'Por favor, entre em contato com nossa equipe de atendimento.',
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        )
    """)

    # Migrações idempotentes para bancos antigos
    cursor.execute("PRAGMA table_info(chunks)")
    colunas_chunks = [c["name"] for c in cursor.fetchall()]
    if "embedding" not in colunas_chunks:
        cursor.execute("ALTER TABLE chunks ADD COLUMN embedding TEXT")

    cursor.execute("PRAGMA table_info(documentos)")
    colunas_docs = [c["name"] for c in cursor.fetchall()]
    if "empresa_id" not in colunas_docs:
        cursor.execute("ALTER TABLE documentos ADD COLUMN empresa_id INTEGER DEFAULT 1")
    if "nivel_acesso" not in colunas_docs:
        cursor.execute("ALTER TABLE documentos ADD COLUMN nivel_acesso TEXT DEFAULT 'publico'")

    cursor.execute("PRAGMA table_info(usuarios)")
    colunas_usuarios = [c["name"] for c in cursor.fetchall()]
    if "perfil" not in colunas_usuarios:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'cliente'")
        if "role" in colunas_usuarios:
            cursor.execute("""
                UPDATE usuarios
                SET perfil = CASE
                    WHEN role = 'admin' THEN 'admin'
                    WHEN role = 'funcionario' THEN 'funcionario'
                    ELSE 'cliente'
                END
            """)

    # Dados iniciais
    cursor.execute("SELECT COUNT(*) AS total FROM empresas")
    if cursor.fetchone()["total"] == 0:
        cursor.execute("""
            INSERT INTO empresas (id, nome, cnpj_ou_identificador, data_criacao)
            VALUES (1, 'Empresa Padrão', '00.000.000/0001-00', datetime('now'))
        """)

    cursor.execute("SELECT COUNT(*) AS total FROM configuracoes_empresa WHERE empresa_id = 1")
    if cursor.fetchone()["total"] == 0:
        cursor.execute("""
            INSERT INTO configuracoes_empresa (empresa_id, numero_suporte_humano, mensagem_suporte)
            VALUES (1, '(11) 99999-9999', 'Por favor, entre em contato com nossa equipe de atendimento.')
        """)


# ---------------------------------------------------------------------------
# PostgreSQL — produção (Render)
# ---------------------------------------------------------------------------

def _criar_banco_postgres(cursor):
    # 1. Empresas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS empresas (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            cnpj_ou_identificador TEXT UNIQUE,
            data_criacao TEXT NOT NULL
        )
    """)

    # 2. Usuários
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            perfil TEXT DEFAULT 'cliente',
            telegram_chat_id TEXT UNIQUE,
            status TEXT DEFAULT 'ativo',
            data_criacao TEXT NOT NULL,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 3. Documentos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER DEFAULT 1,
            nome_arquivo TEXT NOT NULL,
            tipo_arquivo TEXT NOT NULL,
            caminho_arquivo TEXT NOT NULL,
            conteudo_texto TEXT NOT NULL,
            hash_conteudo TEXT NOT NULL,
            nivel_acesso TEXT DEFAULT 'publico',
            data_upload TIMESTAMP DEFAULT NOW(),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        )
    """)

    # 4. Instruções
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instrucoes (
            id SERIAL PRIMARY KEY,
            titulo TEXT,
            conteudo TEXT NOT NULL,
            data_criacao TEXT NOT NULL
        )
    """)

    # 5. Chunks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id SERIAL PRIMARY KEY,
            documento_id INTEGER NOT NULL,
            numero_chunk INTEGER NOT NULL,
            conteudo TEXT NOT NULL,
            embedding TEXT,
            FOREIGN KEY (documento_id) REFERENCES documentos(id) ON DELETE CASCADE
        )
    """)

    # 6. Histórico de Conversas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_conversas (
            id SERIAL PRIMARY KEY,
            telegram_chat_id TEXT NOT NULL,
            mensagem_usuario TEXT NOT NULL,
            resposta_ia TEXT NOT NULL,
            data_interacao TIMESTAMP DEFAULT NOW()
        )
    """)

    # 7. Configurações da Empresa
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes_empresa (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER DEFAULT 1,
            numero_suporte_humano TEXT DEFAULT '(11) 99999-9999',
            mensagem_suporte TEXT DEFAULT 'Por favor, entre em contato com nossa equipe de atendimento.',
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        )
    """)

    # Dados iniciais (INSERT ... ON CONFLICT DO NOTHING é idiomático no Postgres)
    cursor.execute("""
        INSERT INTO empresas (id, nome, cnpj_ou_identificador, data_criacao)
        VALUES (1, 'Empresa Padrão', '00.000.000/0001-00', NOW())
        ON CONFLICT (id) DO NOTHING
    """)

    cursor.execute("""
        INSERT INTO configuracoes_empresa (empresa_id, numero_suporte_humano, mensagem_suporte)
        SELECT 1, '(11) 99999-9999', 'Por favor, entre em contato com nossa equipe de atendimento.'
        WHERE NOT EXISTS (
            SELECT 1 FROM configuracoes_empresa WHERE empresa_id = 1
        )
    """)