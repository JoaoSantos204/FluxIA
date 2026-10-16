import json
import logging
import re
from typing import Optional, List
from fastapi import APIRouter, Query, BackgroundTasks, HTTPException
from pydantic import BaseModel

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
    conversa_id: Optional[int] = None


@router.get("/conversas")
def listar_conversas(
    usuario_id: int = Query(...),
    empresa_id: Optional[int] = Query(None),
    limite: int = Query(50, ge=1, le=100)
):
    """
    Retorna a lista de conversas (threads) do usuário no chat interno do portal.
    Ordenadas pela interação mais recente (estilo ChatGPT/Claude).
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        filtros = [f"c.usuario_id = {ph}"]
        params = [usuario_id]
        if empresa_id:
            filtros.append(f"c.empresa_id = {ph}")
            params.append(empresa_id)

        where = " AND ".join(filtros)
        cursor.execute(f"""
            SELECT 
                c.id, 
                c.titulo, 
                c.criado_em, 
                c.atualizado_em,
                COUNT(m.id) AS total_mensagens
            FROM conversas_chat_portal c
            LEFT JOIN mensagens_chat_portal m ON m.conversa_id = c.id
            WHERE {where}
            GROUP BY c.id
            ORDER BY c.atualizado_em DESC
            LIMIT {ph}
        """, (*params, limite))
        linhas = cursor.fetchall()

        conversas = []
        for r in linhas:
            conversas.append({
                "id": r["id"],
                "titulo": r["titulo"],
                "criado_em": str(r["criado_em"]),
                "atualizado_em": str(r["atualizado_em"]),
                "total_mensagens": int(r["total_mensagens"] or 0)
            })
        return {"total": len(conversas), "conversas": conversas}
    finally:
        conexao.close()


@router.get("/historico-recente")
def obter_historico_recente_usuario(
    usuario_id: int = Query(...),
    empresa_id: Optional[int] = Query(None),
    limite: int = Query(50, ge=1, le=100)
):
    """Endpoint de compatibilidade: retorna a lista de conversas ativas do usuário."""
    return listar_conversas(usuario_id=usuario_id, empresa_id=empresa_id, limite=limite)


@router.get("/conversa/{conversa_id}")
def obter_detalhes_conversa(conversa_id: int):
    """
    Retorna os detalhes completos de uma conversa e TODAS as suas mensagens
    para visualização integral pelo usuário na interface.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT id, empresa_id, usuario_id, titulo, criado_em, atualizado_em
            FROM conversas_chat_portal
            WHERE id = {ph}
        """, (conversa_id,))
        conv = cursor.fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")

        cursor.execute(f"""
            SELECT 
                id, 
                remetente, 
                texto, 
                fonte, 
                teve_contexto, 
                documentos_utilizados, 
                modelo_usado, 
                criado_em
            FROM mensagens_chat_portal
            WHERE conversa_id = {ph}
            ORDER BY id ASC
        """, (conversa_id,))
        linhas_msgs = cursor.fetchall()

        mensagens = []
        for m in linhas_msgs:
            docs = []
            if m.get("documentos_utilizados"):
                try:
                    docs = json.loads(m["documentos_utilizados"]) if isinstance(m["documentos_utilizados"], str) else m["documentos_utilizados"]
                except Exception:
                    pass
            mensagens.append({
                "id": m["id"],
                "remetente": m["remetente"],
                "texto": m["texto"],
                "fonte": m.get("fonte"),
                "teve_contexto": bool(m.get("teve_contexto")),
                "documentos_consultados": [d.get("nome_arquivo") for d in docs if isinstance(d, dict) and d.get("nome_arquivo")],
                "modelo_usado": m.get("modelo_usado") or ai_service.primary_model,
                "criado_em": str(m["criado_em"])
            })

        return {
            "id": conv["id"],
            "titulo": conv["titulo"],
            "empresa_id": conv["empresa_id"],
            "usuario_id": conv["usuario_id"],
            "criado_em": str(conv["criado_em"]),
            "atualizado_em": str(conv["atualizado_em"]),
            "mensagens": mensagens
        }
    finally:
        conexao.close()


@router.delete("/conversa/{conversa_id}")
def excluir_conversa(conversa_id: int):
    """Exclui uma conversa e todo seu histórico de mensagens."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"DELETE FROM conversas_chat_portal WHERE id = {ph} RETURNING id", (conversa_id,))
        deleted = cursor.fetchone()
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")
        conexao.commit()
        return {"status": "ok", "mensagem": "Conversa excluída com sucesso."}
    finally:
        conexao.close()


@router.post("/")
def conversar(pergunta: Pergunta, background_tasks: BackgroundTasks):
    """
    Chat Interno do Portal (Assistente Híbrido Corporativo):
    - Agrupa interações por conversa (threads estilo ChatGPT).
    - Para geração de resposta pelo LLM, utiliza estritamente o limite de contexto de
      até as 5 mensagens anteriores da conversa.
    - Para visualização na tela, persiste e mantém TODAS as mensagens da conversa.
    - Busca chunks semanticamente nos documentos da empresa (públicos e internos).
    - Persiste a interação em perguntas_historico com canal='portal' para analytics e auditoria.
    - Dispara avaliação contínua em segundo plano (RAG Triad & LLM-as-a-judge).
    """
    texto_pergunta = pergunta.mensagem.strip()
    empresa_id = pergunta.empresa_id or 1
    usuario_id = pergunta.usuario_id
    conversa_id = pergunta.conversa_id
    session_id = pergunta.session_id or f"portal_conv_{conversa_id or 'nova'}_{usuario_id or 'anon'}"

    from app.services.knowledge_service import (
        extrair_intencao_adicionar_conhecimento,
        adicionar_conhecimento_empresa,
        obter_resumo_corporativo_empresa
    )

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        # 1. Garante ou cria a conversa
        if conversa_id:
            cursor.execute(f"SELECT id, titulo FROM conversas_chat_portal WHERE id = {ph}", (conversa_id,))
            conversa_atual = cursor.fetchone()
            if not conversa_atual:
                conversa_id = None

        if not conversa_id:
            # Gera título a partir da primeira linha da pergunta (máximo 45 caracteres)
            primeira_linha = texto_pergunta.split("\n")[0].strip()
            titulo_conversa = (primeira_linha[:45] + "...") if len(primeira_linha) > 45 else (primeira_linha or "Nova Conversa")
            cursor.execute(f"""
                INSERT INTO conversas_chat_portal (empresa_id, usuario_id, titulo, criado_em, atualizado_em)
                VALUES ({ph}, {ph}, {ph}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id, titulo
            """, (empresa_id, usuario_id, titulo_conversa))
            nova_conv = cursor.fetchone()
            conversa_id = nova_conv["id"]
            titulo_ativo = nova_conv["titulo"]
        else:
            titulo_ativo = conversa_atual["titulo"]

        # 2. Busca estritamente até as 5 mensagens anteriores da conversa para o LLM
        # Limite deslizante de contexto de 5 mensagens anteriores conforme solicitado pelo usuário
        cursor.execute(f"""
            SELECT remetente, texto
            FROM mensagens_chat_portal
            WHERE conversa_id = {ph}
            ORDER BY id DESC
            LIMIT 5
        """, (conversa_id,))
        ultimas_cinco_raw = cursor.fetchall()
        # Inverte para ordem cronológica (da mais antiga para a mais recente entre as 5)
        historico_contexto_llm = list(reversed([{"remetente": r["remetente"], "texto": r["texto"]} for r in ultimas_cinco_raw]))

        # 3. Salva a pergunta do usuário no banco (armazenando a conversa inteira para visualização)
        cursor.execute(f"""
            INSERT INTO mensagens_chat_portal (conversa_id, remetente, texto, criado_em)
            VALUES ({ph}, 'usuario', {ph}, CURRENT_TIMESTAMP)
            RETURNING id, criado_em
        """, (conversa_id, texto_pergunta))
        msg_user_row = cursor.fetchone()
        msg_user_id = msg_user_row["id"]
        data_user_criado = msg_user_row["criado_em"]

        # Atualiza o timestamp da conversa
        cursor.execute(f"""
            UPDATE conversas_chat_portal SET atualizado_em = CURRENT_TIMESTAMP WHERE id = {ph}
        """, (conversa_id,))
        conexao.commit()

        # 4. Verifica se o usuário solicitou acrescentar documento / nota à base de conhecimento
        intencao_adicionar = extrair_intencao_adicionar_conhecimento(texto_pergunta)
        if intencao_adicionar:
            try:
                res_add = adicionar_conhecimento_empresa(
                    titulo=intencao_adicionar["titulo"],
                    conteudo=intencao_adicionar["conteudo"],
                    empresa_id=empresa_id,
                    nivel_acesso=intencao_adicionar["nivel_acesso"]
                )
                resposta_texto = (
                    f"✅ **Documento adicionado à Base de Conhecimento com sucesso!**\n\n"
                    f"📄 **Título:** `{res_add['titulo']}`\n"
                    f"🔒 **Nível de Acesso:** `{res_add['nivel_acesso'].upper()}`\n"
                    f"🧩 **Total de Chunks Indexados:** `{res_add['total_chunks']}`\n\n"
                    "Essa informação foi vetorizada e já está imediatamente disponível para consultas semânticas no portal e nos atendimentos autorizados."
                )
                fonte = "adicao_conhecimento"
                teve_contexto = True

                cursor.execute(f"""
                    INSERT INTO mensagens_chat_portal 
                    (conversa_id, remetente, texto, fonte, teve_contexto, documentos_utilizados, modelo_usado, criado_em)
                    VALUES ({ph}, 'ia', {ph}, {ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
                    RETURNING id
                """, (conversa_id, resposta_texto, fonte, True, json.dumps([{"nome_arquivo": res_add["titulo"], "similaridade": 1.0}]), ai_service.primary_model))
                msg_ia_row = cursor.fetchone()
                conexao.commit()

                registrar_pergunta_historico(
                    canal="portal",
                    empresa_id=empresa_id,
                    pergunta=texto_pergunta,
                    resposta=resposta_texto,
                    usuario_id=usuario_id,
                    telegram_chat_id=None,
                    documentos_utilizados=[{"nome_arquivo": res_add["titulo"], "similaridade": 1.0}],
                    teve_contexto=True,
                    fonte_resposta=fonte
                )

                return {
                    "conversa_id": conversa_id,
                    "titulo": titulo_ativo,
                    "resposta": resposta_texto,
                    "fonte": fonte,
                    "teve_contexto": True,
                    "documentos_consultados": [res_add["titulo"]],
                    "modelo_usado": ai_service.primary_model,
                    "telemetria_id": 0,
                    "session_id": session_id,
                    "msg_user_id": msg_user_id,
                    "msg_ia_id": msg_ia_row["id"]
                }
            except Exception as e_add:
                logger.error(f"[ChatPortal] Erro ao adicionar à base de conhecimento: {e_add}")
                resposta_erro = f"❌ Houve uma falha ao indexar o documento na base de conhecimento: {e_add}"
                return {
                    "conversa_id": conversa_id,
                    "titulo": titulo_ativo,
                    "resposta": resposta_erro,
                    "fonte": "erro",
                    "teve_contexto": False,
                    "documentos_consultados": [],
                    "modelo_usado": ai_service.primary_model,
                    "telemetria_id": 0,
                    "session_id": session_id,
                    "msg_user_id": msg_user_id,
                    "msg_ia_id": None
                }

        # 5. Busca semântica nos documentos da empresa (públicos e internos)
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

        # 6. Se a pergunta for corporativa, enriquece o contexto com o catálogo/CRM da empresa
        padroes_corporativos = [
            r"\b(clientes?|carteira|lead|leads|contato|contatos)\b",
            r"\b(produtos?|servi[cç]os?|planos?|pre[cç]os?|cat[aá]logo)\b",
            r"\b(propostas?|or[cç]amentos?|valores?\s+de\s+proposta)\b",
            r"\b(contratos?|assinaturas?|contratado|contratados)\b",
            r"\b(neg[oó]cios?|funil|pipeline|crm|oportunidades?|est[aá]gio|vendas?)\b",
            r"\b(faturamento|receita|fechados?|perdidos?)\b",
        ]
        pergunta_eh_corporativa = any(re.search(p, texto_pergunta.lower()) for p in padroes_corporativos)
        if pergunta_eh_corporativa:
            resumo_empresa = obter_resumo_corporativo_empresa(empresa_id, texto_pergunta)
            contexto_texto = f"{resumo_empresa}\n\n{contexto_texto}" if contexto_texto else resumo_empresa

        # 7. Gera a resposta orquestrada via LangChain com Observabilidade
        # Passando estritamente a janela de até 5 mensagens anteriores
        telemetria_id = 0
        fonte = "base_conhecimento" if (contexto_texto and len(contexto_texto.strip()) >= 15) else "conhecimento_geral"

        try:
            resposta_texto, telemetria_id = ai_engine.gerar_resposta_orquestrada(
                pergunta=texto_pergunta,
                contexto=contexto_texto,
                historico=historico_contexto_llm,
                empresa_id=empresa_id,
                canal="portal",
                session_id=session_id
            )
        except Exception as e:
            logger.warning(f"[ChatPortal] Falha no ai_engine, fallback direto para ai_service: {e}")
            resposta_texto, fonte = ai_service.gerar_resposta_interna(
                pergunta=texto_pergunta,
                contexto=contexto_texto,
                historico=historico_contexto_llm,
                empresa_id=empresa_id
            )

        teve_contexto = (fonte == "base_conhecimento")

        # 8. Salva a resposta da IA na conversa (visualização de todas as mensagens mantida)
        cursor.execute(f"""
            INSERT INTO mensagens_chat_portal 
            (conversa_id, remetente, texto, fonte, teve_contexto, documentos_utilizados, modelo_usado, telemetria_id, criado_em)
            VALUES ({ph}, 'ia', {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
            RETURNING id, criado_em
        """, (
            conversa_id,
            resposta_texto,
            fonte,
            teve_contexto,
            json.dumps(documentos_usados) if documentos_usados else None,
            ai_service.primary_model,
            telemetria_id
        ))
        msg_ia_row = cursor.fetchone()
        msg_ia_id = msg_ia_row["id"]
        conexao.commit()

        # 9. Dispara continuous evaluation em background
        if telemetria_id:
            background_tasks.add_task(
                avaliar_interacao_ia,
                telemetria_id=telemetria_id,
                empresa_id=empresa_id,
                pergunta=texto_pergunta,
                resposta=resposta_texto,
                contexto_utilizado=contexto_texto if teve_contexto else None
            )

        # 10. Loga em perguntas_historico para manter retrocompatibilidade com Analytics
        documentos_unicos = list(dict.fromkeys(d["nome_arquivo"] for d in documentos_usados))

        registrar_pergunta_historico(
            canal="portal",
            empresa_id=empresa_id,
            pergunta=texto_pergunta,
            resposta=resposta_texto,
            usuario_id=usuario_id,
            telegram_chat_id=None,
            documentos_utilizados=documentos_usados if teve_contexto else [],
            teve_contexto=teve_contexto,
            fonte_resposta=fonte
        )

        return {
            "conversa_id": conversa_id,
            "titulo": titulo_ativo,
            "resposta": resposta_texto,
            "fonte": fonte,
            "teve_contexto": teve_contexto,
            "documentos_consultados": documentos_unicos if teve_contexto else [],
            "modelo_usado": ai_service.primary_model,
            "telemetria_id": telemetria_id,
            "session_id": session_id,
            "msg_user_id": msg_user_id,
            "msg_ia_id": msg_ia_id
        }
    finally:
        conexao.close()