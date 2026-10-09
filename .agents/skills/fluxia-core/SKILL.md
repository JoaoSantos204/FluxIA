---
name: fluxia-core
description: Base do projeto FluxIA (SaaS multitenant FastAPI + PostgreSQL). Use SEMPRE que a tarefa envolver estrutura do projeto, banco de dados (DDL, migrações, novas tabelas/colunas), autenticação, login, cadastro de empresa, perfis RBAC (master/admin/colaborador), main.py, security_service, company.py ou configurações da empresa. Consulte antes de criar qualquer rota ou tabela nova, mesmo que o usuário não cite "multitenant".
---

# FluxIA Core

## Regras de economia de tokens (valem para TODAS as skills FluxIA)
- Leia só os arquivos listados na skill relevante. Não varra o repositório.
- Use busca (grep) por nome de função/rota antes de abrir um arquivo inteiro.
- Edite com patches/trechos mínimos. Nunca reescreva arquivo inteiro.
- Na resposta final: explique O QUE e COMO foi alterado, em poucas linhas. NÃO devolva o código completo.
- Não crie testes, docs ou refactors que não foram pedidos.

## Stack
Python 3.12, FastAPI (APIRouter por módulo, Pydantic, Uvicorn), PostgreSQL via psycopg2 + RealDictCursor, APScheduler, deploy no Render. Frontend em HTML/CSS/JS puro (sem React/Vue/npm).

## Regra nº 1: multitenancy por `empresa_id`
- TODA tabela de dados de negócio tem `empresa_id`.
- TODA query (SELECT/UPDATE/DELETE) filtra por `empresa_id`. Nunca confie em `empresa_id` vindo do body quando houver sessão; derive da sessão/usuário autenticado.
- Nova tabela de negócio => coluna `empresa_id` + índice.

## Banco de dados (`app/database/database.py`)
- DDL e migrações são IDEMPOTENTES: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`.
- Rode a migração na inicialização; nunca exija passo manual.
- Use os helpers existentes de conexão (`_cursor`, `_placeholder`) em vez de abrir conexão nova. Confira a assinatura no próprio arquivo antes de usar.
- `schema.sql` é só referência; atualize se mudar o schema.

## Tabelas principais
empresas, usuarios, documentos, chunks, clientes, produtos, pipelines, etapas_pipeline, negocios, propostas, contratos, conversas_telegram, historico_conversas, configuracoes_empresa, perguntas_historico, ia_telemetria_execucao, ia_evaluations.
- `usuarios`: id, empresa_id, nome, email, senha_hash, perfil, telegram_chat_id, status.
- `configuracoes_empresa`: chaves BYOK (gemini_api_key, openai_api_key, provedor_ia_padrao), fuso_horario, telegram_bot_token/username/webhook_ativo, ia_prompt_sistema, ia_coletar_dados_obrigatorio, followup_ativo, followup_horas_inatividade, followup_mensagem_personalizada, numero_suporte_humano, mensagem_suporte.
- Para colunas de outras tabelas, leia o DDL em `database.py` (busque `CREATE TABLE IF NOT EXISTS <nome>`).

## RBAC
- `master`: admin global da infraestrutura (painel multiempresa).
- `admin`: administra a empresa (config, documentos, equipe, analytics).
- `colaborador`/`atendente`: CRM e atendimento.
- `cliente`: NÃO é perfil de plataforma; clientes são registros da tabela `clientes`.
- Validação de perfil fica em `security_service.py` (senhas PBKDF2/SHA256). Reaproveite, não duplique.

## Rotas e arquivos
- `app/main.py`: cria app, inclui routers, serve static, inicia APScheduler.
- `routes/auth.py`: POST /auth/login, POST /auth/cadastro-empresa.
- `routes/company.py` + `services/company_service.py`: GET /empresa/configuracoes (chaves mascaradas), POST /empresa/ia-regras, /followup-config, /telegram-bot, /fuso-horario, /provedor-ia.
- `routes/users.py` + `services/user_service.py`: equipe.
- Novo módulo de rotas: criar `routes/<nome>.py` com APIRouter e registrar em `main.py`.

## Segurança
- Chaves de API nunca em texto claro na resposta: sempre mascaradas.
- Segredos só em `.env` (DATABASE_URL, GEMINI_API_KEY, RENDER_API_KEY...). Nunca commitar.
- Aplique checagem de perfil (admin) em rotas de configuração, documentos e analytics.

## Checklist antes de finalizar
1. Query filtra `empresa_id`? 2. Migração idempotente? 3. Perfil checado? 4. Router registrado em main.py? 5. Respondi sem despejar código completo?
