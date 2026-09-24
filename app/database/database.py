import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Detecta se está em produção (DATABASE_URL definida pelo Render) ou local (SQLite)
DATABASE_URL = os.getenv("DATABASE_URL")
USAR_POSTGRES = DATABASE_URL is not None and DATABASE_URL.strip() != ""

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

    # 8. Produtos (CRM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            descricao TEXT,
            ativo BOOLEAN DEFAULT 1,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 9. Clientes (CRM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            telefone TEXT,
            email TEXT,
            telegram_chat_id TEXT UNIQUE,
            origem TEXT DEFAULT 'telegram',
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 10. Negócios (CRM Pipeline)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negocios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER,
            valor_estimado REAL DEFAULT 0,
            estagio TEXT DEFAULT 'novo' CHECK (estagio IN ('novo','qualificado','proposta','negociacao','fechado','perdido')),
            proposta_enviada BOOLEAN DEFAULT 0,
            ultima_interacao_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            ultimo_followup_em DATETIME,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE SET NULL
        )
    """)

    # 11. Contratos (CRM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contratos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER UNIQUE NOT NULL,
            status TEXT DEFAULT 'pendente' CHECK (status IN ('pendente','assinado','cancelado')),
            valor_contrato REAL DEFAULT 0,
            data_assinatura DATETIME,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (negocio_id) REFERENCES negocios(id) ON DELETE CASCADE
        )
    """)

    # 12. Conversas Telegram (Status Atendente Bot vs Humano)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversas_telegram (
            chat_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'bot' CHECK (status IN ('bot','humano')),
            atendente_id INTEGER,
            atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (atendente_id) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)

    # 13. Perguntas Histórico & Analytics
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS perguntas_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal TEXT CHECK (canal IN ('telegram','portal')),
            empresa_id INTEGER NOT NULL,
            usuario_id INTEGER,
            telegram_chat_id TEXT,
            pergunta TEXT NOT NULL,
            resposta TEXT NOT NULL,
            documentos_utilizados TEXT,
            teve_contexto BOOLEAN DEFAULT 0,
            fonte_resposta TEXT CHECK (fonte_resposta IN ('base_conhecimento','conhecimento_geral') OR fonte_resposta IS NULL),
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
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

    cursor.execute("SELECT COUNT(*) AS total FROM produtos WHERE empresa_id = 1")
    if cursor.fetchone()["total"] == 0:
        cursor.execute("""
            INSERT INTO produtos (empresa_id, nome, descricao, ativo)
            VALUES (1, 'Plano Pro FluxIA', 'Licença mensal da plataforma CRM com agentes inteligentes e RAG corporativo', 1),
                   (1, 'Consultoria em IA', 'Implantação especializada e treinamento da equipe para automação de vendas', 1)
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
            data_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            data_interacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    # 8. Produtos (CRM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            descricao TEXT,
            ativo BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 9. Clientes (CRM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            telefone TEXT,
            email TEXT,
            telegram_chat_id TEXT UNIQUE,
            origem TEXT DEFAULT 'telegram',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 10. Negócios (CRM Pipeline)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negocios (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER,
            valor_estimado NUMERIC(12,2) DEFAULT 0,
            estagio TEXT DEFAULT 'novo' CHECK (estagio IN ('novo','qualificado','proposta','negociacao','fechado','perdido')),
            proposta_enviada BOOLEAN DEFAULT FALSE,
            ultima_interacao_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ultimo_followup_em TIMESTAMP,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE SET NULL
        )
    """)

    # 11. Contratos (CRM)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contratos (
            id SERIAL PRIMARY KEY,
            negocio_id INTEGER UNIQUE NOT NULL,
            status TEXT DEFAULT 'pendente' CHECK (status IN ('pendente','assinado','cancelado')),
            valor_contrato NUMERIC(12,2) DEFAULT 0,
            data_assinatura TIMESTAMP,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (negocio_id) REFERENCES negocios(id) ON DELETE CASCADE
        )
    """)

    # 12. Conversas Telegram (Status Atendente Bot vs Humano)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversas_telegram (
            chat_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'bot' CHECK (status IN ('bot','humano')),
            atendente_id INTEGER,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (atendente_id) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)

    # 13. Perguntas Histórico & Analytics
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS perguntas_historico (
            id SERIAL PRIMARY KEY,
            canal TEXT CHECK (canal IN ('telegram','portal')),
            empresa_id INTEGER NOT NULL,
            usuario_id INTEGER,
            telegram_chat_id TEXT,
            pergunta TEXT NOT NULL,
            resposta TEXT NOT NULL,
            documentos_utilizados TEXT,
            teve_contexto BOOLEAN DEFAULT FALSE,
            fonte_resposta TEXT CHECK (fonte_resposta IN ('base_conhecimento','conhecimento_geral') OR fonte_resposta IS NULL),
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)

    # Migrações idempotentes para Postgres
    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='empresa_id') THEN
                ALTER TABLE documentos ADD COLUMN empresa_id INTEGER DEFAULT 1;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='nivel_acesso') THEN
                ALTER TABLE documentos ADD COLUMN nivel_acesso TEXT DEFAULT 'publico';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='usuarios' AND column_name='perfil') THEN
                ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'cliente';
            END IF;
        END $$;
    """)

    # Dados iniciais
    cursor.execute("""
        INSERT INTO empresas (id, nome, cnpj_ou_identificador, data_criacao)
        VALUES (1, 'Empresa Padrão', '00.000.000/0001-00', NOW()::text)
        ON CONFLICT (id) DO NOTHING
    """)

    cursor.execute("""
        INSERT INTO configuracoes_empresa (empresa_id, numero_suporte_humano, mensagem_suporte)
        SELECT 1, '(11) 99999-9999', 'Por favor, entre em contato com nossa equipe de atendimento.'
        WHERE NOT EXISTS (
            SELECT 1 FROM configuracoes_empresa WHERE empresa_id = 1
        )
    """)

    cursor.execute("""
        INSERT INTO produtos (empresa_id, nome, descricao, ativo)
        SELECT 1, 'Plano Pro FluxIA', 'Licença mensal da plataforma CRM com agentes inteligentes e RAG corporativo', TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM produtos WHERE empresa_id = 1
        )
    """)

    cursor.execute("""
        INSERT INTO produtos (empresa_id, nome, descricao, ativo)
        SELECT 1, 'Consultoria em IA', 'Implantação especializada e treinamento da equipe para automação de vendas', TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM produtos WHERE empresa_id = 1 AND nome = 'Consultoria em IA'
        )
    """)