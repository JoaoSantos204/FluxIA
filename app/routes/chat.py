from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List

from app.services.semantic_search_service import buscar_chunks_semanticamente
from app.services.ai_service import AIService
from app.routes.analytics import registrar_pergunta_historico

router = APIRouter(
    prefix="/chat",
    tags=["Chat"]
)

# Instância do serviço de IA
ai_service = AIService()


class Pergunta(BaseModel):
    mensagem: str
    empresa_id: Optional[int] = 1
    usuario_id: Optional[int] = None


@router.post("/")
def conversar(pergunta: Pergunta):
    """
    Chat Interno do Portal (Assistente Híbrido Corporativo):
    - Busca chunks semanticamente nos documentos da empresa (públicos e internos).
    - Se encontrar contexto relevante, responde com base nos documentos corporativos.
    - Se NÃO encontrar contexto, NÃO RECUSA: responde utilizando conhecimento geral corporativo,
      esclarecendo que a resposta não provém dos documentos internos.
    - Persiste a interação em perguntas_historico com canal='portal'.
    """
    texto_pergunta = pergunta.mensagem.strip()
    empresa_id = pergunta.empresa_id or 1

    # 1. Busca semântica nos documentos da empresa (busca todos: públicos e internos)
    chunks = buscar_chunks_semanticamente(texto_pergunta, limite=3, empresa_id=empresa_id)

    contexto_texto = ""
    documentos_usados = []

    if chunks:
        for chunk in chunks:
            nome_arq = chunk.get("nome_arquivo", "documento")
            sim = round(float(chunk.get("similaridade", 0.0)), 3)
            documentos_usados.append({
                "nome_arquivo": nome_arq,
                "similaridade": sim
            })
            contexto_texto += (
                f"\n\nDOCUMENTO: {nome_arq}\n"
                f"{chunk.get('conteudo', '')}"
            )

    # 2. Gera a resposta com o modelo de IA interno híbrido
    resposta_texto, fonte = ai_service.gerar_resposta_interna(
        pergunta=texto_pergunta,
        contexto=contexto_texto
    )

    teve_contexto = (fonte == "base_conhecimento")

    # 3. Loga no histórico e analytics
    documentos_unicos = list(dict.fromkeys(d["nome_arquivo"] for d in documentos_usados))

    registrar_pergunta_historico(
        canal="portal",
        empresa_id=empresa_id,
        pergunta=texto_pergunta,
        resposta=resposta_texto,
        usuario_id=pergunta.usuario_id,
        telegram_chat_id=None,
        documentos_utilizados=documentos_usados if teve_contexto else [],
        teve_contexto=teve_contexto,
        fonte_resposta=fonte
    )

    return {
        "resposta": resposta_texto,
        "fonte": fonte,
        "teve_contexto": teve_contexto,
        "documentos_consultados": documentos_unicos if teve_contexto else []
    }