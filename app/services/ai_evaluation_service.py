import os
import json
import logging
from typing import Optional, Dict, Any

from app.database.database import conectar, _cursor, _placeholder
from app.services.company_service import obter_configuracao_empresa

logger = logging.getLogger(__name__)

PROMPT_EVALUATOR_SYSTEM = """
Você é um Engenheiro de IA e Especialista em Avaliação Contínua de Sistemas RAG e LLMs (LLM-as-a-Judge).
Sua função é avaliar com rigor técnico a qualidade de uma interação entre um Usuário e um Assistente de IA.

Avalie os seguintes 3 pilares da RAG Triad (notas de 0.0 a 1.0):
1. fidelidade (Faithfulness / Groundedness):
   - A resposta se baseia estritamente nos fatos apresentados no Contexto fornecido?
   - Se houver contexto e a IA inventar fatos, procedimentos, números ou políticas ausentes, a nota de fidelidade DEVE ser baixa (< 0.5) e alucinação deve ser True.
   - Se não houver contexto fornecido (conhecimento geral), avalie se a resposta foi consistente e transparente.

2. relevancia_resposta (Answer Relevance):
   - A resposta responde diretamente ao que o usuário perguntou?
   - É concisa, clara e útil? (1.0 = perfeita, 0.0 = totalmente desconexa).

3. relevancia_contexto (Context Relevance):
   - Os trechos recuperados da base de conhecimento eram realmente pertinentes para a dúvida do usuário?
   - Se nenhum contexto foi fornecido, atribua 1.0.

Você DEVE responder EXCLUSIVAMENTE em formato JSON válido, sem blocos markdown adicionais, no seguinte formato:
{
  "score_fidelidade": 0.95,
  "score_relevancia_resposta": 0.90,
  "score_relevancia_contexto": 0.85,
  "possivel_alucinacao": false,
  "justificativa": "Breve justificativa técnica da avaliação (máximo 2 frases)."
}
"""


def salvar_avaliacao_no_banco(
    telemetria_id: Optional[int],
    empresa_id: int,
    pergunta: str,
    resposta: str,
    contexto_utilizado: Optional[str],
    eval_resultado: Dict[str, Any],
    avaliador_modelo: str = "gemini-2.5-flash-lite"
) -> int:
    """Persiste a avaliação na tabela ia_evaluations do banco de dados (Neon Postgres)."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    score_fid = float(eval_resultado.get("score_fidelidade", 1.0))
    score_rel_resp = float(eval_resultado.get("score_relevancia_resposta", 1.0))
    score_rel_ctx = float(eval_resultado.get("score_relevancia_contexto", 1.0))
    possivel_alucinacao = bool(eval_resultado.get("possivel_alucinacao", False))
    justificativa = eval_resultado.get("justificativa", "")

    try:
        query = f"""
            INSERT INTO ia_evaluations (
                telemetria_id, empresa_id, pergunta, resposta,
                contexto_utilizado, score_fidelidade, score_relevancia_resposta,
                score_relevancia_contexto, possivel_alucinacao, justificativa_avaliacao,
                avaliador_modelo
            ) VALUES (
                {ph}, {ph}, {ph}, {ph},
                {ph}, {ph}, {ph},
                {ph}, {ph}, {ph},
                {ph}
            )
        """
        from app.database.database import USAR_POSTGRES
        if USAR_POSTGRES:
            query += " RETURNING id"

        valores = (
            telemetria_id, empresa_id, pergunta, resposta,
            contexto_utilizado or "", score_fid, score_rel_resp,
            score_rel_ctx, possivel_alucinacao, justificativa,
            avaliador_modelo
        )

        cursor.execute(query, valores)
        if USAR_POSTGRES:
            res = cursor.fetchone()
            eval_id = res["id"] if isinstance(res, dict) else res[0]
        else:
            eval_id = cursor.lastrowid

        conexao.commit()
        logger.info(f"[AIEvaluation] Avaliação salva #{eval_id} (Fidelidade: {score_fid}, Alucinação: {possivel_alucinacao})")
        return eval_id
    except Exception as e:
        conexao.rollback()
        logger.error(f"[AIEvaluation] Erro ao persistir avaliação: {e}")
        return 0
    finally:
        conexao.close()


def avaliar_interacao_ia(
    telemetria_id: Optional[int],
    empresa_id: int,
    pergunta: str,
    resposta: str,
    contexto_utilizado: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executa a avaliação contínua em segundo plano utilizando LLM-as-a-Judge.
    Projetado para ser chamado via FastAPI BackgroundTasks (latência zero para o cliente).
    """
    logger.info(f"[AIEvaluation] Iniciando avaliação online para empresa={empresa_id}, telemetria={telemetria_id}...")

    # Recupera chave da empresa ou chave global
    cfg = obter_configuracao_empresa(empresa_id)
    gemini_key = cfg.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")
    openai_key = cfg.get("openai_api_key") or os.getenv("OPENAI_API_KEY")

    prompt_analise = f"""
    Pergunta do Usuário:
    "{pergunta}"

    Contexto Fornecido pela Base de Conhecimento (RAG):
    \"\"\"{contexto_utilizado or "Nenhum contexto recuperado (busca retornou vazia ou conhecimento geral)"}\"\"\"

    Resposta Gerada pela IA:
    \"\"\"{resposta}\"\"\"
    """

    eval_json = None
    avaliador_modelo = "gemini-2.5-flash-lite"

    # Tenta via Gemini primeiro
    if gemini_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm_judge = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash-lite",
                google_api_key=gemini_key,
                temperature=0.0
            )
            resp = llm_judge.invoke([
                SystemMessage(content=PROMPT_EVALUATOR_SYSTEM),
                HumanMessage(content=prompt_analise)
            ])
            conteudo = resp.content.strip()
            # Remove blocos markdown se existirem
            if conteudo.startswith("```"):
                linhas = conteudo.split("\n")
                if linhas[0].startswith("```"):
                    linhas = linhas[1:]
                if linhas and linhas[-1].startswith("```"):
                    linhas = linhas[:-1]
                conteudo = "\n".join(linhas).strip()
            eval_json = json.loads(conteudo)
            avaliador_modelo = "gemini-2.5-flash-lite"
        except Exception as e:
            logger.warning(f"[AIEvaluation] Falha no avaliador Gemini: {e}")

    # Fallback para OpenAI se Gemini falhar ou se OpenAI for o provedor ativo
    if not eval_json and openai_key:
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm_judge = ChatOpenAI(
                model="gpt-4o-mini",
                openai_api_key=openai_key,
                temperature=0.0
            )
            resp = llm_judge.invoke([
                SystemMessage(content=PROMPT_EVALUATOR_SYSTEM),
                HumanMessage(content=prompt_analise)
            ])
            conteudo = resp.content.strip()
            if conteudo.startswith("```"):
                linhas = conteudo.split("\n")
                if linhas[0].startswith("```"):
                    linhas = linhas[1:]
                if linhas and linhas[-1].startswith("```"):
                    linhas = linhas[:-1]
                conteudo = "\n".join(linhas).strip()
            eval_json = json.loads(conteudo)
            avaliador_modelo = "gpt-4o-mini"
        except Exception as e:
            logger.warning(f"[AIEvaluation] Falha no avaliador OpenAI: {e}")

    # Se nenhum LLM respondeu a avaliação, usa heurística segura padrão
    if not eval_json:
        logger.warning("[AIEvaluation] Usando fallback heurístico para avaliação.")
        eval_json = {
            "score_fidelidade": 1.0,
            "score_relevancia_resposta": 0.9,
            "score_relevancia_contexto": 1.0 if not contexto_utilizado else 0.8,
            "possivel_alucinacao": False,
            "justificativa": "Avaliação gerada via fallback heurístico operacional."
        }
        avaliador_modelo = "heuristic-evaluator"

    # Salva no banco de dados Neon
    salvar_avaliacao_no_banco(
        telemetria_id=telemetria_id,
        empresa_id=empresa_id,
        pergunta=pergunta,
        resposta=resposta,
        contexto_utilizado=contexto_utilizado,
        eval_resultado=eval_json,
        avaliador_modelo=avaliador_modelo
    )

    return eval_json
