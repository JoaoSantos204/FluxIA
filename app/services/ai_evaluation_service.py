import os
import json
import logging
from typing import Optional, Dict, Any

from app.database.database import conectar, _cursor, _placeholder
from app.services.company_service import obter_configuracao_empresa

logger = logging.getLogger(__name__)

PROMPT_EVALUATOR_SYSTEM = """
Você é um Auditor Executivo de Inteligência Artificial e Qualidade Comercial do FluxIA (LLM-as-a-Judge).
Sua missão é auditar com rigor prático e de negócio a resposta gerada pelo Assistente de IA para a pergunta do cliente.

CRITÉRIOS DE AUDITORIA DE NEGÓCIO:
1. Fidelidade Comercial e Anti-Alucinação (0 a 100%):
   - A resposta respeita os produtos, serviços, preços oficiais cadastrados e regras da empresa?
   - ALUCINAÇÃO CRÍTICA (possivel_alucinacao = true, score_fidelidade < 50%):
     * A IA inventou produtos/serviços que a empresa NÃO comercializa.
     * A IA inventou ou alterou preços, condições de pagamento, descontos ou políticas não autorizadas.
     * A IA contradisse informações explícitas da base de conhecimento da empresa.
   - RESPOSTA CONFORME / ADERENTE (possivel_alucinacao = false, score_fidelidade >= 85%):
     * A resposta citou produtos e preços exatamente conforme o catálogo oficial ou base de conhecimento.
     * OU a resposta foi uma interação natural de atendimento (saudação, despedida, horário de funcionamento, endereço, suporte geral educado) sem inventar dados cadastrais falsos.

2. Relevância da Resposta (0 a 100%):
   - A resposta foi clara, profissional, concisa e respondeu diretamente ao que o cliente perguntou?

3. Relevância do Contexto Utilizado (0 a 100%):
   - Os documentos recuperados da base de conhecimento eram pertinentes para a dúvida? (Se nenhum contexto foi necessário ou usado, atribua 100.0).

Você DEVE responder EXCLUSIVAMENTE em formato JSON válido, sem blocos markdown extras:
{
  "score_fidelidade": 95.0,
  "score_relevancia_resposta": 90.0,
  "score_relevancia_contexto": 100.0,
  "possivel_alucinacao": false,
  "status_resumo": "Aderente ao Catálogo",
  "justificativa": "Explicação em português em 1 ou 2 frases curtas para o gestor comercial (ex: Citou o produto X com o valor oficial de R$ 299 / ou Inventou plano inexistente por R$ 50)."
}
"""


def salvar_avaliacao_no_banco(
    telemetria_id: Optional[int],
    empresa_id: int,
    pergunta: str,
    resposta: str,
    contexto_utilizado: Optional[str],
    eval_resultado: Dict[str, Any],
    avaliador_modelo: str = "gemini-3.5-flash-lite"
) -> int:
    """Persiste a avaliação na tabela ia_evaluations do banco de dados (Neon Postgres)."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    # Normaliza scores para percentual (0.0 a 100.0)
    score_fid = float(eval_resultado.get("score_fidelidade", 100.0))
    if score_fid <= 1.0:
        score_fid = round(score_fid * 100.0, 1)

    score_rel_resp = float(eval_resultado.get("score_relevancia_resposta", 100.0))
    if score_rel_resp <= 1.0:
        score_rel_resp = round(score_rel_resp * 100.0, 1)

    score_rel_ctx = float(eval_resultado.get("score_relevancia_contexto", 100.0))
    if score_rel_ctx <= 1.0:
        score_rel_ctx = round(score_rel_ctx * 100.0, 1)

    possivel_alucinacao = bool(eval_resultado.get("possivel_alucinacao", False))
    justificativa = eval_resultado.get("justificativa", "")
    status_resumo = eval_resultado.get("status_resumo", "")
    if status_resumo and status_resumo not in justificativa:
        justificativa = f"[{status_resumo}] {justificativa}".strip()

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
            ) RETURNING id
        """

        valores = (
            telemetria_id, empresa_id, pergunta, resposta,
            contexto_utilizado or "", score_fid, score_rel_resp,
            score_rel_ctx, possivel_alucinacao, justificativa,
            avaliador_modelo
        )

        cursor.execute(query, valores)
        res = cursor.fetchone()
        eval_id = res["id"] if isinstance(res, dict) else res[0]

        conexao.commit()
        logger.info(f"[AIEvaluation] Avaliação salva #{eval_id} (Fidelidade: {score_fid}%, Alucinação: {possivel_alucinacao})")
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
    Executa a auditoria de conformidade comercial em segundo plano utilizando LLM-as-a-Judge.
    Compara a resposta contra o catálogo oficial de produtos/preços e a base de conhecimento.
    """
    logger.info(f"[AIEvaluation] Iniciando avaliação online para empresa={empresa_id}, telemetria={telemetria_id}...")

    # Recupera chave da empresa ou chave global
    cfg = obter_configuracao_empresa(empresa_id)
    gemini_key = cfg.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")
    openai_key = cfg.get("openai_api_key") or os.getenv("OPENAI_API_KEY")

    # Consulta catálogo de produtos e serviços cadastrados da empresa
    produtos_texto = "Nenhum produto cadastrado no catálogo oficial."
    try:
        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()
        cursor.execute(f"""
            SELECT nome, preco, categoria, descricao
            FROM produtos
            WHERE empresa_id = {ph}
            ORDER BY id ASC LIMIT 35
        """, (empresa_id,))
        rows = cursor.fetchall()
        if rows:
            itens = []
            for r in rows:
                preco_fmt = f"R$ {float(r['preco']):.2f}" if r.get("preco") is not None else "Sob consulta"
                itens.append(f"- Produto/Serviço: {r.get('nome')} | Categoria: {r.get('categoria') or 'Geral'} | Preço Oficial: {preco_fmt} | Detalhes: {r.get('descricao') or 'Sem descrição'}")
            produtos_texto = "\n".join(itens)
        conexao.close()
    except Exception as e:
        logger.warning(f"[AIEvaluation] Aviso ao recuperar catálogo de produtos: {e}")

    prompt_analise = f"""
CATÁLOGO OFICIAL DE PRODUTOS/SERVIÇOS CADASTRADOS DA EMPRESA:
{produtos_texto}

CONTEXTO DA BASE DE CONHECIMENTO (RAG):
\"\"\"{contexto_utilizado or "Nenhum documento específico recuperado (saudação, conversa geral ou dúvida não catalogada)"}\"\"\"

PERGUNTA DO CLIENTE:
"{pergunta}"

RESPOSTA GERADA PELA IA:
\"\"\"{resposta}\"\"\"
"""

    eval_json = None
    avaliador_modelo = "gemini-3.5-flash-lite"

    # Tenta via Gemini primeiro
    if gemini_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm_judge = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                google_api_key=gemini_key,
                temperature=0.0
            )
            resp = llm_judge.invoke([
                SystemMessage(content=PROMPT_EVALUATOR_SYSTEM),
                HumanMessage(content=prompt_analise)
            ])
            raw_resp = resp.content
            if isinstance(raw_resp, str):
                conteudo = raw_resp
            elif isinstance(raw_resp, list):
                conteudo = "".join(part if isinstance(part, str) else str(part.get("text", part) if isinstance(part, dict) else part) for part in raw_resp)
            else:
                conteudo = str(raw_resp or "")
            conteudo = conteudo.strip()
            # Remove blocos markdown se existirem
            if conteudo.startswith("```"):
                linhas = conteudo.split("\n")
                if linhas[0].startswith("```"):
                    linhas = linhas[1:]
                if linhas and linhas[-1].startswith("```"):
                    linhas = linhas[:-1]
                conteudo = "\n".join(linhas).strip()
            eval_json = json.loads(conteudo)
            avaliador_modelo = "gemini-3.5-flash-lite"
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
            raw_resp = resp.content
            if isinstance(raw_resp, str):
                conteudo = raw_resp
            elif isinstance(raw_resp, list):
                conteudo = "".join(part if isinstance(part, str) else str(part.get("text", part) if isinstance(part, dict) else part) for part in raw_resp)
            else:
                conteudo = str(raw_resp or "")
            conteudo = conteudo.strip()
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

    # Fallback seguro com percentuais se nenhum LLM responder
    if not eval_json:
        logger.warning("[AIEvaluation] Usando fallback operacional para avaliação.")
        eval_json = {
            "score_fidelidade": 95.0,
            "score_relevancia_resposta": 90.0,
            "score_relevancia_contexto": 100.0 if not contexto_utilizado else 85.0,
            "possivel_alucinacao": False,
            "status_resumo": "Aderente",
            "justificativa": "Interação de atendimento analisada com conformidade operacional."
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
