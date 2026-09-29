import os
import logging
from typing import Optional, List, Dict, Any, Tuple
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.database.database import conectar, _cursor, _placeholder
from app.services.company_service import obter_configuracao_empresa
from app.services.ai_observability_service import AIObservabilityCallbackHandler
from app.services.ai_evaluation_service import avaliar_interacao_ia

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Ferramentas (Tools) do Agente CRM com LangChain
# ---------------------------------------------------------------------------

@tool
def buscar_base_conhecimento(pergunta: str, empresa_id: int = 1) -> str:
    """Busca informações e documentos corporativos na base de conhecimento da empresa via RAG semântico."""
    try:
        from app.services.semantic_search_service import buscar_chunks_semanticamente
        resultados = buscar_chunks_semanticamente(pergunta=pergunta, limite=3, empresa_id=empresa_id)
        if not resultados:
            return "Nenhum documento relevante encontrado na base de conhecimento."
        textos = [f"[Documento: {r.get('nome_arquivo', 'arquivo')}]:\n{r.get('conteudo', '')}" for r in resultados]
        return "\n\n".join(textos)
    except Exception as e:
        logger.error(f"[Tools] Erro ao consultar base de conhecimento: {e}")
        return f"Falha na busca da base de conhecimento: {e}"


@tool
def consultar_status_cliente_crm(telegram_chat_id: str) -> str:
    """Consulta o estágio atual do cliente no funil de vendas (CRM) pelo chat_id do Telegram."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT c.nome, c.telefone, c.email, n.estagio, n.valor_estimado, p.nome AS produto
            FROM clientes c
            LEFT JOIN negocios n ON n.cliente_id = c.id
            LEFT JOIN produtos p ON p.id = n.produto_id
            WHERE c.telegram_chat_id = {ph}
            ORDER BY n.id DESC LIMIT 1
        """, (telegram_chat_id,))
        linha = cursor.fetchone()
        if not linha:
            return f"Nenhum registro de cliente encontrado para chat_id={telegram_chat_id}."
        return (
            f"Cliente: {linha['nome']} | Estágio Funil: {linha['estagio']} | "
            f"Produto Interesse: {linha.get('produto', 'Não definido')} | "
            f"Valor Estimado: R$ {linha.get('valor_estimado', 0)}"
        )
    except Exception as e:
        return f"Erro ao consultar CRM: {e}"
    finally:
        conexao.close()


@tool
def transferir_atendimento_humano(telegram_chat_id: str, motivo: str = "Solicitação do cliente") -> str:
    """Transfere o atendimento do bot para a equipe humana quando solicitado ou em caso de dúvida complexa."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            INSERT INTO conversas_telegram (chat_id, status)
            VALUES ({ph}, 'humano')
            ON CONFLICT (chat_id) DO UPDATE SET status = 'humano'
        """, (telegram_chat_id,))
        conexao.commit()
        return f"Atendimento transferido para a equipe de suporte humano com sucesso! (Motivo: {motivo})"
    except Exception as e:
        conexao.rollback()
        return f"Erro ao transferir para atendente humano: {e}"
    finally:
        conexao.close()


FERRAMENTAS_CRM = [buscar_base_conhecimento, consultar_status_cliente_crm, transferir_atendimento_humano]


# ---------------------------------------------------------------------------
# 2. Multi-Vendor LLM Router com Fallback Automático
# ---------------------------------------------------------------------------

class MultiVendorAIEngine:
    """
    Orquestrador de IA Multi-Vendor (Google Gemini + OpenAI) utilizando LangChain,
    com suporte a failover automático, observabilidade por callback e RAG contínuo.
    """

    def __init__(self):
        self.default_gemini_key = os.getenv("GEMINI_API_KEY")
        self.default_openai_key = os.getenv("OPENAI_API_KEY")

    def obter_llm(
        self,
        empresa_id: int = 1,
        temperature: float = 0.3,
        callbacks: Optional[List[Any]] = None
    ):
        """
        Retorna uma instância de LLM do LangChain configurada com a chave BYOK da empresa
        ou global do sistema, aplicando fallback automático entre Google e OpenAI.
        """
        cfg = obter_configuracao_empresa(empresa_id)
        provedor = (cfg.get("provedor_ia_padrao") or "google").lower()
        gemini_key = cfg.get("gemini_api_key") or self.default_gemini_key
        openai_key = cfg.get("openai_api_key") or self.default_openai_key

        model_gemini = None
        if gemini_key:
            try:
                model_gemini = ChatGoogleGenerativeAI(
                    model="gemini-2.5-flash-lite",
                    google_api_key=gemini_key,
                    temperature=temperature,
                    callbacks=callbacks
                )
            except Exception as e:
                logger.warning(f"[AIEngine] Falha ao criar ChatGoogleGenerativeAI: {e}")

        model_openai = None
        if openai_key:
            try:
                model_openai = ChatOpenAI(
                    model="gpt-4o-mini",
                    openai_api_key=openai_key,
                    temperature=temperature,
                    callbacks=callbacks
                )
            except Exception as e:
                logger.warning(f"[AIEngine] Falha ao criar ChatOpenAI: {e}")

        # Se o provedor preferido for OpenAI e a chave existir:
        if provedor == "openai" and model_openai:
            if model_gemini:
                # OpenAI primário com failover para Gemini
                return model_openai.with_fallbacks([model_gemini])
            return model_openai

        # Provedor padrão é Google Gemini com failover para OpenAI
        if model_gemini:
            if model_openai:
                return model_gemini.with_fallbacks([model_openai])
            return model_gemini

        if model_openai:
            return model_openai

        raise ValueError("Nenhum provedor de IA (Google Gemini ou OpenAI) possui chave de API configurada.")

    def gerar_resposta_orquestrada(
        self,
        pergunta: str,
        contexto: str,
        historico: Optional[List[Dict[str, str]]] = None,
        config_suporte: Optional[Dict[str, Any]] = None,
        empresa_id: int = 1,
        canal: str = "telegram",
        session_id: Optional[str] = None
    ) -> Tuple[str, int]:
        """
        Executa a geração de resposta via LangChain injetando o Callback de Observabilidade.
        Retorna (texto_resposta, telemetria_id).
        """
        from app.services.ai_service import montar_system_prompt

        callback_obs = AIObservabilityCallbackHandler(
            empresa_id=empresa_id,
            canal=canal,
            session_id=session_id
        )

        llm = self.obter_llm(
            empresa_id=empresa_id,
            temperature=0.3,
            callbacks=[callback_obs]
        )

        prompt_sistema = montar_system_prompt(config_suporte)
        mensagens: List[BaseMessage] = [SystemMessage(content=prompt_sistema)]

        # Monta contexto e histórico nas mensagens
        partes_usuario = []
        if contexto and len(contexto.strip()) >= 10:
            partes_usuario.append(f"--- CONTEXTO DA BASE DE CONHECIMENTO ---\n{contexto}")
        else:
            partes_usuario.append("--- CONTEXTO DA BASE DE CONHECIMENTO ---\n[Nenhum documento público correspondente foi encontrado para esta dúvida]")

        if historico:
            for item in historico:
                u_msg = item.get("mensagem_usuario", "")
                ia_msg = item.get("resposta_ia", "")
                if u_msg:
                    mensagens.append(HumanMessage(content=u_msg))
                if ia_msg:
                    mensagens.append(AIMessage(content=ia_msg))

        partes_usuario.append(f"--- PERGUNTA DO USUÁRIO ---\n{pergunta}")
        mensagens.append(HumanMessage(content="\n\n".join(partes_usuario)))

        try:
            resposta = llm.invoke(mensagens)
            texto_final = resposta.content.strip() if hasattr(resposta, "content") else str(resposta).strip()
            telemetria_id = callback_obs.telemetria_id or 0
            return texto_final, telemetria_id
        except Exception as e:
            logger.error(f"[AIEngine] Erro na execução orquestrada com LangChain: {e}")
            # Em caso de erro crítico nos dois provedores, retorna mensagem amigável de suporte
            telefone = (config_suporte or {}).get("numero_suporte_humano", "(11) 99999-9999")
            msg_erro = (
                f"Desculpe, nossos serviços de inteligência estão temporariamente indisponíveis. "
                f"Por favor, entre em contato com nosso atendimento humano pelo telefone: {telefone}."
            )
            return msg_erro, callback_obs.telemetria_id or 0


# Instância global do motor de IA
ai_engine = MultiVendorAIEngine()
