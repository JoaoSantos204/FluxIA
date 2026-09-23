import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

def montar_system_prompt(config_suporte: dict | None = None) -> str:
    suporte = config_suporte or {}
    telefone = suporte.get("numero_suporte_humano", "(11) 99999-9999")
    mensagem_suporte = suporte.get("mensagem_suporte", "Por favor, entre em contato com nossa equipe de atendimento.")

    return f"""
    Você é o assistente virtual inteligente da FluxIA. Sua missão é responder às 
    dúvidas dos usuários de forma natural, precisa e profissional.

    Diretrizes de Resposta:
    1. Fonte de Informação (RAG):
        - Utilize as informações fornecidas no contexto da base de conhecimento para responder às perguntas.
        - NUNCA invente procedimentos corporativos, dados cadastrais, regras internas, preços ou informações técnicas ausentes no contexto.

    2. Transição para Atendimento/Suporte Humano:
        - Se a dúvida técnica do usuário não estiver contemplada no contexto fornecido (ou se os documentos correspondentes não forem encontrados/forem de acesso restrito):
            * Seja transparente e educado, informando que essa informação não consta no material disponível no momento.
            * Indique que ele pode falar diretamente com o suporte humano pelo contato: {telefone}.
            * Adicione a recomendação: "{mensagem_suporte}".

    3. Fluidez Conversacional e Encerramento das Respostas:
        - NUNCA finalize suas respostas perguntando "Como posso ajudar você hoje?", "Em que posso te ajudar?" ou qualquer saudação de início de conversa, pois a interação já está em andamento.
        - Para concluir a resposta de forma prestativa e natural, finalize sempre perguntando se o usuário precisa de mais alguma assistência, usando variações como:
            * "Posso ajudar em algo mais?"
            * "Ficou alguma dúvida ou ajudo em algo mais?"
            * "Posso te auxiliar com mais alguma informação?"
        - Se houver histórico recente da conversa, evite saudações repetitivas no início (como "Olá", "Bom dia", "Tudo bem?"). Vá direto à explicação e encerre com presteza.

    4. Tom e Estilo:
        - Responda sempre em Português do Brasil.
        - Mantenha um tom profissional, amigável e acolhedor.
        - NUNCA use jargões de engenharia de software na resposta como "de acordo com o chunk", "conforme o embedding", "via RAG" ou "na base de dados". Responda de forma humanizada.
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

    def montar_system_prompt(self, config_suporte: dict | None = None) -> str:
        return montar_system_prompt(config_suporte)

    def tratar_mensagem_erro(self, erro: Exception) -> str:
        """
        Analisa a exceção da API e converte para uma mensagem simplificada no log.
        """
        msg_erro = str(erro).lower()

        if "503" in msg_erro or "unavailable" in msg_erro or "high demand" in msg_erro:
            return "Alta demanda temporária nos servidores do modelo."
        elif "404" in msg_erro or "not_found" in msg_erro or "not found" in msg_erro:
            return "Modelo não encontrado ou incompatível com esta versão da API."
        elif "timeout" in msg_erro or "deadline" in msg_erro:
            return "Tempo limite de conexão excedido (Timeout)."
        elif "401" in msg_erro or "403" in msg_erro or "api_key" in msg_erro:
            return "Falha de autenticação (Chave de API inválida ou sem permissão)."
        else:
            return f"Erro inesperado no processamento: {type(erro).__name__}" 

    def gerar_resposta(
        self,
        pergunta: str,
        contexto: str,
        historico: list | None = None,
        config_suporte: dict | None = None,
        retries: int = 3,
        delay: int = 2
    ) -> str:
        """
        Gera uma resposta baseada no contexto e histórico recente utilizando o SDK do Gemini
        com suporte a RBAC, histórico conversacional e transição para suporte humano.
        """
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

        # Tenta modelo por modelo
        for model in models_to_try:
            print(f"[AIService] Testando modelo: {model}")
            for tentativa in range(1, retries + 1):
                try:
                    resposta = self.client.models.generate_content(
                        model=model,
                        contents=user_content,
                        config=config
                    )

                    if resposta and resposta.text:
                        return resposta.text.strip()
                    else:
                        raise Exception("Resposta vazia retornada pela API.")

                except Exception as e:
                    print(f"[AIService] Modelo '{model}' - Tentativa {tentativa}/{retries} falhou: {e}")
                    if tentativa < retries:
                        time.sleep(delay)
                    else:
                        print(f"[AIService] Esgotadas as tentativas para '{model}'. Tentando próximo modelo...")

        # Se TODOS os modelos falharem em TODAS as tentativas, retorna mensagem de fallback com suporte humano
        telefone = (config_suporte or {}).get("numero_suporte_humano", "(11) 99999-9999")
        return (
            f"Desculpe, nossos serviços de inteligência estão com alta demanda no momento. "
            f"Caso precise de assistência imediata, por favor contate nossa equipe de suporte pelo telefone: {telefone}."
        )

