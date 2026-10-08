import os
import psycopg2
import psycopg2.extras
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Validação estrita: O FluxIA agora opera exclusivamente sobre PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL or not DATABASE_URL.strip():
    raise RuntimeError("Defina a variável DATABASE_URL apontando para um PostgreSQL")


def conectar():
    """Retorna uma conexão ativa com o banco PostgreSQL com cursor de dicionário por padrão."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _cursor(conexao):
    """Retorna cursor configurado para mapeamento em dicionário (RealDictCursor)."""
    return conexao.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _placeholder():
    """Retorna o placeholder padrão do PostgreSQL (%s)."""
    return "%s"


def criar_banco():
    """Inicializa todas as tabelas, migrações e índices idempotentes no PostgreSQL."""
    conexao = conectar()
    cursor = _cursor(conexao)

    try:
        _criar_banco_postgres(cursor)
        conexao.commit()
        print("[OK] Banco de dados PostgreSQL inicializado com sucesso!")
    except Exception as e:
        conexao.rollback()
        raise e
    finally:
        conexao.close()


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
            origem VARCHAR(50) DEFAULT 'upload',
            ref_tipo VARCHAR(50),
            ref_id INTEGER,
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
            empresa_id INTEGER DEFAULT 1,
            telegram_chat_id TEXT NOT NULL,
            mensagem_usuario TEXT NOT NULL,
            resposta_ia TEXT NOT NULL,
            lida BOOLEAN DEFAULT FALSE,
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
            gemini_api_key TEXT,
            openai_api_key TEXT,
            provedor_ia_padrao TEXT DEFAULT 'google',
            fuso_horario TEXT DEFAULT 'America/Sao_Paulo',
            telegram_bot_token TEXT,
            telegram_bot_username TEXT,
            telegram_webhook_ativo BOOLEAN DEFAULT FALSE,
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
            preco NUMERIC(12,2) DEFAULT 0.0,
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
            aguardando_contato BOOLEAN DEFAULT FALSE,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 9.5 Pipelines & Etapas (CRM Configurável)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pipelines (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            nome VARCHAR(255) NOT NULL,
            produto_id INTEGER REFERENCES produtos(id) ON DELETE SET NULL,
            padrao BOOLEAN DEFAULT FALSE,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS etapas_pipeline (
            id SERIAL PRIMARY KEY,
            pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
            nome VARCHAR(100) NOT NULL,
            ordem INTEGER NOT NULL DEFAULT 1,
            cor VARCHAR(50) DEFAULT '#3b82f6',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 10. Negócios (CRM Pipeline)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS negocios (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER,
            etapa_id INTEGER REFERENCES etapas_pipeline(id) ON DELETE SET NULL,
            valor_estimado NUMERIC(12,2) DEFAULT 0,
            estagio TEXT DEFAULT 'novo',
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

    # 11.5 Propostas Comerciais
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS propostas (
            id SERIAL PRIMARY KEY,
            negocio_id INTEGER NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
            empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
            produto_id INTEGER REFERENCES produtos(id) ON DELETE SET NULL,
            valor NUMERIC(12,2) DEFAULT 0,
            condicoes_pagamento TEXT,
            validade_dias INTEGER DEFAULT 15,
            status_envio VARCHAR(50) DEFAULT 'pendente',
            email_destinatario VARCHAR(255),
            documento_id INTEGER REFERENCES documentos(id) ON DELETE SET NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 12. Conversas Telegram (Status Atendente Bot vs Humano)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversas_telegram (
            chat_id TEXT PRIMARY KEY,
            empresa_id INTEGER DEFAULT 1,
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

    # 14. Telemetria de IA & Observabilidade
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ia_telemetria_execucao (
            id SERIAL PRIMARY KEY,
            empresa_id INTEGER NOT NULL,
            canal TEXT DEFAULT 'telegram' CHECK (canal IN ('telegram','portal')),
            session_id TEXT,
            vendor TEXT NOT NULL,
            modelo TEXT NOT NULL,
            tokens_prompt INTEGER DEFAULT 0,
            tokens_completion INTEGER DEFAULT 0,
            tokens_total INTEGER DEFAULT 0,
            custo_estimado_usd DOUBLE PRECISION DEFAULT 0.0,
            latencia_ms INTEGER DEFAULT 0,
            status_execucao TEXT DEFAULT 'sucesso' CHECK (status_execucao IN ('sucesso','erro','fallback')),
            tools_executadas TEXT,
            mensagem_erro TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # 15. Continuous Evaluation (RAG Triad & LLM-as-a-Judge)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ia_evaluations (
            id SERIAL PRIMARY KEY,
            telemetria_id INTEGER,
            empresa_id INTEGER NOT NULL,
            pergunta TEXT NOT NULL,
            resposta TEXT NOT NULL,
            contexto_utilizado TEXT,
            score_fidelidade DOUBLE PRECISION,
            score_relevancia_resposta DOUBLE PRECISION,
            score_relevancia_contexto DOUBLE PRECISION,
            possivel_alucinacao BOOLEAN DEFAULT FALSE,
            justificativa_avaliacao TEXT,
            avaliador_modelo TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telemetria_id) REFERENCES ia_telemetria_execucao(id) ON DELETE SET NULL,
            FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
        )
    """)

    # Migrações idempotentes para colunas de bancos já existentes
    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='empresa_id') THEN
                ALTER TABLE documentos ADD COLUMN empresa_id INTEGER DEFAULT 1;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='nivel_acesso') THEN
                ALTER TABLE documentos ADD COLUMN nivel_acesso TEXT DEFAULT 'publico';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='origem') THEN
                ALTER TABLE documentos ADD COLUMN origem VARCHAR(50) DEFAULT 'upload';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='ref_tipo') THEN
                ALTER TABLE documentos ADD COLUMN ref_tipo VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documentos' AND column_name='ref_id') THEN
                ALTER TABLE documentos ADD COLUMN ref_id INTEGER;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='chunks' AND column_name='embedding') THEN
                ALTER TABLE chunks ADD COLUMN embedding TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='usuarios' AND column_name='perfil') THEN
                ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'cliente';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='clientes' AND column_name='aguardando_contato') THEN
                ALTER TABLE clientes ADD COLUMN aguardando_contato BOOLEAN DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='historico_conversas' AND column_name='lida') THEN
                ALTER TABLE historico_conversas ADD COLUMN lida BOOLEAN DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='historico_conversas' AND column_name='empresa_id') THEN
                ALTER TABLE historico_conversas ADD COLUMN empresa_id INTEGER DEFAULT 1;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversas_telegram' AND column_name='empresa_id') THEN
                ALTER TABLE conversas_telegram ADD COLUMN empresa_id INTEGER DEFAULT 1;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='gemini_api_key') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN gemini_api_key TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='openai_api_key') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN openai_api_key TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='provedor_ia_padrao') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN provedor_ia_padrao TEXT DEFAULT 'google';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='fuso_horario') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN fuso_horario TEXT DEFAULT 'America/Sao_Paulo';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='telegram_bot_token') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN telegram_bot_token TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='telegram_bot_username') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN telegram_bot_username TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='configuracoes_empresa' AND column_name='telegram_webhook_ativo') THEN
                ALTER TABLE configuracoes_empresa ADD COLUMN telegram_webhook_ativo BOOLEAN DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='negocios' AND column_name='etapa_id') THEN
                ALTER TABLE negocios ADD COLUMN etapa_id INTEGER REFERENCES etapas_pipeline(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='negocios' AND column_name='proposta_enviada') THEN
                ALTER TABLE negocios ADD COLUMN proposta_enviada BOOLEAN DEFAULT FALSE;
            END IF;
        END $$;
    """)

    # Relaxa CHECK constraint legado de estagio no Postgres caso exista
    try:
        cursor.execute("ALTER TABLE negocios DROP CONSTRAINT IF EXISTS negocios_estagio_check;")
    except Exception:
        pass

    # Garante índice único para telegram_bot_token por empresa
    try:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_config_empresa_telegram_token 
            ON configuracoes_empresa (telegram_bot_token) 
            WHERE telegram_bot_token IS NOT NULL AND telegram_bot_token != '';
        """)
    except Exception:
        pass

    # Migrações de consolidação de duplicados (Parte 3)
    _migrar_produtos_duplicados(cursor)
    _migrar_pipelines_duplicados(cursor)

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
            SELECT 1 FROM produtos WHERE empresa_id = 1 AND LOWER(TRIM(nome)) = 'plano pro fluxia'
        )
    """)

    cursor.execute("""
        INSERT INTO produtos (empresa_id, nome, descricao, ativo)
        SELECT 1, 'Consultoria em IA', 'Implantação especializada e treinamento da equipe para automação de vendas', TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM produtos WHERE empresa_id = 1 AND LOWER(TRIM(nome)) = 'consultoria em ia'
        )
    """)

    _garantir_pipelines_padrao(cursor)


def _migrar_produtos_duplicados(cursor):
    """
    Identifica produtos com mesmo empresa_id e LOWER(TRIM(nome)),
    renomeando os duplicados com sufixo ' (2)', ' (3)', etc., antes de criar o índice único.
    """
    cursor.execute("""
        SELECT empresa_id, LOWER(TRIM(nome)) as nome_norm, COUNT(*) as qtd
        FROM produtos
        GROUP BY empresa_id, LOWER(TRIM(nome))
        HAVING COUNT(*) > 1
    """)
    duplicados = cursor.fetchall()
    for item in duplicados:
        emp_id = item["empresa_id"]
        nome_norm = item["nome_norm"]
        cursor.execute("""
            SELECT id, nome
            FROM produtos
            WHERE empresa_id = %s AND LOWER(TRIM(nome)) = %s
            ORDER BY id ASC
        """, (emp_id, nome_norm))
        prods = cursor.fetchall()
        for idx, p in enumerate(prods[1:], start=2):
            novo_nome = f"{p['nome']} ({idx})"
            cursor.execute("UPDATE produtos SET nome = %s WHERE id = %s", (novo_nome, p["id"]))
            print(f"[MIGRAÇÃO] Produto duplicado ID {p['id']} renomeado para '{novo_nome}' na empresa {emp_id}")

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_produtos_empresa_nome_lower
        ON produtos (empresa_id, LOWER(TRIM(nome)));
    """)


def _migrar_pipelines_duplicados(cursor):
    """
    Garante regra de 1 pipeline por produto e 1 pipeline geral por empresa.
    Consolida eventuais pipelines duplicados existentes.
    """
    # 1. Pipelines por produto duplicados
    cursor.execute("""
        SELECT produto_id, COUNT(*) as qtd
        FROM pipelines
        WHERE produto_id IS NOT NULL
        GROUP BY produto_id
        HAVING COUNT(*) > 1
    """)
    dups_produtos = cursor.fetchall()
    for item in dups_produtos:
        prod_id = item["produto_id"]
        cursor.execute("SELECT id, empresa_id, nome FROM pipelines WHERE produto_id = %s ORDER BY id ASC", (prod_id,))
        pipes = cursor.fetchall()
        manter_pipe = pipes[0]
        extras = pipes[1:]

        cursor.execute("SELECT id, LOWER(TRIM(nome)) as nome_norm FROM etapas_pipeline WHERE pipeline_id = %s ORDER BY ordem ASC, id ASC", (manter_pipe["id"],))
        etapas_manter = cursor.fetchall()
        primeira_etapa_id = etapas_manter[0]["id"] if etapas_manter else None
        mapa_etapas_manter = {e["nome_norm"]: e["id"] for e in etapas_manter}

        for extra in extras:
            cursor.execute("SELECT id, LOWER(TRIM(nome)) as nome_norm FROM etapas_pipeline WHERE pipeline_id = %s", (extra["id"],))
            etapas_extra = cursor.fetchall()
            for ee in etapas_extra:
                alvo_etapa_id = mapa_etapas_manter.get(ee["nome_norm"], primeira_etapa_id)
                if alvo_etapa_id:
                    cursor.execute("UPDATE negocios SET etapa_id = %s WHERE etapa_id = %s", (alvo_etapa_id, ee["id"]))

            cursor.execute("DELETE FROM etapas_pipeline WHERE pipeline_id = %s", (extra["id"],))
            cursor.execute("DELETE FROM pipelines WHERE id = %s", (extra["id"],))
            print(f"[MIGRAÇÃO] Pipeline duplicado ID {extra['id']} consolidado no pipeline ID {manter_pipe['id']} para produto ID {prod_id}")

    # 2. Pipelines Gerais duplicados (produto_id IS NULL) por empresa
    cursor.execute("""
        SELECT empresa_id, COUNT(*) as qtd
        FROM pipelines
        WHERE produto_id IS NULL
        GROUP BY empresa_id
        HAVING COUNT(*) > 1
    """)
    dups_gerais = cursor.fetchall()
    for item in dups_gerais:
        emp_id = item["empresa_id"]
        cursor.execute("SELECT id, nome FROM pipelines WHERE empresa_id = %s AND produto_id IS NULL ORDER BY id ASC", (emp_id,))
        pipes = cursor.fetchall()
        manter_pipe = pipes[0]
        extras = pipes[1:]

        cursor.execute("SELECT id, LOWER(TRIM(nome)) as nome_norm FROM etapas_pipeline WHERE pipeline_id = %s ORDER BY ordem ASC, id ASC", (manter_pipe["id"],))
        etapas_manter = cursor.fetchall()
        primeira_etapa_id = etapas_manter[0]["id"] if etapas_manter else None
        mapa_etapas_manter = {e["nome_norm"]: e["id"] for e in etapas_manter}

        for extra in extras:
            cursor.execute("SELECT id, LOWER(TRIM(nome)) as nome_norm FROM etapas_pipeline WHERE pipeline_id = %s", (extra["id"],))
            etapas_extra = cursor.fetchall()
            for ee in etapas_extra:
                alvo_etapa_id = mapa_etapas_manter.get(ee["nome_norm"], primeira_etapa_id)
                if alvo_etapa_id:
                    cursor.execute("UPDATE negocios SET etapa_id = %s WHERE etapa_id = %s", (alvo_etapa_id, ee["id"]))

            cursor.execute("DELETE FROM etapas_pipeline WHERE pipeline_id = %s", (extra["id"],))
            cursor.execute("DELETE FROM pipelines WHERE id = %s", (extra["id"],))
            print(f"[MIGRAÇÃO] Pipeline Geral duplicado ID {extra['id']} consolidado no pipeline ID {manter_pipe['id']} para empresa ID {emp_id}")

    # Criação dos índices únicos parciais
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pipelines_produto_unique
        ON pipelines (produto_id)
        WHERE produto_id IS NOT NULL;
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pipelines_geral_unique
        ON pipelines (empresa_id)
        WHERE produto_id IS NULL;
    """)


def _garantir_pipelines_padrao(cursor):
    """Garante a existência de pipeline padrão com as 6 etapas para cada empresa cadastrada."""
    cursor.execute("SELECT id, nome FROM empresas")
    empresas = cursor.fetchall()

    etapas_padrao_info = [
        ("Novo", 1, "#3b82f6"),
        ("Qualificado", 2, "#6366f1"),
        ("Proposta", 3, "#a855f7"),
        ("Negociação", 4, "#f59e0b"),
        ("Fechado", 5, "#22c55e"),
        ("Perdido", 6, "#ef4444")
    ]

    for emp in empresas:
        emp_id = emp["id"]
        cursor.execute("SELECT id FROM pipelines WHERE empresa_id = %s AND padrao = %s", (emp_id, True))
        pip = cursor.fetchone()

        if not pip:
            cursor.execute("""
                INSERT INTO pipelines (empresa_id, nome, produto_id, padrao)
                VALUES (%s, 'Funil de Vendas Padrão', NULL, %s)
                RETURNING id
            """, (emp_id, True))
            pip = cursor.fetchone()

        if pip:
            pipeline_id = pip["id"]
            for nome_etapa, ordem, cor in etapas_padrao_info:
                cursor.execute("SELECT id FROM etapas_pipeline WHERE pipeline_id = %s AND nome = %s", (pipeline_id, nome_etapa))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO etapas_pipeline (pipeline_id, nome, ordem, cor)
                        VALUES (%s, %s, %s, %s)
                    """, (pipeline_id, nome_etapa, ordem, cor))

            # Migra negócios legados sem etapa_id vinculando ao ID correspondente no pipeline padrão
            cursor.execute("SELECT id, nome, LOWER(nome) as nome_lower FROM etapas_pipeline WHERE pipeline_id = %s", (pipeline_id,))
            etapas_map = {row["nome_lower"]: row["id"] for row in cursor.fetchall()}
            etapas_map["negociacao"] = etapas_map.get("negociação") or etapas_map.get("negociacao")

            cursor.execute("SELECT id, estagio FROM negocios WHERE empresa_id = %s AND etapa_id IS NULL", (emp_id,))
            negocios = cursor.fetchall()
            for neg in negocios:
                estagio_txt = (neg.get("estagio") or "novo").strip().lower()
                target_id = etapas_map.get(estagio_txt) or etapas_map.get("novo")
                if target_id:
                    cursor.execute("UPDATE negocios SET etapa_id = %s WHERE id = %s", (target_id, neg["id"]))