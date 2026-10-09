import os
import re
import json
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from app.database.database import conectar, _cursor, _placeholder
from app.services.company_service import obter_configuracao_empresa
from app.services.ai_service import AIService
from app.services.history_service import salvar_interacao, obter_ultimas_interacoes

logger = logging.getLogger(__name__)


def is_nome_generico(nome: Optional[str]) -> bool:
    """Valida se o nome é genérico ou provisório (não é o nome real informado pelo cliente)."""
    if not nome:
        return True
    n = nome.strip()
    if len(n) < 2:
        return True
    n_lower = n.lower()
    if (
        n_lower.startswith("usuário #") or
        n_lower.startswith("usuario #") or
        n_lower.startswith("lead telegram") or
        n_lower.startswith("lead #") or
        n_lower.startswith("lead ") or
        n.startswith("@") or
        re.match(r"^\d+$", n) or
        "?" in n or
        n_lower in ["muito obrigado", "obrigado", "valeu", "bom dia", "boa tarde", "boa noite", "olá", "ola", "oi", "sim", "não", "nao"]
    ):
        return True
    return False


def is_telefone_valido(telefone: Optional[str]) -> bool:
    """Valida se o telefone possui ao menos 8 dígitos (padrão Brasil celular/fixo)."""
    if not telefone:
        return False
    digitos = re.sub(r"\D", "", str(telefone))
    return len(digitos) >= 8


def formatar_telefone_br(telefone: str) -> str:
    """Formata telefone brasileiro para padrão legível (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    digitos = re.sub(r"\D", "", str(telefone))
    if digitos.startswith("55") and len(digitos) in [12, 13]:
        digitos = digitos[2:]
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    elif len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return telefone.strip()


class AgentOrchestrator:
    """
    Agente Gerente e Orquestrador Central de IA da FluxIA.
    - Interpreta mensagens com base em critérios estritos.
    - Garante cadastro obrigatório de contato (nome e telefone) ao iniciar ou continuar chats.
    - Valida se o cliente já está cadastrado e o chama pelo nome.
    - Compreende os últimos negócios e produtos vinculados ao cliente.
    - Discrimina saudações, despedidas, consultas comerciais, transbordo e perguntas legítimas (RAG).
    """

    def __init__(self):
        self.ai_service = AIService()

    def obter_contexto_cliente(self, chat_id: str, empresa_id: int = 1, nome_sugerido: str = "") -> dict:
        """
        Recupera ou inicializa o cliente no CRM, identificando se possui cadastro completo
        e compilando seus últimos negócios e produtos vinculados.
        """
        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()

        try:
            # 1. Busca ou cria cliente
            cursor.execute(
                f"SELECT id, nome, email, telefone, telegram_chat_id, origem, aguardando_contato, criado_em, empresa_id "
                f"FROM clientes WHERE telegram_chat_id = {ph} AND empresa_id = {ph}",
                (str(chat_id), empresa_id)
            )
            cliente = cursor.fetchone()

            if not cliente:
                nome_inicial = nome_sugerido if (nome_sugerido and not is_nome_generico(nome_sugerido)) else f"Usuário #{chat_id[-4:] if len(chat_id) >= 4 else chat_id}"
                cursor.execute(f"""
                    INSERT INTO clientes (empresa_id, nome, telegram_chat_id, origem, aguardando_contato)
                    VALUES ({ph}, {ph}, {ph}, 'telegram', {ph})
                """, (empresa_id, nome_inicial, str(chat_id), False))
                conexao.commit()
                cursor.execute(
                    f"SELECT id, nome, email, telefone, telegram_chat_id, origem, aguardando_contato, criado_em, empresa_id "
                    f"FROM clientes WHERE telegram_chat_id = {ph} AND empresa_id = {ph}",
                    (str(chat_id), empresa_id)
                )
                cliente = cursor.fetchone()
            else:
                # Se tem nome sugerido real e o cliente tem nome genérico, atualiza
                if nome_sugerido and not is_nome_generico(nome_sugerido) and is_nome_generico(cliente.get("nome")):
                    cursor.execute(f"UPDATE clientes SET nome = {ph} WHERE id = {ph}", (nome_sugerido, cliente["id"]))
                    conexao.commit()
                    cliente["nome"] = nome_sugerido

            cliente_id = cliente["id"]
            nome_cliente = cliente.get("nome") or ""
            telefone_cliente = cliente.get("telefone") or ""
            email_cliente = cliente.get("email") or ""

            tem_nome_valido = not is_nome_generico(nome_cliente)
            tem_telefone_valido = is_telefone_valido(telefone_cliente)
            cadastrado_completo = bool(tem_nome_valido and tem_telefone_valido)

            # 2. Busca últimos negócios e produtos vinculados
            cursor.execute(f"""
                SELECT 
                    n.id, n.estagio, n.valor_estimado, n.proposta_enviada,
                    n.ultima_interacao_em, n.criado_em,
                    p.id AS produto_id, p.nome AS produto_nome, p.descricao AS produto_descricao, p.preco AS produto_preco
                FROM negocios n
                LEFT JOIN produtos p ON p.id = n.produto_id
                WHERE n.cliente_id = {ph} AND (n.empresa_id = {ph} OR n.empresa_id IS NULL)
                ORDER BY n.id DESC
                LIMIT 5
            """, (cliente_id, empresa_id))
            negocios_linhas = cursor.fetchall()

            negocios = []
            produtos_vinculados = []
            negocio_ativo = None

            for row in negocios_linhas:
                item = {
                    "id": row["id"],
                    "estagio": row["estagio"],
                    "valor_estimado": float(row["valor_estimado"] or 0.0),
                    "proposta_enviada": bool(row["proposta_enviada"]),
                    "produto_id": row.get("produto_id"),
                    "produto_nome": row.get("produto_nome"),
                    "produto_descricao": row.get("produto_descricao"),
                    "produto_preco": float(row.get("produto_preco") or 0.0) if row.get("produto_preco") else None,
                    "ultima_interacao_em": str(row.get("ultima_interacao_em") or "")
                }
                negocios.append(item)
                if item["produto_nome"] and item["produto_nome"] not in produtos_vinculados:
                    produtos_vinculados.append(item["produto_nome"])
                if not negocio_ativo and item["estagio"] not in ["fechado", "perdido"]:
                    negocio_ativo = item

            # 3. Status de atendimento (bot vs humano)
            cursor.execute(
                f"SELECT status, atendente_id FROM conversas_telegram WHERE chat_id = {ph} AND (empresa_id = {ph} OR empresa_id IS NULL)",
                (str(chat_id), empresa_id)
            )
            conv_status = cursor.fetchone()

            # 4. Configuração da empresa
            config_empresa = obter_configuracao_empresa(empresa_id)

            return {
                "cliente_id": cliente_id,
                "chat_id": str(chat_id),
                "empresa_id": empresa_id,
                "nome": nome_cliente,
                "telefone": telefone_cliente,
                "email": email_cliente,
                "tem_nome_valido": tem_nome_valido,
                "tem_telefone_valido": tem_telefone_valido,
                "cadastrado_completo": cadastrado_completo,
                "aguardando_contato": bool(cliente.get("aguardando_contato")),
                "negocios": negocios,
                "negocio_ativo": negocio_ativo,
                "produtos_vinculados": produtos_vinculados,
                "status_atendimento": conv_status.get("status") if conv_status else "bot",
                "atendente_id": conv_status.get("atendente_id") if conv_status else None,
                "config_empresa": config_empresa
            }
        finally:
            conexao.close()

    def extrair_dados_contato(self, texto: str, empresa_id: int = 1) -> dict:
        """
        Extrai nome, telefone e email do texto usando padrões regex e verificação estruturada.
        """
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", texto)
        email = email_match.group(0).lower() if email_match else None

        # Regex flexível para telefones com DDD brasileiro
        phone_match = re.search(r"(?:\+?55\s?)?(?:\(?0?[1-9]{2}\)?\s?)?(?:9\s?)?\d{4,5}[-\s]?\d{4}", texto)
        telefone = formatar_telefone_br(phone_match.group(0)) if phone_match else None

        # Nome explícito
        nome = None
        match_nome_exp = re.search(r"(?i)(?:meu nome [eé]|sou o|sou a|me chamo|nome:?)\s+([A-Za-zÀ-ÿ\s]{2,40})", texto)
        if match_nome_exp:
            cand = match_nome_exp.group(1).strip()
            cand = re.split(r"(?i)\s+(?:e\s+(?:o\s+)?(?:meu\s+)?(?:zap|whats|whatsapp|telefone|celular|fone|contato)|(?:e-?mail|telefone|tel|cel|zap|whats|whatsapp):?)\b", cand)[0].strip()
            cand = re.sub(r"[,;:\.\-]+$", "", cand).strip()
            if not is_nome_generico(cand):
                nome = cand

        # Se veio telefone ou email, mas não capturou nome por prefixo:
        if (telefone or email) and not nome:
            texto_limpo = texto
            if email_match:
                texto_limpo = texto_limpo.replace(email_match.group(0), "")
            if phone_match:
                texto_limpo = texto_limpo.replace(phone_match.group(0), "")
            texto_limpo = re.sub(r"(?i)(e-mail:?|email:?|telefone:?|tel:?|cel:?|whatsapp:?|zap:?)", "", texto_limpo)
            texto_limpo = re.sub(r"[,;\n\r\t]+", " ", texto_limpo).strip()
            palavras = texto_limpo.split()
            if 1 <= len(palavras) <= 4 and len(texto_limpo) >= 2 and not is_nome_generico(texto_limpo):
                nome = texto_limpo

        # Fallback inteligente com IA se parecer mensagem com dados mas regex não pegou ambos
        if (telefone and not nome) or (not telefone and ("fone" in texto.lower() or "whatsapp" in texto.lower() or "zap" in texto.lower() or "me chamo" in texto.lower())):
            try:
                client = self.ai_service.obter_client(empresa_id)
                prompt_extracao = f"""
                Extraia estritamente os dados de contato do cliente contidos nesta mensagem:
                "{texto}"
                
                Retorne ESTRITAMENTE um JSON com as chaves:
                {{
                  "nome": "Nome do cliente ou null",
                  "telefone": "Telefone com DDD ou null",
                  "email": "Email ou null"
                }}
                """
                resp = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=prompt_extracao
                )
                t_resp = (resp.text or "").strip()
                t_limpo = re.sub(r"```json\s*|\s*```", "", t_resp).strip()
                parsed = json.loads(t_limpo)
                if not nome and parsed.get("nome") and not is_nome_generico(parsed["nome"]):
                    nome = parsed["nome"].strip()
                if not telefone and parsed.get("telefone") and is_telefone_valido(parsed["telefone"]):
                    telefone = formatar_telefone_br(parsed["telefone"].strip())
                if not email and parsed.get("email"):
                    email = parsed["email"].strip().lower()
            except Exception as e:
                logger.debug(f"[AgentOrchestrator] Falha leve na extração de contato via IA: {e}")

        return {
            "nome": nome,
            "telefone": telefone,
            "email": email,
            "tem_contato": bool(nome or telefone or email),
            "completo": bool(nome and not is_nome_generico(nome) and telefone and is_telefone_valido(telefone))
        }

    def atualizar_dados_cliente(self, cliente_id: int, nome: Optional[str] = None, telefone: Optional[str] = None, email: Optional[str] = None, aguardando: bool = False):
        """Atualiza no banco de dados os dados de contato capturados para o cliente."""
        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()
        try:
            campos = []
            valores = []
            if nome and not is_nome_generico(nome):
                campos.append(f"nome = {ph}")
                valores.append(nome.strip())
            if telefone and is_telefone_valido(telefone):
                campos.append(f"telefone = {ph}")
                valores.append(formatar_telefone_br(telefone))
            if email:
                campos.append(f"email = {ph}")
                valores.append(email.strip().lower())

            campos.append(f"aguardando_contato = {ph}")
            valores.append(aguardando)

            valores.append(cliente_id)
            cursor.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id = {ph}", tuple(valores))
            conexao.commit()
        finally:
            conexao.close()

    def classificar_intencao_gerente(
        self,
        mensagem: str,
        contexto: dict,
        dados_contato_extraidos: dict
    ) -> dict:
        """
        Agente Gerente: Analisa a mensagem com base em critérios operacionais e decide a ação a ser tomada.
        Critérios:
        - COLETA_CONTATO: Cliente ainda não possui cadastro completo (nome e telefone) e a regra obrigatória está ativa.
        - DESPEDIDA: Encerramento ou agradecimento final (tchau, até mais, obrigado por tudo, era só isso).
        - SAUDACAO: Apenas saudação inicial (oi, olá, bom dia) sem pergunta substantiva.
        - TRANSBORDO_HUMANO: Solicitação explícita de atendente/suporte humano.
        - CONSULTA_COMERCIAL: Dúvida sobre status de negócio, proposta em andamento ou produtos contratados.
        - PERGUNTA_CONHECIMENTO: Pergunta legítima sobre produtos, serviços, preços, regras ou informações corporativas.
        """
        msg_limpa = mensagem.strip().lower()
        msg_sem_pont = re.sub(r"[^\w\s]", "", msg_limpa).strip()

        # 1. Comandos Diretos e Transbordo Humano
        termos_transbordo = [
            "/suporte", "suporte", "/support", "falar com atendente", "falar com um atendente",
            "atendente humano", "falar com humano", "falar com uma pessoa",
            "atendimento humano", "transferir para atendente", "consultor humano",
            "chamar atendente", "preciso de um atendente"
        ]
        if any(t in msg_limpa for t in termos_transbordo):
            return {"intencao": "TRANSBORDO_HUMANO", "criterio": "Solicitação explícita de atendente/suporte humano"}

        # 2. Despedida / Fechamento de conversa
        despedidas_exatas = {
            "tchau", "adeus", "até mais", "ate mais", "até logo", "ate logo", "até amanhã", "ate amanha",
            "valeu", "valeu!", "valeu obrigado", "muito obrigado por tudo", "muito obrigada por tudo",
            "era só isso", "era so isso", "só isso obrigado", "so isso obrigado", "por hoje é só", "por hoje e so",
            "pode encerrar", "encerrar", "obrigado tenha um bom dia", "obrigado boa tarde", "obrigado boa noite"
        }
        if msg_sem_pont in despedidas_exatas or any(d in msg_limpa for d in ["era só isso", "era so isso", "pode encerrar", "até mais", "ate mais", "muito obrigado por tudo"]):
            return {"intencao": "DESPEDIDA", "criterio": "Usuário encerrou o diálogo ou despediu-se cordialmente"}

        # 3. Se a coleta obrigatória estiver ligada e faltar nome ou telefone:
        coleta_obrigatoria = contexto["config_empresa"].get("ia_coletar_dados_obrigatorio", True)
        ja_cadastrado = contexto["cadastrado_completo"]

        # Se acabou de fornecer os dados faltantes nesta mensagem
        if dados_contato_extraidos.get("tem_contato"):
            tem_nome_agora = bool(dados_contato_extraidos.get("nome") or contexto["tem_nome_valido"])
            tem_tel_agora = bool(dados_contato_extraidos.get("telefone") or contexto["tem_telefone_valido"])
            if tem_nome_agora and tem_tel_agora:
                # Remove os dados de contato e saudações/introduções comuns para ver se restou uma dúvida
                msg_resto = msg_limpa
                if dados_contato_extraidos.get("nome"):
                    msg_resto = msg_resto.replace(dados_contato_extraidos["nome"].lower(), "")
                if dados_contato_extraidos.get("telefone"):
                    digitos_tel = re.sub(r"\D", "", dados_contato_extraidos["telefone"])
                    msg_resto = msg_resto.replace(dados_contato_extraidos["telefone"].lower(), "").replace(digitos_tel, "")
                # remove termos comuns de apresentação
                msg_resto = re.sub(r"(?i)(me chamo|meu nome [eé]|sou o|sou a|whats(?:app)?|zap|celular|telefone|fone|contato|olá|ola|oi|bom dia|boa tarde|boa noite)", "", msg_resto)
                msg_resto = re.sub(r"[^\w\s]", "", msg_resto).strip()
                palavras_resto = msg_resto.split()

                tem_pergunta = (
                    "?" in mensagem or
                    any(w in msg_resto for w in ["quanto", "como", "qual", "quais", "preço", "preco", "plano", "planos", "valor", "valores", "serviço", "serviços", "duvida", "dúvida", "informação", "informacao", "saber"]) or
                    len(palavras_resto) >= 4
                )
                if tem_pergunta:
                    return {"intencao": "PERGUNTA_CONHECIMENTO", "criterio": "Dados de contato fornecidos junto com dúvida"}
                return {"intencao": "CADASTRO_CONCLUIDO", "criterio": "Dados de contato fornecidos com sucesso"}

        if coleta_obrigatoria and not ja_cadastrado:
            # Cliente não tem cadastro completo e não forneceu dados nesta mensagem
            # Se a mensagem for só uma saudação ou dúvida, devemos solicitar os dados de contato primeiro!
            return {"intencao": "COLETA_CONTATO", "criterio": "Cliente sem telefone/nome cadastrado e regra de contato obrigatório ativa"}

        # 4. Saudação Simples (sem pergunta adicional)
        saudacoes = {
            "oi", "ola", "olá", "oie", "bom dia", "boa tarde", "boa noite", "e ai", "e aí",
            "tudo bem", "tudo bom", "ola tudo bem", "olá tudo bem", "oi tudo bem", "opa"
        }
        if msg_sem_pont in saudacoes:
            return {"intencao": "SAUDACAO", "criterio": "Saudação inicial sem perguntas"}

        # 5. Consulta Comercial sobre negócio ou produto do cliente
        termos_comerciais = ["minha proposta", "meu negócio", "meu negocio", "meu contrato", "andamento do meu", "status da proposta", "status do meu pedido", "status do negócio"]
        if any(t in msg_limpa for t in termos_comerciais):
            return {"intencao": "CONSULTA_COMERCIAL", "criterio": "Consulta sobre histórico comercial do próprio cliente"}

        # 6. Pergunta legítima para base de conhecimento
        return {"intencao": "PERGUNTA_CONHECIMENTO", "criterio": "Pergunta substantiva direcionada à base de conhecimento da empresa"}

    def executar_orquestracao(
        self,
        mensagem: str,
        chat_id: str,
        empresa_id: int = 1,
        nome_telegram: str = ""
    ) -> Tuple[str, dict]:
        """
        Executa o pipeline completo de orquestração do Agente Gerente:
        1. Carrega contexto do cliente, negócios e produtos.
        2. Extrai possíveis dados de contato na mensagem.
        3. Classifica a intenção com base nos critérios estabelecidos.
        4. Despacha para o subagente especializado (Coleta, Saudação, Despedida, Comercial, Transbordo ou RAG).
        5. Retorna (texto_resposta, metadados_execucao).
        """
        # 1. Contexto do cliente
        contexto = self.obter_contexto_cliente(chat_id=chat_id, empresa_id=empresa_id, nome_sugerido=nome_telegram)
        config_empresa = contexto["config_empresa"]

        # Se já estiver em modo humano, a orquestração não responde (apenas armazena)
        if contexto["status_atendimento"] == "humano":
            return "", {"modo": "humano", "acao": "nenhuma"}

        # 2. Extração de contato
        dados_contato = self.extrair_dados_contato(mensagem, empresa_id=empresa_id)

        # Se veio contato na mensagem, atualiza o cliente no banco
        if dados_contato["tem_contato"]:
            novo_nome = dados_contato.get("nome")
            novo_tel = dados_contato.get("telefone")
            novo_email = dados_contato.get("email")
            self.atualizar_dados_cliente(
                cliente_id=contexto["cliente_id"],
                nome=novo_nome,
                telefone=novo_tel,
                email=novo_email,
                aguardando=False
            )
            # Atualiza o contexto em memória
            if novo_nome:
                contexto["nome"] = novo_nome
                contexto["tem_nome_valido"] = True
            if novo_tel:
                contexto["telefone"] = novo_tel
                contexto["tem_telefone_valido"] = True
            contexto["cadastrado_completo"] = bool(contexto["tem_nome_valido"] and contexto["tem_telefone_valido"])

        # 3. Classificação pelo Agente Gerente
        classificacao = self.classificar_intencao_gerente(
            mensagem=mensagem,
            contexto=contexto,
            dados_contato_extraidos=dados_contato
        )
        intencao = classificacao["intencao"]
        logger.info(f"[Orquestrador] Chat {chat_id} | Intenção: {intencao} | Critério: {classificacao.get('criterio')}")

        nome_exibicao = contexto["nome"] if contexto["tem_nome_valido"] else ""

        # 4. Ações dos Subagentes

        # --- AÇÃO: COLETA DE CONTATO OBRIGATÓRIA ---
        if intencao == "COLETA_CONTATO":
            # Marca aguardando_contato no cliente
            self.atualizar_dados_cliente(cliente_id=contexto["cliente_id"], aguardando=True)

            precisa_nome = not contexto["tem_nome_valido"]
            precisa_tel = not contexto["tem_telefone_valido"]

            if precisa_nome and precisa_tel:
                msg_coleta = (
                    "👋 Olá! Seja muito bem-vindo ao atendimento da **FluxIA**.\n\n"
                    "Para iniciarmos seu atendimento personalizado e podermos registrar suas solicitações no sistema, "
                    "por favor me informe o seu **Nome Completo** e seu número de **WhatsApp/Telefone** com DDD."
                )
            elif precisa_tel:
                msg_coleta = (
                    f"👋 Olá, {nome_exibicao}! Para darmos continuidade ao seu atendimento personalizado, "
                    "por favor me informe o seu número de **WhatsApp/Telefone com DDD** para contato."
                )
            else:
                msg_coleta = (
                    "👋 Olá! Para podermos registrar seu atendimento, por favor me informe o seu **Nome Completo**."
                )

            return msg_coleta, {
                "intencao": "COLETA_CONTATO",
                "cadastrado": False,
                "documentos_usados": []
            }

        # --- AÇÃO: CADASTRO CONCLUÍDO NESSA MENSAGEM ---
        if intencao == "CADASTRO_CONCLUIDO":
            tel_fmt = contexto.get("telefone") or dados_contato.get("telefone") or ""
            msg_ok = (
                f"✅ Perfeito, {nome_exibicao}! Seu cadastro foi concluído com sucesso"
                f"{f' (WhatsApp: {tel_fmt})' if tel_fmt else ''}.\n\n"
                "Como posso te ajudar hoje? Fique à vontade para me perguntar sobre nossos produtos, planos e serviços!"
            )
            return msg_ok, {
                "intencao": "CADASTRO_CONCLUIDO",
                "cadastrado": True,
                "documentos_usados": []
            }

        # --- AÇÃO: SAUDAÇÃO INTELIGENTE COM CONTEXTO ---
        if intencao == "SAUDACAO":
            saudacao_nome = f", {nome_exibicao}" if nome_exibicao else ""

            # Verifica se tem negócio ativo ou produtos vinculados para personalizar
            neg_ativo = contexto.get("negocio_ativo")
            if neg_ativo and neg_ativo.get("produto_nome"):
                prod = neg_ativo["produto_nome"]
                estagio = neg_ativo["estagio"]
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! É um prazer falar com você novamente.\n\n"
                    f"Vejo aqui que temos em andamento o seu interesse no produto **{prod}** (estágio: *{estagio}*). "
                    "Como posso te ajudar hoje? Gostaria de tirar dúvidas sobre a proposta ou precisa de alguma nova informação?"
                )
            elif contexto.get("produtos_vinculados"):
                prods_str = ", ".join(contexto["produtos_vinculados"][:2])
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! Que bom ter você por aqui.\n\n"
                    f"Em que posso te auxiliar hoje em relação a {prods_str} ou outros serviços da empresa?"
                )
            else:
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! Seja muito bem-vindo ao nosso atendimento.\n\n"
                    "Como posso te ajudar hoje? Fique à vontade para tirar dúvidas sobre nossos produtos, soluções e planos!"
                )

            return msg_saudacao, {
                "intencao": "SAUDACAO",
                "cadastrado": contexto["cadastrado_completo"],
                "documentos_usados": []
            }

        # --- AÇÃO: DESPEDIDA / ENCERRAMENTO ---
        if intencao == "DESPEDIDA":
            desp_nome = f", {nome_exibicao}" if nome_exibicao else ""
            msg_despedida = (
                f"Foi um prazer falar com você{desp_nome}! 😊\n\n"
                "Caso precise de qualquer outra informação ou deseje dar andamento ao seu pedido, "
                "basta me chamar a qualquer momento. Tenha um excelente dia!"
            )
            return msg_despedida, {
                "intencao": "DESPEDIDA",
                "cadastrado": contexto["cadastrado_completo"],
                "documentos_usados": []
            }

        # --- AÇÃO: TRANSBORDO HUMANO ---
        if intencao == "TRANSBORDO_HUMANO":
            # Atualiza status no banco para humano
            conexao = conectar()
            cursor = _cursor(conexao)
            ph = _placeholder()
            try:
                cursor.execute(f"""
                    INSERT INTO conversas_telegram (chat_id, status, empresa_id)
                    VALUES ({ph}, 'humano', {ph})
                    ON CONFLICT (chat_id) DO UPDATE SET status = 'humano', atualizado_em = CURRENT_TIMESTAMP
                """, (chat_id, empresa_id))
                conexao.commit()
            finally:
                conexao.close()

            tel_suporte = config_empresa.get("numero_suporte_humano", "(11) 99999-9999")
            orientacao = config_empresa.get("mensagem_suporte", "Nossa equipe de suporte foi notificada e entrará em contato.")

            msg_transbordo = (
                f"👤 {nome_exibicao + ', ' if nome_exibicao else ''}seu atendimento foi transferido para um consultor humano da nossa equipe!\n\n"
                f"{orientacao}\n"
                f"📱 Contato de Suporte Direto: `{tel_suporte}`\n\n"
                "Um atendente responderá nesta mesma conversa em breve."
            )
            return msg_transbordo, {
                "intencao": "TRANSBORDO_HUMANO",
                "status": "humano",
                "documentos_usados": []
            }

        # --- AÇÃO: CONSULTA COMERCIAL (STATUS DE NEGÓCIO/PROPOSTA) ---
        if intencao == "CONSULTA_COMERCIAL":
            neg_ativo = contexto.get("negocio_ativo")
            if neg_ativo:
                prod = neg_ativo.get("produto_nome") or "Produto/Solução em negociação"
                estagio = neg_ativo.get("estagio") or "em andamento"
                valor = neg_ativo.get("valor_estimado") or 0.0
                proposta = "Sim" if neg_ativo.get("proposta_enviada") else "Em elaboração"

                msg_comercial = (
                    f"📋 {nome_exibicao + ', ' if nome_exibicao else ''}aqui estão os detalhes do seu atendimento comercial:\n\n"
                    f"• **Produto de Interesse:** {prod}\n"
                    f"• **Estágio Atual:** {estagio.upper()}\n"
                    f"• **Valor Estimado:** R$ {valor:,.2f}\n"
                    f"• **Proposta Comercial:** {proposta}\n\n"
                    "Gostaria de tirar alguma dúvida sobre estes valores ou prefere que um consultor entre em contato para formalizar?"
                )
            else:
                msg_comercial = (
                    f"{nome_exibicao + ', ' if nome_exibicao else ''}não encontrei nenhuma negociação em aberto no momento em seu cadastro. "
                    "Gostaria de solicitar uma proposta comercial ou conhecer os produtos disponíveis?"
                )
            return msg_comercial, {
                "intencao": "CONSULTA_COMERCIAL",
                "cadastrado": contexto["cadastrado_completo"],
                "documentos_usados": []
            }

        # --- AÇÃO: PERGUNTA LEGÍTIMA (RAG DA BASE DE CONHECIMENTO) ---
        from app.routes.telegram import buscar_contexto_relevante
        contexto_publico, docs_usados = buscar_contexto_relevante(pergunta=mensagem, empresa_id=empresa_id)

        historico_recente = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=3, empresa_id=empresa_id)

        # Monta prompt customizado da empresa se configurado
        prompt_custom = config_empresa.get("ia_prompt_sistema")
        if prompt_custom and prompt_custom.strip():
            # Injeta diretriz personalizada no config_suporte
            config_suporte = config_empresa.copy()
            config_suporte["mensagem_suporte"] = f"{config_empresa.get('mensagem_suporte', '')}\nDiretriz Adicional: {prompt_custom}"
        else:
            config_suporte = config_empresa

        # Se temos o nome real do cliente, instruímos o gerador a ser cordial
        prefixo_cliente = f"Cliente atendido: {nome_exibicao}. Chame-o pelo nome cordialmente.\n" if nome_exibicao else ""

        try:
            from app.services.ai_engine_service import ai_engine
            resposta_ia, telemetria_id = ai_engine.gerar_resposta_orquestrada(
                pergunta=prefixo_cliente + mensagem,
                contexto=contexto_publico,
                historico=historico_recente,
                config_suporte=config_suporte,
                empresa_id=empresa_id,
                canal="telegram",
                session_id=chat_id
            )
        except Exception as e:
            logger.warning(f"[Orquestrador] Fallback no ai_engine: {e}")
            resposta_ia = self.ai_service.gerar_resposta(
                pergunta=prefixo_cliente + mensagem,
                contexto=contexto_publico,
                historico=historico_recente,
                config_suporte=config_suporte,
                empresa_id=empresa_id
            )
            telemetria_id = 0

        # Se acabou de cadastrar dados nesta mensagem, prefixa uma confirmação amigável
        if dados_contato.get("tem_contato") and dados_contato.get("nome"):
            resposta_ia = f"Perfeito, {nome_exibicao}! Seus dados foram anotados com sucesso.\n\n" + resposta_ia

        return resposta_ia, {
            "intencao": "PERGUNTA_CONHECIMENTO",
            "cadastrado": contexto["cadastrado_completo"],
            "documentos_usados": docs_usados,
            "telemetria_id": telemetria_id,
            "contexto_publico": contexto_publico
        }


# Instância global do Orquestrador
agent_orchestrator = AgentOrchestrator()
