from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

# 1. Carrega as variáveis do .env assim que o servidor liga
load_dotenv()

from app.database.database import criar_banco
from app.routes.chat import router as chat_router
from app.routes.documents import router as documents_router
from app.routes.telegram import router as telegram_router, configurar_comandos_bot_telegram
from app.routes.company import router as company_router
from app.routes.users import router as users_router
from app.routes.auth import router as auth_router

# Criação da Aplicação (Ponto Central)
app = FastAPI(
    title="FluxIA API",
    description="Back-end do projeto FluxIA",
    version="0.1.0"
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Monta arquivos estáticos
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.on_event("startup")
def iniciar_aplicacao():
    criar_banco()
    configurar_comandos_bot_telegram()

# Registra as rotas da aplicação
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(documents_router)
app.include_router(telegram_router)
app.include_router(company_router)
app.include_router(users_router)


@app.get("/login", response_class=FileResponse)
def tela_login():
    """Tela de Autenticação do Usuário"""
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/", response_class=FileResponse)
def portal_visualizacao():
    """Portal de Visualização e Operação (WhatsApp CRM + RAG)"""
    return FileResponse(STATIC_DIR / "crm_portal.html")


@app.get("/admin", response_class=FileResponse)
def portal_administracao():
    """Portal de Administração de Ambientes e Usuários"""
    return FileResponse(STATIC_DIR / "admin_portal.html")


@app.get("/health")
@app.get("/api")
def api_status():
    return {
        "status": "online",
        "mensagem": "FluxIA API funcionando!"
    }