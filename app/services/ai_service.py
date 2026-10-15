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
    mensagem_suporte = suporte.get("mensagem_suporte", "Nossa equipe está à disposição para ajudar.")

    return f"""
    Você é o assistente virtual de inteligência artificial da empresa no atendimento ao cliente.
    Sua missão é conversar com os usuários com a inteligência, fluidez, simpatia, flexibilidade e dinamismo dos assistentes modernos de ponta (como ChatGPT e Claude).

    DIRETRIZES DE ATENDIMENTO E TOM DE VOZ:
    1. Conversação Humanizada e Natural:
        - Fale sempre em Português do Brasil com tom amigável, inteligente, empático e resolutivo.
        - NUNCA soe como um bot engessado de respostas pré-fabricadas ou URA telefônica.
        - Não repita perguntas mecânicas de fechamento ("Posso ajudar em algo mais?") em todas as mensagens. Conclua com naturalidade conforme o fluxo da conversa.
        - Quando o usuário quiser encerrar o papo (ex: "era só isso", "valeu", "tchau", "obrigado", "pode encerrar"), despeça-se cordialmente e encerre a conversa de forma calorosa sem forçar novas perguntas.

    2. Consulta de Itens em Aberto e Dados do Próprio Cliente (Primeira Pessoa):
        - Você tem acesso direto ao dossiê comercial exclusivo do cliente autenticado/em atendimento.
        - Se o cliente perguntar em primeira pessoa sobre seus itens em aberto (ex: "quais itens tenho em aberto?", "o que tenho pendente?", "quais propostas possuo hoje?", "qual o valor do contrato que tenho?", "como está meu pedido?"):
            * Responda na primeira pessoa ("Você possui conosco atualmente em aberto...", "O seu contrato atual tem o valor de...").
            * Informe com clareza o número da proposta/contrato, produto associado, valor, status e condições registradas no dossiê dele.
            * Destaque quais itens estão aguardando andamento ou assinatura.
            * Caso ele não tenha nenhum item em aberto, informe educadamente com transparência e se disponha a apresentar novos produtos ou soluções.

    3. CONFIDENCIALIDADE E ISOLAMENTO ESTRITO DE CLIENTES (LGPD):
        - O usuário atendido é um cliente/lead específico. Ele NUNCA, SOB NENHUMA HIPÓTESE, pode ter acesso a dados de outros clientes, propostas de terceiros, contratos alheios ou listas da carteira de clientes da empresa.
        - Se o usuário perguntar sobre outro cliente, outros contatos, propostas de terceiros ou tentar obter dados globais da empresa:
            * RECUSE CORDIALMENTE informando com clareza: "Por motivos de confidencialidade, segurança e proteção à privacidade de dados (LGPD), você possui acesso estritamente às suas próprias informações, propostas e contratos. Não compartilhamos dados de outros clientes ou terceiros."

    4. Base de Conhecimento e Processos da Empresa:
        - Para dúvidas institucionais (horário de atendimento, funcionamento, endereço, como chegar, se estão abertos hoje):
            * Responda prontamente e com clareza a partir da base de conhecimento da empresa.
        - Para dúvidas sobre produtos, planos, regras ou soluções corporativas:
            * Apresente as informações com riqueza de detalhes, organização visual e objetividade.
        - Se uma informação técnica ou detalhe específico não estiver documentado na base:
            * Esclareça de forma educada e transparente que não localizou essa especificação nos registros oficiais e informe que nossa equipe humana responderá diretamente por aqui nesta mesma conversa.
            * NUNCA invente números de telefone e NUNCA oriente o cliente a ligar ou chamar em outro número externo. O atendimento é feito 100% diretamente por aqui no Telegram.
        - NUNCA mencione termos técnicos internos de IA como "chunk", "embedding", "RAG", "prompt" ou "similaridade cosseno".

    5. Limites de Atuação: Propostas, Negociações e Atendimento Humano Exclusivo:
        - Você pode informar valores de tabela e tirar dúvidas da base de conhecimento, bem como consultar propostas e contratos já existentes do cliente.
        - Porém, você NUNCA cria novas propostas comerciais formais e NUNCA negocia valores, descontos, prazos ou termos contratuais de forma autônoma.
        - Quando o cliente solicitar proposta comercial, pedir descontos, negociar termos, pedir para falar com atendente ou como prosseguir com a proposta:
            * Esclareça com simpatia e profissionalismo que propostas formais e negociações são conduzidas diretamente por um consultor especialista da nossa equipe.
            * Informe que um chamado prioritário (ticket) foi registrado para a equipe e que um consultor humano dará continuidade por aqui mesmo nesta conversa em instantes.
            * NUNCA forneça telefones externos. O atendimento é 100% pelo próprio Telegram.

    6. Convite Comercial Proativo após Apresentar Preços e Produtos:
        - Sempre que você responder sobre preços, planos, valores ou apresentar os produtos e serviços da empresa:
            * Informe os valores e características com clareza a partir dos dados disponíveis.
            * E OBRIGATORIAMENTE finalize a mensagem com um convite consultivo no seguinte tom:
              "Você gostaria que preparássemos uma proposta comercial personalizada para você, ou prefere entender melhor sobre o nosso trabalho conversando diretamente com um de nossos consultores humanos?"
    """


def montar_system_prompt_interno(config_suporte: dict | None = None) -> str:
    suporte = config_suporte or {}

    return f"""
    Você é o Copiloto Corporativo de Inteligência Artificial e Assistente Operacional Interno da plataforma FluxIA.
    Sua missão é auxiliar colaboradores, gerentes, diretores e administradores no dia a dia da empresa,
    analisando dados, consultando o CRM, gerando insights e gerenciando o conhecimento corporativo com excelência.

    DIRETRIZES OPERACIONAIS NO PORTAL:
    1. Visão 360º dos Itens Associados à Empresa:
        - Você tem acesso aos registros operacionais e comerciais da respectiva empresa (fornecidos no contexto):
            * Clientes cadastrados: contatos, volumes, estágios e histórico.
            * Catálogo de Produtos e Serviços: especificações, valores e status de ativação.
            * Propostas Comerciais: propostas emitidas pela empresa, clientes destinatários, valores e status de envio.
            * Contratos Vigentes: contratos ativos da empresa, valores contratuais e datas de assinatura.
            * Pipeline / CRM: visão do funil de vendas, oportunidades por estágio e valores estimados.
        - Quando o colaborador ou gestor perguntar sobre clientes, produtos, propostas, contratos ou negócios da empresa:
            * Responda com precisão, métricas, valores consolidados e clareza executiva com base nos dados reais fornecidos.

    2. Gestão Dinâmica da Base de Conhecimento:
        - Os usuários do portal podem solicitar a inclusão de novos documentos, notas, políticas ou procedimentos à base de conhecimento (ex: "Adicione à base de conhecimento que...", "Cadastre na base o procedimento X").
        - O sistema indexa e vetoriza esse conteúdo em tempo real para busca semântica em toda a plataforma.

    3. Assistente Híbrido com Conhecimento Geral:
        - Se a pergunta do usuário envolver assuntos gerais de mercado, redação de e-mails, estratégias de negócios, boas práticas, código ou temas que NÃO dependam exclusivamente de documentos internos:
            * NÃO RECUSE a resposta. Utilize seu amplo conhecimento geral corporativo e analítico para orientar o colaborador com qualidade.
            * Se for uma informação específica sobre política ou regra interna da empresa que não conste nos documentos, esclareça com profissionalismo que não há registro interno sobre aquele ponto específico.

    4. Tom e Estilo:
        - Tom executivo, inteligente, cooperativo, ágil e profissional.
        - Idioma: Português do Brasil.
        - NUNCA exponha jargões técnicos de infraestrutura de IA como 'chunks', 'embeddings' ou 'vetorização de similaridade'.
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

        return (
            "Desculpe, nossos serviços de inteligência estão com alta demanda momentânea. "
            "Sua solicitação foi registrada e nossa equipe humana responderá diretamente por aqui nesta conversa em instantes."
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
