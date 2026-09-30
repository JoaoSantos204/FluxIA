import time
import json
import logging
from typing import Any, Dict, List, Optional
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)

# Tabela de preços aproximada por 1 milhão de tokens (USD)
TABELA_CUSTOS_USD = {
    "google": {
        "gemini-3.5-flash-lite": {"prompt": 0.075, "completion": 0.30},
        "gemini-2.5-flash-lite": {"prompt": 0.075, "completion": 0.30},
        "gemini-2.5-flash": {"prompt": 0.15, "completion": 0.60},
        "default": {"prompt": 0.075, "completion": 0.30}
    },
    "openai": {
        "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
        "gpt-4o": {"prompt": 2.50, "completion": 10.00},
        "default": {"prompt": 0.15, "completion": 0.60}
    }
}


def calcular_custo_estimado(vendor: str, modelo: str, tokens_prompt: int, tokens_completion: int) -> float:
    """Calcula o custo estimado em USD com base na quantidade de tokens e provedor/modelo."""
    v_norm = (vendor or "google").lower()
    tabela_vendor = TABELA_CUSTOS_USD.get(v_norm, TABELA_CUSTOS_USD["google"])
    
    precos = tabela_vendor.get(modelo, tabela_vendor["default"])
    custo_prompt = (tokens_prompt / 1_000_000.0) * precos["prompt"]
    custo_comp = (tokens_completion / 1_000_000.0) * precos["completion"]
    return round(custo_prompt + custo_comp, 7)


def registrar_telemetria(
    empresa_id: int,
    canal: str = "telegram",
    session_id: Optional[str] = None,
    vendor: str = "google",
    modelo: str = "gemini-3.5-flash-lite",
    tokens_prompt: int = 0,
    tokens_completion: int = 0,
    latencia_ms: int = 0,
    status_execucao: str = "sucesso",
    tools_executadas: Optional[List[Dict[str, Any]]] = None,
    mensagem_erro: Optional[str] = None
) -> int:
    """
    Insere registro detalhado de telemetria na tabela ia_telemetria_execucao.
    Retorna o ID do registro gerado.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    tokens_total = (tokens_prompt or 0) + (tokens_completion or 0)
    custo = calcular_custo_estimado(vendor, modelo, tokens_prompt or 0, tokens_completion or 0)
    tools_json = json.dumps(tools_executadas, ensure_ascii=False) if tools_executadas else None

    telemetria_id = 0
    try:
        query = f"""
            INSERT INTO ia_telemetria_execucao (
                empresa_id, canal, session_id, vendor, modelo,
                tokens_prompt, tokens_completion, tokens_total,
                custo_estimado_usd, latencia_ms, status_execucao,
                tools_executadas, mensagem_erro
            ) VALUES (
                {ph}, {ph}, {ph}, {ph}, {ph},
                {ph}, {ph}, {ph},
                {ph}, {ph}, {ph},
                {ph}, {ph}
            )
        """
        # Em Postgres usa RETURNING id
        from app.database.database import USAR_POSTGRES
        if USAR_POSTGRES:
            query += " RETURNING id"

        valores = (
            empresa_id, canal, session_id, vendor, modelo,
            tokens_prompt or 0, tokens_completion or 0, tokens_total,
            custo, latencia_ms, status_execucao,
            tools_json, mensagem_erro
        )

        cursor.execute(query, valores)
        if USAR_POSTGRES:
            res = cursor.fetchone()
            telemetria_id = res["id"] if isinstance(res, dict) else res[0]
        else:
            telemetria_id = cursor.lastrowid

        conexao.commit()
        logger.info(f"[AIObservability] Telemetria registrada #{telemetria_id}: {vendor}/{modelo} ({latencia_ms}ms, {tokens_total} tok, ${custo} USD)")
        return telemetria_id
    except Exception as e:
        conexao.rollback()
        logger.error(f"[AIObservability] Falha ao gravar telemetria: {e}")
        return 0
    finally:
        conexao.close()


class AIObservabilityCallbackHandler(BaseCallbackHandler):
    """
    LangChain Callback Handler customizado para captura de métricas em tempo real (tracing de execução,
    latência, consumo de tokens, custo e ferramentas chamadas).
    """

    def __init__(self, empresa_id: int, canal: str = "telegram", session_id: Optional[str] = None):
        super().__init__()
        self.empresa_id = empresa_id
        self.canal = canal
        self.session_id = session_id

        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.vendor: str = "google"
        self.modelo: str = "gemini-3.5-flash-lite"
        self.tokens_prompt: int = 0
        self.tokens_completion: int = 0
        self.tools_chamadas: List[Dict[str, Any]] = []
        self.status: str = "sucesso"
        self.mensagem_erro: Optional[str] = None
        self.telemetria_id: Optional[int] = None

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> None:
        self.start_time = time.time()
        # Identifica modelo/vendor através dos metadados da invocação
        invoc_params = kwargs.get("invocation_params", {})
        if "model" in invoc_params:
            self.modelo = invoc_params["model"]
            if "gpt" in self.modelo:
                self.vendor = "openai"
        elif "model_name" in invoc_params:
            self.modelo = invoc_params["model_name"]
            if "gpt" in self.modelo:
                self.vendor = "openai"

    def on_tool_start(
        self, serialized: Dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        tool_name = serialized.get("name", "tool")
        self.tools_chamadas.append({
            "tool": tool_name,
            "input": input_str,
            "inicio": time.time()
        })

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        if self.tools_chamadas:
            ultimo = self.tools_chamadas[-1]
            ultimo["output"] = str(output)[:500] # truncar para evitar payload gigante
            ultimo["duracao_s"] = round(time.time() - ultimo.get("inicio", time.time()), 3)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self.status = "erro"
        self.mensagem_erro = str(error)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        self.end_time = time.time()
        latencia_ms = int((self.end_time - self.start_time) * 1000) if self.start_time else 0

        # Tentar extrair contagem de tokens de forma unificada
        try:
            if response.llm_output:
                token_usage = response.llm_output.get("token_usage") or response.llm_output.get("usage")
                if token_usage:
                    self.tokens_prompt = getattr(token_usage, "prompt_tokens", None) or token_usage.get("prompt_tokens") or 0
                    self.tokens_completion = getattr(token_usage, "completion_tokens", None) or token_usage.get("completion_tokens") or 0
            
            # Se não encontrou no llm_output, inspeciona as gerações
            if not self.tokens_prompt and response.generations:
                for gen_list in response.generations:
                    for gen in gen_list:
                        info = getattr(gen, "generation_info", {}) or {}
                        usage = info.get("usage_metadata") or info.get("token_usage") or {}
                        if usage:
                            self.tokens_prompt = usage.get("prompt_token_count") or usage.get("input_tokens") or self.tokens_prompt
                            self.tokens_completion = usage.get("candidates_token_count") or usage.get("output_tokens") or self.tokens_completion
        except Exception as e:
            logger.warning(f"[AIObservability] Aviso ao extrair tokens: {e}")

        # Grava a telemetria automaticamente no banco Neon
        self.telemetria_id = registrar_telemetria(
            empresa_id=self.empresa_id,
            canal=self.canal,
            session_id=self.session_id,
            vendor=self.vendor,
            modelo=self.modelo,
            tokens_prompt=self.tokens_prompt,
            tokens_completion=self.tokens_completion,
            latencia_ms=latencia_ms,
            status_execucao=self.status,
            tools_executadas=self.tools_chamadas,
            mensagem_erro=self.mensagem_erro
        )
