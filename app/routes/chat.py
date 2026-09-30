from fastapi import APIRouter, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.services.semantic_search_service import buscar_chunks_semanticamente
from app.services.ai_service import AIService
from app.services.ai_engine_service import ai_engine
from app.services.ai_evaluation_service import avaliar_interacao_ia
from app.routes.analytics import registrar_pergunta_historico
from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)

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
    session_id: Optional[str] = None


@router.get("/historico-recente")
def obter_historico_recente_usuario(
    usuario_id: int = Query(...),
    empresa_id: Optional[int] = Query(None),
    limite: int = Query(30, ge=1, le=100)
):
    """
    Retorna as conversas recentes daquele usuário logado no portal para exibição na sidebar.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        filtros = [f"usuario_id = {ph}", "canal = 'portal'"]
        params = [usuario_id]
        if empresa_id:
            filtros.append(f"empresa_id = {ph}")
            params.append(empresa_id)

        where = " AND ".join(filtros)
        cursor.execute(f"""
            SELECT id, pergunta, resposta, fonte_resposta, teve_contexto, documentos_utilizados, criado_em
            FROM perguntas_historico
            WHERE {where}
            ORDER BY id DESC
            LIMIT {ph}
        """, (*params, limite))
        linhas = cursor.fetchall()

        itens = []
        for r in linhas:
            docs = []
            if r.get("documentos_utilizados"):
                try:
                    import json
                    docs = json.loads(r["documentos_utilizados"]) if isinstance(r["documentos_utilizados"], str) else r["documentos_utilizados"]
                except Exception:
                    pass
            itens.append({
                "id": r["id"],
                "pergunta": r["pergunta"],
                "resposta": r["resposta"],
                "fonte": r["fonte_resposta"],
                "teve_contexto": bool(r["teve_contexto"]),
                "documentos_consultados": [d.get("nome_arquivo") for d in docs if isinstance(d, dict) and d.get("nome_arquivo")],
                "criado_em": str(r["criado_em"]),
                "modelo_usado": ai_service.primary_model
            })
        return {"total": len(itens), "conversas": itens, "historico": itens}
    finally:
        conexao.close()


@router.get("/conversa/{conversa_id}")
def obter_detalhes_conversa(conversa_id: int):
    """Retorna os detalhes completos de uma conversa/pergunta específica do chat RAG."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT id, usuario_id, empresa_id, pergunta, resposta, fonte_resposta,
                   teve_contexto, documentos_utilizados, criado_em
            FROM perguntas_historico
            WHERE id = {ph}
        """, (conversa_id,))
        r = cursor.fetchone()
        if not r:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")

        docs = []
        if r.get("documentos_utilizados"):
            try:
                import json
                docs = json.loads(r["documentos_utilizados"]) if isinstance(r["documentos_utilizados"], str) else r["documentos_utilizados"]
            except Exception:
                pass

        return {
            "id": r["id"],
            "pergunta": r["pergunta"],
            "resposta": r["resposta"],
            "fonte": r["fonte_resposta"],
            "teve_contexto": bool(r["teve_contexto"]),
            "documentos_consultados": [d.get("nome_arquivo") for d in docs if isinstance(d, dict) and d.get("nome_arquivo")],
            "criado_em": str(r["criado_em"]),
            "modelo_usado": ai_service.primary_model
        }
    finally:
        conexao.close()


@router.post("/")
def conversar(pergunta: Pergunta, background_tasks: BackgroundTasks):
    """
    Chat Interno do Portal (Assistente Híbrido Corporativo):
    - Busca chunks semanticamente nos documentos da empresa (públicos e internos).
    - Se encontrar contexto relevante, responde com base nos documentos corporativos.
    - Se NÃO encontrar contexto, responde utilizando conhecimento geral corporativo.
    - Persiste a interação em perguntas_historico com canal='portal'.
    - Coleta telemetria de tokens, custo e latência via LangChain Callback.
    - Dispara avaliação contínua em segundo plano (RAG Triad & LLM-as-a-judge).
    """
    texto_pergunta = pergunta.mensagem.strip()
    empresa_id = pergunta.empresa_id or 1
    session_id = pergunta.session_id or f"portal_user_{pergunta.usuario_id or 'anon'}"

    # 1. Busca semântica nos documentos da empresa (busca todos: públicos e internos) com tratamento robusto
    chunks = []
    try:
        chunks = buscar_chunks_semanticamente(texto_pergunta, limite=3, empresa_id=empresa_id)
    except Exception as e_busca:
        logger.warning(f"[ChatPortal] Falha na busca semântica: {e_busca}")

    contexto_texto = ""
    documentos_usados = []

    if chunks:
        for chunk in chunks:
            nome_arq = chunk.get("nome_arquivo", "documento")
            # Protege contra similaridade None (chunks vizinhos adicionados por expansão)
            sim_val = chunk.get("similaridade")
            sim = round(float(sim_val), 3) if sim_val is not None else 0.75
            documentos_usados.append({
                "nome_arquivo": nome_arq,
                "similaridade": sim
            })
            contexto_texto += (
                f"\n\nDOCUMENTO: {nome_arq}\n"
                f"{chunk.get('conteudo', '')}"
            )

    # 2. Gera a resposta orquestrada via LangChain com Observabilidade
    telemetria_id = 0
    fonte = "base_conhecimento" if (contexto_texto and len(contexto_texto.strip()) >= 15) else "conhecimento_geral"

    try:
        resposta_texto, telemetria_id = ai_engine.gerar_resposta_orquestrada(
            pergunta=texto_pergunta,
            contexto=contexto_texto,
            empresa_id=empresa_id,
            canal="portal",
            session_id=session_id
        )
    except Exception as e:
        logger.warning(f"[ChatPortal] Falha no ai_engine, fallback direto para ai_service: {e}")
        resposta_texto, fonte = ai_service.gerar_resposta_interna(
            pergunta=texto_pergunta,
            contexto=contexto_texto,
            empresa_id=empresa_id
        )

    teve_contexto = (fonte == "base_conhecimento")

    # 3. Dispara continuous evaluation em background
    if telemetria_id:
        background_tasks.add_task(
            avaliar_interacao_ia,
            telemetria_id=telemetria_id,
            empresa_id=empresa_id,
            pergunta=texto_pergunta,
            resposta=resposta_texto,
            contexto_utilizado=contexto_texto if teve_contexto else None
        )

    # 4. Loga no histórico e analytics
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
        "documentos_consultados": documentos_unicos if teve_contexto else [],
        "modelo_usado": ai_service.primary_model,
        "telemetria_id": telemetria_id,
        "session_id": session_id
    }