# FluxIA - Plataforma Inteligente Multitenant

FluxIA é uma plataforma de CRM, atendimento multicanal e inteligência artificial para vendas e atendimento ao cliente desenvolvida com **FastAPI** e **PostgreSQL**.

> **AVISO IMPORTANTE:** O desenvolvimento local e a execução em produção requerem obrigatoriamente um PostgreSQL (via variável de ambiente `DATABASE_URL`). O suporte a SQLite foi completamente removido do projeto.

---

## Requisitos de Ambiente

- **Python 3.10+**
- **PostgreSQL 14+** (local ou remoto, ex: Neon, Render, Supabase)
- **DATABASE_URL** configurada no arquivo `.env`

Exemplo de `.env`:
```env
DATABASE_URL=postgresql://usuario:senha@host:5432/nomedobanco
SECRET_KEY=sua_chave_secreta
ADMIN_API_KEY=sua_chave_administrativa
```

---

## Executando Localmente

1. Clone o repositório e crie o ambiente virtual:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # ou .venv\Scripts\activate no Windows
   pip install -r requirements.txt
   ```

2. Configure a variável `DATABASE_URL` no `.env`.

3. Inicie o servidor FastAPI:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

4. Acesse o portal web no navegador:
   `http://localhost:8000/crm_portal.html`
