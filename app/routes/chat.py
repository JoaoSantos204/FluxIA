from fastapi import APIRouter
from pydantic import BaseModel

from app.services.semantic_search_service import buscar_chunks_semanticamente
from app.services.ai_service import AIService
from app.database.database import conectar
from app.services.context_service import verificar_contexto

router = APIRouter(
    prefix="/chat",
    tags=["Chat"]
)

# Instância única do serviço de IA
ai_service = AIService()

# Define o formato esperado pelo chatbot.
class Pergunta(BaseModel):
    mensagem: str

def existe_base_de_conhecimento():

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        "SELECT COUNT(*) AS total FROM documentos"
    )

    total_documentos = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) AS total FROM instrucoes"
    )

    total_instrucoes = cursor.fetchone()["total"]

    conexao.close()

    return total_documentos > 0 or total_instrucoes > 0

@router.post("/")

def conversar(pergunta: Pergunta):

    if not existe_base_de_conhecimento():
        return{
            "resposta": (
                "Ainda não existe uma base de conhecimento cadastrada "
                "para que eu possa responder às suas perguntas. "
                "Envie um documento ou adicione instruções para criar "
                "uma base de conhecimento."
            )
        }

    # Busca os chunks semanticamente mais relevantes.
    contexto = buscar_chunks_semanticamente(pergunta.mensagem)

    if not contexto:
        return {
            "resposta": (
                "Não encontrei informações relacionadas à sua pergunta "
                "na base de conhecimento."
            )
        }

    contexto_texto = ""

    for chunk in contexto:

        contexto_texto += (
            f"\n\nDOCUMENTO: {chunk['nome_arquivo']}\n"
            f"{chunk['conteudo']}"
        )

    # Extrai apenas os nomes ÚNICOS dos arquivos mantendo a ordem
    documentos_unicos = list(dict.fromkeys(chunk["nome_arquivo"] for chunk in contexto))

    possui_contexto = verificar_contexto(
        pergunta.mensagem,
        contexto_texto
    )

    if not possui_contexto:
        return {
            "resposta": (
                "Não há informações suficientes na base de conhecimento "
                "para responder a essa pergunta."
            ),
            "documentos_consultados": documentos_unicos
        }

    resposta = ai_service.gerar_resposta(
        pergunta=pergunta.mensagem,
        contexto=contexto_texto
    )

    return {
        "resposta": resposta,
        "documentos_consultados": documentos_unicos
    }