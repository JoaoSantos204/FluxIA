from fastapi import FastAPI
from dotenv import load_dotenv

# 1. Carrega as variáveis do .env assim que o servidor liga
load_dotenv()

from app.database.database import criar_banco
from app.routes.chat import router as chat_router
from app.routes.documents import router as documents_router
from app.routes.telegram import router as telegram_router, configurar_comandos_bot_telegram
from app.routes.company import router as company_router
from app.routes.users import router as users_router

# Criação da Aplicação (Ponto Central)
app = FastAPI(
    title="FluxIA API",
    description="Back-end do projeto FluxIA",
    version="0.1.0"
)

@app.on_event("startup")
def iniciar_aplicacao():
    criar_banco()
    configurar_comandos_bot_telegram()

# Registra as rotas da aplicação
app.include_router(chat_router)
app.include_router(documents_router)
app.include_router(telegram_router)
app.include_router(company_router)
app.include_router(users_router)

@app.get("/")
def inicio():
    return {
        "mensagem": "FluxIA API funcionando!"
    }