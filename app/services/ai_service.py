import os
import time
import logging

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger(__name__)


def montar_system_prompt(config_suporte: dict | None = None) -> str:
    suporte = config_suporte or {}
    telefone = suporte.get("numero_suporte_humano", "(11) 99999-9999")
    mensagem_suporte = suporte.get("mensagem_suporte", "Por favor, entre em contato com nossa equipe de atendimento.")

    return f"""
    Você é o assistente virtual inteligente da FluxIA. Sua missão é responder às 
    dúvidas dos usuários de forma natural, precisa e profissional.

    Diretrizes de Resposta:
    1. Fonte de Informação (RAG Público):
        - Utilize prioritariamente as informações fornecidas no contexto da base de conhecimento pública para responder às perguntas.
        - NUNCA invente procedimentos corporativos, dados cadastrais, regras internas, preços ou informações técnicas ausentes no contexto.

    2. Transição para Atendimento/Suporte Humano:
        - Se a dúvida técnica do usuário não estiver contemplada no contexto fornecido (ou se os documentos correspondentes não forem encontrados/forem de acesso restrito):
            * Seja transparente e educado, informando que essa informação não consta no material disponível no momento.
            * Indique que ele pode falar diretamente com o suporte humano pelo contato: {telefone}.
            * Adicione a recomendação: "{mensagem_suporte}".

    3. Fluidez Conversacional e Encerramento das Respostas:
        - NUNCA finalize suas respostas perguntando "Como posso ajudar você hoje?", "Em que posso te ajudar?" ou qualquer saudação de início de conversa.
        - Para concluir a resposta de forma prestativa e natural, finalize sempre perguntando se o usuário precisa de mais alguma assistência, usando variações como:
            * "Posso ajudar em algo mais?"
            * "Ficou alguma dúvida ou ajudo em algo mais?"
            * "Posso te auxiliar com mais alguma informação?"
        - Se houver histórico recente da conversa, evite saudações repetitivas no início.

    4. Tom e Estilo:
        - Responda sempre em Português do Brasil.
        - Mantenha um tom profissional, amigável e acolhedor.
        - NUNCA use jargões de engenharia de software como "chunk", "embedding" ou "similaridade cosseno".
    """


def montar_system_prompt_interno(config_suporte: dict | None = None) -> str:
    suporte = config_suporte or {}
    telefone = suporte.get("numero_suporte_humano", "(11) 99999-9999")

    return f"""
    Você é o Assistente Interno de IA e Copiloto Operacional da plataforma FluxIA.
    Sua missão é auxiliar colaboradores, gerentes e diretores no dia a dia, respondendo perguntas
    sobre a empresa e auxiliando em tarefas gerais de negócios, análise, redação e suporte.

    Diretrizes de Resposta:
    1. Prioridade para a Base de Conhecimento Interna:
        - Se houver trechos de documentos fornecidos no contexto da empresa, utilize-os como fonte prioritária da verdade.
    2. Assistente Híbrido com Conhecimento Geral:
        - Se a pergunta do colaborador NÃO for encontrada nos documentos internos ou não houver documentos correspondentes, você NÃO DEVE RECUSAR a resposta.
        - Ao invés de recusar, responda utilizando seu amplo conhecimento geral (técnico, comercial, operacional ou de boas práticas).
        - Sempre que responder com conhecimento geral (sem embasamento nos documentos internos da empresa), esclareça de forma natural e profissional:
          "[Nota: Esta informação foi respondida com base em conhecimento geral de mercado, pois não há documento interno registrado sobre este tópico específico]".
    3. Tom e Estilo:
        - Mantenha um tom executivo, objetivo, inteligente e colaborativo.
        - Idioma: Português do Brasil.
        - NUNCA mencione termos técnicos internos de IA como 'chunks', 'embeddings' ou 'limiar de similaridade'.
    """


class AIService:

    def __init__(self):
        API_KEY = os.getenv("GEMINI_API_KEY")

        if not API_KEY:
            raise ValueError(
                "A variável GEMINI_API_KEY não foi encontrada."
            )

        self.client = genai.Client(api_key=API_KEY)
        self.primary_model = "gemini-3.5-flash-lite"
        self.fallback_model = "gemini-3.6-flash"

    def obter_client(self, empresa_id: int | None = None) -> genai.Client:
        """
        Retorna o client do Google Gemini.
        Se a empresa possuir chave própria de API (BYOK) cadastrada em configuracoes_empresa,
        utiliza essa chave; caso contrário, utiliza a chave padrão da plataforma (GEMINI_API_KEY).
        """
        chave = None
        if empresa_id:
            try:
                from app.services.company_service import obter_configuracao_empresa
                cfg = obter_configuracao_empresa(empresa_id)
                chave = cfg.get("gemini_api_key")
            except Exception as e:
                logger.warning(f"[AIService] Falha ao consultar chave da empresa {empresa_id}: {e}")

        if not chave or not chave.strip():
            return self.client

        try:
            return genai.Client(api_key=chave.strip())
        except Exception as e:
            logger.warning(f"[AIService] Erro ao instanciar client com chave da empresa {empresa_id}: {e}. Usando client padrão.")
            return self.client

    def montar_system_prompt(self, config_suporte: dict | None = None) -> str:
        return montar_system_prompt(config_suporte)

    def montar_system_prompt_interno(self, config_suporte: dict | None = None) -> str:
        return montar_system_prompt_interno(config_suporte)

    def gerar_resposta(
        self,
        pergunta: str,
        contexto: str,
        historico: list | None = None,
        config_suporte: dict | None = None,
        empresa_id: int | None = None,
        retries: int = 3,
        delay: int = 2
    ) -> str:
        partes = []

        if contexto and len(contexto.strip()) >= 10:
            partes.append(f"--- CONTEXTO DA BASE DE CONHECIMENTO ---\n{contexto}")
        else:
            partes.append("--- CONTEXTO DA BASE DE CONHECIMENTO ---\n[Nenhum documento público correspondente foi encontrado para esta dúvida]")

        if historico:
            linhas_hist = []
            for interacao in historico:
                usuario_msg = interacao.get("mensagem_usuario", "")
                ia_msg = interacao.get("resposta_ia", "")
                linhas_hist.append(f"Usuário: {usuario_msg}\nAssistente: {ia_msg}")
            partes.append("--- HISTÓRICO RECENTE DA CONVERSA ---\n" + "\n\n".join(linhas_hist))

        partes.append(f"--- PERGUNTA DO USUÁRIO ---\n{pergunta}")
        user_content = "\n\n".join(partes)

        prompt_sistema = self.montar_system_prompt(config_suporte)

        config = types.GenerateContentConfig(
            system_instruction=prompt_sistema,
            temperature=0.3
        )

        models_to_try = [self.primary_model, self.fallback_model]
        client_ativo = self.obter_client(empresa_id)

        for model in models_to_try:
            for tentativa in range(1, retries + 1):
                try:
                    resposta = client_ativo.models.generate_content(
                        model=model,
                        contents=user_content,
                        config=config
                    )

                    if resposta and resposta.text:
                        return resposta.text.strip()
                    else:
                        raise Exception("Resposta vazia retornada pela API.")

                except Exception as e:
                    logger.warning(f"[AIService] Modelo '{model}' - Tentativa {tentativa}/{retries} falhou: {e}")
                    if tentativa < retries:
                        time.sleep(delay)

        telefone = (config_suporte or {}).get("numero_suporte_humano", "(11) 99999-9999")
        return (
            f"Desculpe, nossos serviços de inteligência estão com alta demanda no momento. "
            f"Caso precise de assistência imediata, por favor contate nossa equipe de suporte pelo telefone: {telefone}."
        )

    def gerar_resposta_interna(
        self,
        pergunta: str,
        contexto: str,
        historico: list | None = None,
        config_suporte: dict | None = None,
        empresa_id: int | None = None,
        retries: int = 3,
        delay: int = 2
    ) -> tuple[str, str]:
        """
        Gera resposta para o Chat Interno do Portal.
        Se houver contexto na base, responde como 'base_conhecimento'.
        Se não houver contexto, NÃO recusa: responde como 'conhecimento_geral'.
        Retorna (texto_resposta, fonte_resposta).
        """
        tem_contexto = bool(contexto and len(contexto.strip()) >= 15)
        fonte = "base_conhecimento" if tem_contexto else "conhecimento_geral"

        partes = []
        if tem_contexto:
            partes.append(f"--- CONTEXTO DOS DOCUMENTOS INTERNOS DA EMPRESA ---\n{contexto}")
        else:
            partes.append("--- CONTEXTO DOS DOCUMENTOS INTERNOS DA EMPRESA ---\n[Nenhum documento interno específico encontrado para esta dúvida. Utilize conhecimento geral corporativo.]")

        if historico:
            linhas_hist = []
            for interacao in historico:
                usuario_msg = interacao.get("mensagem_usuario", "")
                ia_msg = interacao.get("resposta_ia", "")
                linhas_hist.append(f"Colaborador: {usuario_msg}\nCopiloto: {ia_msg}")
            partes.append("--- HISTÓRICO RECENTE ---\n" + "\n\n".join(linhas_hist))

        partes.append(f"--- PERGUNTA DO COLABORADOR ---\n{pergunta}")
        user_content = "\n\n".join(partes)

        prompt_sistema = self.montar_system_prompt_interno(config_suporte)

        config = types.GenerateContentConfig(
            system_instruction=prompt_sistema,
            temperature=0.4
        )

        models_to_try = [self.primary_model, self.fallback_model]
        client_ativo = self.obter_client(empresa_id)

        for model in models_to_try:
            for tentativa in range(1, retries + 1):
                try:
                    resposta = client_ativo.models.generate_content(
                        model=model,
                        contents=user_content,
                        config=config
                    )
                    if resposta and resposta.text:
                        return resposta.text.strip(), fonte
                    else:
                        raise Exception("Resposta vazia retornada pela API.")
                except Exception as e:
                    logger.warning(f"[AIService Interno] Modelo '{model}' tentativa {tentativa}/{retries} falhou: {e}")
                    if tentativa < retries:
                        time.sleep(delay)

        return (
            "No momento o serviço de inteligência artificial está temporariamente instável. Por favor, tente novamente em instantes.",
            fonte
        )
