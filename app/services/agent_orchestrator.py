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

            # 2.1. Busca propostas formais do cliente
            cursor.execute(f"""
                SELECT p.id, p.valor, p.condicoes_pagamento, p.validade_dias, p.status_envio,
                       p.descricao_itens, p.criado_em, COALESCE(prod.nome, prod_n.nome) AS produto_nome, n.estagio AS negocio_estagio
                FROM propostas p
                LEFT JOIN produtos prod ON p.produto_id = prod.id
                LEFT JOIN negocios n ON p.negocio_id = n.id
                LEFT JOIN produtos prod_n ON n.produto_id = prod_n.id
                WHERE (p.cliente_id = {ph} OR n.cliente_id = {ph}) AND (p.empresa_id = {ph} OR p.empresa_id IS NULL)
                ORDER BY p.id DESC
                LIMIT 5
            """, (cliente_id, cliente_id, empresa_id))
            propostas_linhas = cursor.fetchall()
            propostas = [dict(pr) for pr in propostas_linhas]

            # 2.2. Busca contratos do cliente
            cursor.execute(f"""
                SELECT c.id, c.status, c.valor_contrato, c.data_assinatura, c.criado_em,
                       n.id AS negocio_id, n.estagio AS negocio_estagio, prod.nome AS produto_nome
                FROM contratos c
                JOIN negocios n ON c.negocio_id = n.id
                LEFT JOIN produtos prod ON n.produto_id = prod.id
                WHERE n.cliente_id = {ph} AND (n.empresa_id = {ph} OR n.empresa_id IS NULL)
                ORDER BY c.id DESC
                LIMIT 5
            """, (cliente_id, empresa_id))
            contratos_linhas = cursor.fetchall()
            contratos = [dict(ct) for ct in contratos_linhas]

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
                "propostas": propostas,
                "contratos": contratos,
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
            texto_limpo = re.sub(r"(?i)\b(e-mail|email|telefone|tel|cel|whatsapp|whats|zap|meu|minha|meus|é|eh|o|a|os|as|número|numero|contato|segue|aqui|me|chamo|sou)\b:?", " ", texto_limpo)
            texto_limpo = re.sub(r"[,;\n\r\t\.\-\:\/]+", " ", texto_limpo).strip()
            texto_limpo = re.sub(r"\s+", " ", texto_limpo).strip()
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

    def formatar_dossie_comercial_cliente(self, contexto: dict) -> str:
        """
        Formata um dossiê estruturado com as propostas, contratos e oportunidades no CRM do cliente,
        permitindo que a IA responda perguntas em primeira pessoa com segurança, precisão e fluidez.
        """
        blocos = []
        nome = contexto.get("nome") or "Cliente"
        blocos.append(f"=== DOSSIÊ COMERCIAL DE: {nome.upper()} ===")

        propostas = contexto.get("propostas") or []
        if propostas:
            blocos.append(f"PROPOSTAS DO CLIENTE ({len(propostas)} encontrada(s)):")
            for p in propostas:
                val = f"R$ {float(p.get('valor') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                prod = p.get("produto_nome") or "Geral"
                status = p.get("status_envio") or "Registrada"
                cond = p.get("condicoes_pagamento") or "Conforme negociação"
                validade = f"{p.get('validade_dias')} dias" if p.get("validade_dias") else "Não informada"
                dt = str(p.get("criado_em") or "")[:10]
                itens = f" | Descrição: {p.get('descricao_itens')}" if p.get("descricao_itens") else ""
                blocos.append(f"- Proposta #{p.get('id')}: Produto: {prod} | Valor: {val} | Status: {status} | Pagamento: {cond} | Validade: {validade} | Data: {dt}{itens}")
        else:
            blocos.append("PROPOSTAS: Nenhuma proposta formal registrada para este cliente no momento.")

        contratos = contexto.get("contratos") or []
        if contratos:
            blocos.append(f"CONTRATOS DO CLIENTE ({len(contratos)} encontrado(s)):")
            for c in contratos:
                val = f"R$ {float(c.get('valor_contrato') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                status = c.get("status") or "Ativo"
                prod = c.get("produto_nome") or "Serviço Contratado"
                dt_ass = str(c.get("data_assinatura") or "")[:10]
                blocos.append(f"- Contrato #{c.get('id')}: Serviço: {prod} | Valor: {val} | Status: {status} | Assinatura: {dt_ass or 'Pendente'}")
        else:
            blocos.append("CONTRATOS: Nenhum contrato ativo registrado para este cliente no momento.")

        negocios = contexto.get("negocios") or []
        if negocios:
            blocos.append(f"OPORTUNIDADES / NEGOCIAÇÕES NO CRM ({len(negocios)}):")
            for n in negocios[:3]:
                val = f"R$ {float(n.get('valor_estimado') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                blocos.append(f"- Negócio #{n.get('id')}: Produto: {n.get('produto_nome') or 'Diversos'} | Estágio: {n.get('estagio')} | Valor Estimado: {val}")

        # Seção explícita consolidando exclusivamente os itens em aberto do cliente
        itens_abertos = []
        for p in propostas:
            st = (p.get("status_envio") or "").lower()
            if st not in ["recusada", "cancelada"]:
                itens_abertos.append(f"• Proposta em Aberto #{p.get('id')}: {p.get('produto_nome') or 'Solução'} (Valor: R$ {float(p.get('valor') or 0):,.2f}, Status: {p.get('status_envio') or 'Registrada'})")
        for ct in contratos:
            st = (ct.get("status") or "").lower()
            if st not in ["cancelado", "rescindido"]:
                itens_abertos.append(f"• Contrato Vigente #{ct.get('id')}: {ct.get('produto_nome') or 'Serviço'} (Valor: R$ {float(ct.get('valor_contrato') or 0):,.2f}, Status: {ct.get('status') or 'Ativo'})")
        for n in negocios:
            st = (n.get("estagio") or "").lower()
            if st not in ["fechado", "perdido"]:
                itens_abertos.append(f"• Oportunidade em Andamento #{n.get('id')}: {n.get('produto_nome') or 'Diversos'} (Estágio no Funil: {n.get('estagio')})")

        blocos.append("\n=== SEUS ITENS EM ABERTO CONOSCO ===")
        if itens_abertos:
            blocos.extend(itens_abertos)
        else:
            blocos.append("• Você não possui nenhuma pendência, proposta em aberto ou contrato no momento.")

        return "\n".join(blocos)

    def obter_pergunta_anterior(self, chat_id: str, empresa_id: int = 1) -> Optional[str]:
        """
        Recupera a última pergunta ou solicitação legítima do usuário enviada antes
        do pedido de compartilhamento de contato.
        Isso garante que, assim que o usuário informa o WhatsApp/telefone, o assistente
        já responda imediatamente a dúvida anterior, sem obrigá-lo a perguntar de novo.
        """
        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()
        try:
            cursor.execute(f"""
                SELECT mensagem_usuario, resposta_ia
                FROM historico_conversas
                WHERE telegram_chat_id = {ph} AND (empresa_id = {ph} OR empresa_id IS NULL)
                ORDER BY id DESC
                LIMIT 6
            """, (str(chat_id), empresa_id))
            linhas = cursor.fetchall()
            if not linhas:
                return None

            for row in linhas:
                msg = (row.get("mensagem_usuario") or "").strip()
                resp = (row.get("resposta_ia") or "").strip().lower()

                # Ignora mensagens de sistema, botões e comandos
                if not msg or msg.startswith("[") or msg.startswith("/"):
                    continue
                # Se for apenas um número de telefone recém enviado, continua buscando
                digitos = re.sub(r"\D", "", msg)
                if len(digitos) >= 8 and len(digitos) / max(len(msg), 1) > 0.5:
                    continue
                # Se for apenas saudação simples
                if self.eh_saudacao_simples(msg):
                    continue

                # É uma pergunta válida se a resposta da IA anterior pediu contato ou se a mensagem tem conteúdo
                pediu_contato = (
                    "compartilhe seu número" in resp or
                    "whatsapp/telefone" in resp or
                    "botão abaixo" in resp or
                    "cadastrado" in resp or
                    "contato" in resp
                )
                if pediu_contato or len(msg.split()) >= 2:
                    return msg

            return None
        except Exception as e:
            logger.warning(f"[Orquestrador] Erro ao recuperar pergunta anterior: {e}")
            return None
        finally:
            conexao.close()

    def eh_pergunta_processo_base(self, mensagem: str) -> bool:
        """
        Verifica se a mensagem é uma pergunta sobre processos operacionais e institucionais básicos da empresa:
        - Horário de atendimento, funcionamento, expediente, que horas abre/fecha, se está aberto hoje/sábado/domingo.
        - Localização, endereço, onde fica, onde vocês estão, como chegar, sede.
        - Canais de contato, site, quem são vocês, sobre a empresa.
        """
        if not mensagem:
            return False
        msg = mensagem.strip().lower()

        padroes = [
            # Horário e funcionamento
            r"\b(hor[aá]rio|expediente|funcionamento)\b",
            r"\b(que\s+horas?\s+(?:voc[eê]s?\s+)?(?:abrem?|fecham?|atendem?|funcionam?))\b",
            r"\b(abrem?\s+(?:que\s+horas?|hoje|s[aá]bado|domingo|amanh[aã]))\b",
            r"\b(fecham?\s+(?:que\s+horas?|hoje|cedo))\b",
            r"\b(aberto|abertos|fechado|fechados)\s+(?:hoje|agora|s[aá]bado|domingo|no\s+almo[cç]o)\b",
            r"\b(est[aã]o|t[aã]o)\s+(?:abertos?|fechados?|atendendo)\b",
            r"\b(funciona|funcionam|atende|atendem)\s+(?:hoje|agora|s[aá]bado|domingo|aos?\s+finais?)\b",
            r"\b(atendimento\s+(?:hoje|presencial))\b",
            # Localização e endereço
            r"\b(onde\s+(?:fica|ficam|est[aã]o|voc[eê]s\s+est[aã]o|voc[eê]s\s+ficam|localiza))\b",
            r"\b(qual\s+(?:o\s+)?(?:endere[cç]o|localiza[cç][aã]o|local))\b",
            r"\b(como\s+(?:fa[cç]o\s+para\s+)?chegar)\b",
            r"\b(qual\s+o\s+ponto\s+de\s+refer[eê]ncia)\b",
            r"\b(endere[cç]o\s+(?:da\s+empresa|de\s+voc[eê]s|completo))\b",
            r"\b(localiza[cç][aã]o\s+(?:da\s+empresa|de\s+voc[eê]s))\b",
            # Institucional geral
            r"\b(quem\s+s[aã]o\s+voc[eê]s)\b",
            r"\b(sobre\s+a\s+empresa)\b",
            r"\b(o\s+que\s+(?:a\s+empresa|voc[eê]s)\s+fazem?)\b",
        ]

        for padrao in padroes:
            if re.search(padrao, msg):
                return True

        return False

    def eh_saudacao_simples(self, mensagem: str) -> bool:
        """Verifica se a mensagem é estritamente uma saudação inicial sem perguntas substantivas."""
        if not mensagem:
            return False
        msg_limpa = mensagem.strip().lower()
        msg_sem_pont = re.sub(r"[^\w\s]", "", msg_limpa).strip()

        saudacoes = {
            "oi", "ola", "olá", "oie", "bom dia", "boa tarde", "boa noite", "e ai", "e aí",
            "tudo bem", "tudo bom", "ola tudo bem", "olá tudo bem", "oi tudo bem", "opa",
            "opa tudo bem", "opa tudo bom", "olá como vai", "ola como vai", "como vai",
            "bom dia tudo bem", "boa tarde tudo bem", "boa noite tudo bem",
            "oi bom dia", "oi boa tarde", "oi boa noite", "ola bom dia", "ola boa tarde", "ola boa noite",
            "salve", "fala aí", "fala ai", "hello", "hi", "hey"
        }
        if msg_sem_pont in saudacoes:
            return True

        padroes_saudacao = [
            r"^(?:oi|ol[aá]|oie|bom dia|boa tarde|boa noite|opa|salve)(?:\s+(?:tudo\s+bem|tudo\s+bom|como\s+vai))?[\s!.]*$"
        ]
        for padrao in padroes_saudacao:
            if re.match(padrao, msg_limpa):
                return True

        return False

    def eh_tentativa_consulta_terceiros(self, mensagem: str) -> bool:
        """
        Verifica se o usuário no Telegram está tentando obter dados confidenciais de outros clientes,
        empresas terceiras ou da carteira geral de clientes da empresa (salvaguarda estrita de privacidade / LGPD).
        """
        if not mensagem:
            return False
        msg = mensagem.strip().lower()
        padroes = [
            r"\b(outros?\s+clientes?|outras?\s+empresas?|demais\s+clientes?)\b",
            r"\b(todos\s+os\s+clientes?|listar?\s+clientes?|lista\s+de\s+clientes?|carteira\s+de\s+clientes)\b",
            r"\b(proposta\s+d[oe]\s+outro|propostas\s+d[oe]\s+outros?|contrato\s+d[oe]\s+outro|contratos\s+d[oe]\s+outros?)\b",
            r"\b(dados\s+d[oe]\s+outro\s+cliente|informa[cç][oõ]es\s+de\s+outro\s+cliente)\b",
            r"\b(quanto\s+(?:o\s+)?outro\s+cliente|quem\s+mais\s+(?:comprou|contratou|assinou))\b",
            r"\b(quanto\s+fulano|proposta\s+d[oe]\s+[A-Za-zÀ-ÿ]+|contrato\s+d[oe]\s+[A-Za-zÀ-ÿ]+)\b",
            r"\b(quais\s+clientes\s+voc[eê]s\s+t[eê]m|quem\s+s[aã]o\s+os\s+clientes)\b",
        ]
        for p in padroes:
            if re.search(p, msg):
                return True
        return False

    def eh_solicitacao_proposta_ou_negociacao(
        self,
        mensagem: str,
        chat_id: Optional[str] = None,
        empresa_id: int = 1
    ) -> tuple[bool, str]:
        """
        Verifica se o cliente está solicitando a elaboração de uma nova proposta comercial,
        orçamento formal, negociando termos, ou respondendo afirmativamente ao convite
        da IA de proposta/atendimento com consultor feito na mensagem anterior.
        Retorna (True, 'proposta' | 'negociacao') ou (False, '').
        """
        if not mensagem:
            return False, ""
        msg = mensagem.strip().lower()
        msg_sem_pont = re.sub(r"[^\w\s]", "", msg).strip()

        # Se for estritamente uma consulta aos itens que ele já possui no sistema, não é novo ticket
        termos_consulta_existente = [
            "quais propostas", "qual proposta que tenho", "propostas que tenho", "tenho proposta",
            "possuo proposta", "proposta que possuo", "meus contratos", "qual o contrato que tenho",
            "contratos que tenho", "itens em aberto", "o que tenho em aberto", "minhas pendências",
            "o que eu contratei", "o que contratei", "valor do meu contrato"
        ]
        if any(t in msg for t in termos_consulta_existente) and not any(p in msg for p in ["nova proposta", "novo orçamento", "outro contrato", "desconto", "negociar", "fechar", "prosseguir", "avançar", "continuidade"]):
            return False, ""

        # 0. Verificação de resposta afirmativa ao convite comercial (CTA) da IA na mensagem anterior
        if chat_id:
            try:
                hist = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=5, empresa_id=empresa_id)
                # Como a rota já pode ter gravado o registro da mensagem atual com resposta_ia vazia,
                # filtramos apenas as interações que contêm resposta da IA de fato.
                hist_com_ia = [h for h in hist if (h.get("resposta_ia") or "").strip()]
                if hist_com_ia:
                    ultima_ia = hist_com_ia[-1]["resposta_ia"].lower()
                    ofertou_proposta_ou_humano = any(p in ultima_ia for p in [
                        "proposta comercial", "proposta personalizada", "preparássemos uma proposta",
                        "preparassemos uma proposta", "consultores humanos", "atendente humano",
                        "entender melhor sobre o nosso trabalho", "entender melhor sobre nosso trabalho",
                        "consultor humano", "proposta comercial personalizada", "enviar uma proposta",
                        "enviada uma proposta", "equipe comercial", "consultor comercial"
                    ])
                    if ofertou_proposta_ou_humano:
                        respostas_afirmativas = {
                            "sim", "sim!", "sim por favor", "sim pf", "quero", "quero sim", "quero sim por favor",
                            "gostaria", "gostaria sim", "gostaria por favor", "pode ser", "com certeza", "claro",
                            "claro com certeza", "vamos", "vamos la", "vamos lá", "bora", "pode mandar", "pode enviar",
                            "manda", "envia", "manda aí", "manda ai", "ok", "beleza", "isso", "por favor",
                            "quero a proposta", "manda a proposta", "gostaria da proposta", "proposta",
                            "quero entender melhor", "entender melhor", "falar com consultor", "falar com atendente",
                            "atendente humano", "consultor humano", "prefiro falar com atendente", "com atendente",
                            "sim gostaria", "sim quero", "sim pode ser", "sim claro", "pode ser sim",
                            "pode", "pode sim", "fechado", "com certeza quero", "manda ver", "manda bala"
                        }
                        if msg_sem_pont in respostas_afirmativas or any(msg.startswith(prefix) for prefix in ["sim", "quero", "gostaria", "pode ser", "claro", "pode mandar", "pode enviar"]):
                            return True, "proposta"
            except Exception as e:
                logger.debug(f"[Orquestrador] Erro ao checar histórico para CTA de proposta: {e}")

        # 1. Padrões de Proposta Comercial / Orçamento Formal / Continuidade de Proposta
        padroes_proposta = [
            r"\b(quero|gostaria|preciso|solicito|desejo|favor|pode|consegue)\s+(?:de\s+)?(?:uma?\s+)?(?:nova?\s+)?(proposta|or[cç]amento|cota[cç][aã]o)\b",
            r"\b(me\s+(?:envie|mande|passa|encaminhe)|enviar|mandar|gerar|elaborar|preparar)\s+(?:uma?\s+)?(?:nova?\s+)?(proposta|or[cç]amento|cota[cç][aã]o)\b",
            r"\b(proposta\s+comercial|proposta\s+formal|or[cç]amento\s+formal)\b",
            r"\b(como\s+(?:fa[cç]o\s+para\s+)?(?:receber|obter|solicitar)\s+(?:uma?\s+)?proposta)\b",
            r"\b(como\s+(?:posso\s+|fa[cç]o\s+para\s+)?(?:prosseguir|avan[cç]ar|dar\s+continuidade|continuar)\s+(?:com\s+|na\s+)?(?:a\s+|minha\s+|uma\s+)?proposta)\b",
            r"\b(possibilidade\s+de\s+(?:uma\s+)?(?:nova\s+)?proposta)\b",
            r"\b(prosseguir|avan[cç]ar|dar\s+continuidade)\s+(?:com\s+|na\s+)?(?:a\s+|minha\s+)?proposta\b",
            r"\b(quero\s+(?:contratar|comprar)|vamos\s+fechar)\s*[,.]?\s*(?:me\s+)?(?:manda|envia)\s+(?:a\s+)?proposta\b",
            r"\b(manda\s+(?:a\s+)?proposta|envia\s+(?:a\s+)?proposta|passa\s+(?:a\s+)?proposta)\b",
            r"\b(quero\s+a\s+proposta|gostaria\s+da\s+proposta|enviar\s+a\s+proposta)\b",
            r"\b(entender\s+melhor\s+sobre\s+o\s+trabalho|falar\s+com\s+(?:um\s+)?consultor)\b",
            r"\b(fechar|aprovar|aceitar)\s+(?:a\s+|minha\s+)?proposta\b"
        ]
        for p in padroes_proposta:
            if re.search(p, msg):
                return True, "proposta"

        # 2. Padrões de Negociação de Valores, Desconto e Contrato
        padroes_negociacao = [
            r"\b(tem|teria|consegue|d[aá]\s+para|conseguem)\s+(?:algum\s+)?desconto\b",
            r"\b(negociar|negocia[cç][aã]o|fazer\s+um\s+(?:pre[cç]o|valor)\s+melhor|mais\s+em\s+conta|mais\s+barato)\b",
            r"\b(podemos\s+negociar|quero\s+negociar|negociar\s+(?:o\s+)?(?:pre[cç]o|valor|contrato))\b",
            r"\b(condi[cç][oõ]es?\s+(?:especia(?:l|is)|personalizadas?|diferenciadas?)\s+de\s+pagamento)\b",
            r"\b(parcelar\s+em\s+mais|formas?\s+de\s+pagamento\s+especia(?:l|is))\b",
            r"\b(fechar\s+(?:o\s+)?contrato|assinar\s+(?:o\s+)?contrato|elaborar\s+(?:o\s+)?contrato)\b",
            r"\b(termos?\s+d[oe]\s+contrato|cl[aá]usulas?\s+d[oe]\s+contrato|alterar\s+cl[aá]usula)\b",
            r"\b(vamos\s+fechar\s+(?:neg[oó]cio|o\s+contrato))\b",
            r"\b(quero\s+fechar\s+(?:neg[oó]cio|o\s+contrato))\b"
        ]
        for p in padroes_negociacao:
            if re.search(p, msg):
                return True, "negociacao"

        return False, ""

    def classificar_intencao_gerente(
        self,
        mensagem: str,
        contexto: dict,
        dados_contato_extraidos: dict
    ) -> dict:
        """
        Agente Gerente: Analisa a mensagem com base em critérios operacionais e decide a ação a ser tomada.
        Critérios:
        - BLOQUEIO_PRIVACIDADE: Tentativa de obter dados de outros clientes ou carteira geral (LGPD estrita).
        - COLETA_CONTATO: Cliente ainda não possui cadastro completo (nome e telefone) e a pergunta exige contato prévio.
        - DESPEDIDA: Encerramento ou agradecimento final (tchau, até mais, obrigado por tudo, era só isso, pode encerrar).
        - SAUDACAO: Apenas saudação inicial (oi, olá, bom dia) sem pergunta substantiva.
        - TRANSBORDO_HUMANO: Solicitação explícita de atendente/suporte humano.
        - CONSULTA_COMERCIAL: Dúvida em primeira pessoa sobre itens em aberto, propostas, contratos, negócios em andamento.
        - PERGUNTA_CONHECIMENTO: Pergunta legítima sobre produtos, serviços, preços, regras ou informações corporativas.
        """
        msg_limpa = mensagem.strip().lower()
        msg_sem_pont = re.sub(r"[^\w\s]", "", msg_limpa).strip()

        # 0. Salvaguarda de Privacidade Estrita (Nenhum cliente consulta dados de outro)
        if self.eh_tentativa_consulta_terceiros(mensagem):
            return {
                "intencao": "BLOQUEIO_PRIVACIDADE",
                "criterio": "Tentativa de consulta a dados de outros clientes ou carteira de clientes (LGPD)"
            }

        # 1. Comandos Diretos e Transbordo Humano
        termos_transbordo = [
            "/suporte", "suporte", "/support", "falar com atendente", "falar com um atendente",
            "atendente humano", "falar com humano", "falar com uma pessoa",
            "atendimento humano", "transferir para atendente", "consultor humano",
            "chamar atendente", "preciso de um atendente"
        ]
        if any(t in msg_limpa for t in termos_transbordo):
            return {"intencao": "TRANSBORDO_HUMANO", "criterio": "Solicitação explícita de atendente/suporte humano"}

        # 2. Despedida / Fechamento de conversa / Encerramento
        despedidas_exatas = {
            "tchau", "adeus", "até mais", "ate mais", "até logo", "ate logo", "até amanhã", "ate amanha",
            "valeu", "valeu!", "valeu obrigado", "muito obrigado por tudo", "muito obrigada por tudo",
            "era só isso", "era so isso", "só isso obrigado", "so isso obrigado", "por hoje é só", "por hoje e so",
            "pode encerrar", "encerrar", "obrigado tenha um bom dia", "obrigado boa tarde", "obrigado boa noite",
            "nao preciso de mais nada", "não preciso de mais nada", "finalizar", "pode finalizar", "encerrar atendimento",
            "fim", "fechar", "ok obrigado tchau", "obrigado tchau", "valeu tchau", "só isso", "so isso",
            "tudo certo obrigado", "tudo certo obrigada"
        }
        termos_despedida = [
            "tchau", "adeus", "até mais", "ate mais", "até logo", "ate logo", "até amanhã", "ate amanha",
            "era só isso", "era so isso", "só isso obrigado", "so isso obrigado", "por hoje é só", "por hoje e so",
            "pode encerrar", "encerrar atendimento", "muito obrigado por tudo", "muito obrigada por tudo",
            "não preciso de mais nada", "nao preciso de mais nada", "pode finalizar", "finalizar atendimento",
            "fechar atendimento", "encerrar a conversa", "pode encerrar a conversa", "por hoje era só"
        ]
        if msg_sem_pont in despedidas_exatas or any(d in msg_limpa for d in termos_despedida):
            return {"intencao": "DESPEDIDA", "criterio": "Usuário encerrou o diálogo ou despediu-se cordialmente"}

        # 3. Termos comerciais, itens em aberto e de primeira pessoa (propostas, contratos, negócios)
        termos_comerciais = [
            "minha proposta", "minhas propostas", "quais propostas", "qual proposta", "tenho proposta",
            "possuo proposta", "proposta que tenho", "propostas que tenho", "propostas possuo",
            "meu contrato", "meus contratos", "qual o contrato", "quais contratos", "tenho contrato",
            "possuo contrato", "contrato que tenho", "contratos que tenho", "valor do contrato",
            "valor do meu contrato", "qual o valor do contrato", "qual o valor do contrato que tenho",
            "meu negócio", "meu negocio", "andamento do meu", "status da proposta", "status do meu pedido",
            "status do negócio", "o que eu contratei", "o que contratei", "meu plano contratado",
            "segunda via do contrato", "quais propostas possuo com vocês", "quais contratos possuo com vocês",
            # Consultas específicas sobre itens em aberto e pendências
            "itens em aberto", "item em aberto", "o que tenho em aberto", "o que está em aberto", "tenho em aberto",
            "itens abertos", "meus itens em aberto", "quais itens tenho em aberto", "quais itens possuo em aberto",
            "itens que tenho em aberto", "o que tenho pendente", "minhas pendências", "quais pendências",
            "meus processos em aberto", "o que está pendente no meu atendimento", "negócios em andamento"
        ]
        padroes_comerciais_regex = [
            r"\b(minha|minhas|meu|meus|qual|quais|tenho|possuo|algum|alguma)\s+.*?\b(proposta|propostas|contrato|contratos|neg[oó]cio|neg[oó]cios)\b",
            r"\b(proposta|propostas|contrato|contratos|neg[oó]cio|neg[oó]cios)\s+.*?\b(que\s+tenho|que\s+possuo|em\s+aberto|abertos?|ativo|ativos|vigente|vigentes|pendente|pendentes|meu|minha|meus|minhas)\b",
            r"\b(itens?|item)\s+.*?\b(em\s+aberto|abertos?|pendentes?)\b",
            r"\b(o\s+que\s+(?:eu\s+)?tenho\s+(?:em\s+aberto|pendente))\b",
            r"\b(minhas?\s+pend[eê]ncias?)\b",
            r"\b(status\s+d[oa]\s+(?:proposta|pedido|neg[oó]cio|contrato))\b",
            r"\b(valor\s+d[oa]\s+(?:meu\s+|minha\s+)?(?:contrato|proposta))\b",
            r"\b(o\s+que\s+(?:eu\s+)?contratei)\b",
        ]
        eh_consulta_comercial = any(t in msg_limpa for t in termos_comerciais) or any(re.search(p, msg_limpa) for p in padroes_comerciais_regex)

        # 3. Solicitação de Proposta Comercial ou Negociação de Contrato (Demanda Humana Exclusiva)
        eh_prop_neg, tipo_prop_neg = self.eh_solicitacao_proposta_ou_negociacao(
            mensagem,
            chat_id=contexto.get("chat_id"),
            empresa_id=contexto.get("empresa_id", 1)
        )
        if eh_prop_neg:
            return {
                "intencao": "SOLICITACAO_PROPOSTA_NEGOCIACAO",
                "tipo_ticket": tipo_prop_neg,
                "criterio": f"Solicitação de {tipo_prop_neg} formal encaminhada para geração imediata de ticket no CRM e atendimento via Telegram"
            }

        # 4. Se acabou de fornecer os dados faltantes nesta mensagem
        if dados_contato_extraidos.get("tem_contato"):
            tem_nome_agora = bool(dados_contato_extraidos.get("nome") or contexto["tem_nome_valido"])
            tem_tel_agora = bool(dados_contato_extraidos.get("telefone") or contexto["tem_telefone_valido"])
            if tem_nome_agora and tem_tel_agora:
                # Remove os dados de contato e saudações comuns para ver se restou uma dúvida
                msg_resto = msg_limpa
                if dados_contato_extraidos.get("nome"):
                    msg_resto = msg_resto.replace(dados_contato_extraidos["nome"].lower(), "")
                if dados_contato_extraidos.get("telefone"):
                    digitos_tel = re.sub(r"\D", "", dados_contato_extraidos["telefone"])
                    msg_resto = msg_resto.replace(dados_contato_extraidos["telefone"].lower(), "").replace(digitos_tel, "")
                msg_resto = re.sub(r"(?i)(me chamo|meu nome [eé]|sou o|sou a|whats(?:app)?|zap|celular|telefone|fone|contato|olá|ola|oi|bom dia|boa tarde|boa noite)", "", msg_resto)
                msg_resto = re.sub(r"[^\w\s]", "", msg_resto).strip()
                palavras_resto = msg_resto.split()

                if eh_prop_neg:
                    return {
                        "intencao": "SOLICITACAO_PROPOSTA_NEGOCIACAO",
                        "tipo_ticket": tipo_prop_neg,
                        "criterio": "Dados de contato fornecidos junto com pedido de proposta/negociação"
                    }

                if eh_consulta_comercial:
                    return {"intencao": "CONSULTA_COMERCIAL", "criterio": "Dados de contato fornecidos junto com consulta comercial"}

                tem_pergunta = (
                    "?" in mensagem or
                    any(w in msg_resto for w in ["quanto", "como", "qual", "quais", "preço", "preco", "plano", "planos", "valor", "valores", "serviço", "serviços", "duvida", "dúvida", "informação", "informacao", "saber"]) or
                    len(palavras_resto) >= 4
                )
                if tem_pergunta:
                    return {"intencao": "PERGUNTA_CONHECIMENTO", "criterio": "Dados de contato fornecidos junto com dúvida"}
                return {"intencao": "CADASTRO_CONCLUIDO", "criterio": "Dados de contato fornecidos com sucesso"}

        # 5. Saudação Simples (sem pergunta adicional): responde cordial com outra saudação, sem pedir número
        if self.eh_saudacao_simples(mensagem):
            return {"intencao": "SAUDACAO", "criterio": "Saudação inicial sem perguntas adicionais"}

        # 6. Consulta Comercial e Itens em Aberto do próprio cliente
        if eh_consulta_comercial:
            if not contexto.get("tem_telefone_valido"):
                return {
                    "intencao": "COLETA_CONTATO",
                    "criterio": "Consulta de itens em aberto, propostas ou contratos exigindo telefone/WhatsApp para identificação segura"
                }
            return {
                "intencao": "CONSULTA_COMERCIAL",
                "criterio": "Consulta sobre histórico comercial, itens em aberto, propostas ou contratos do próprio cliente"
            }

        # 7. Processos base da empresa (horário de funcionamento, localização, se estão abertos hoje, etc.): responde direto sem exigir número
        if self.eh_pergunta_processo_base(mensagem):
            return {
                "intencao": "PERGUNTA_CONHECIMENTO",
                "criterio": "Pergunta sobre processo base da empresa (horário/localização/funcionamento) sem exigir telefone"
            }

        # 8. Se ainda não possui telefone válido: qualquer dúvida sobre produtos, serviços, preços ou planos exige contato
        if not contexto.get("tem_telefone_valido"):
            return {
                "intencao": "COLETA_CONTATO",
                "criterio": "Dúvida sobre produtos, planos ou serviços exigindo telefone"
            }

        # 9. Pergunta legítima para base de conhecimento
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
        1. Carrega contexto do cliente, negócios, propostas e contratos.
        2. Extrai possíveis dados de contato na mensagem.
        3. Classifica a intenção com base nos critérios estabelecidos.
        4. Despacha para o subagente especializado de forma dinâmica e moderna (estilo ChatGPT/Claude).
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

        # --- AÇÃO: BLOQUEIO DE PRIVACIDADE / PROTEÇÃO LGPD ---
        if intencao == "BLOQUEIO_PRIVACIDADE":
            msg_bloqueio = (
                f"🔒 {nome_exibicao + ', ' if nome_exibicao else ''}por motivos de segurança, sigilo profissional e "
                "proteção à privacidade de dados (LGPD), você possui acesso estritamente às suas próprias informações, propostas e contratos.\n\n"
                "Não compartilhamos dados cadastrais, comerciais ou contratuais de outros clientes ou terceiros. "
                "Se desejar consultar os seus próprios itens em aberto, propostas ou contratos, basta me perguntar!"
            )
            return msg_bloqueio, {
                "intencao": "BLOQUEIO_PRIVACIDADE",
                "cadastrado": contexto["cadastrado_completo"],
                "documentos_usados": []
            }

        # --- AÇÃO: COLETA DE CONTATO (NÚMERO DE TELEFONE) ---
        if intencao == "COLETA_CONTATO":
            self.atualizar_dados_cliente(cliente_id=contexto["cliente_id"], aguardando=True)

            eh_comercial = "proposta" in mensagem.lower() or "contrato" in mensagem.lower() or "negócio" in mensagem.lower()
            if eh_comercial:
                if nome_exibicao:
                    msg_coleta = (
                        f"👋 Olá, {nome_exibicao}! Para eu localizar suas **propostas e contratos** com total sigilo e precisão no sistema, "
                        "por favor compartilhe seu número de **WhatsApp/Telefone** no botão abaixo (ou digite seu número com DDD)."
                    )
                else:
                    msg_coleta = (
                        "👋 Olá! Para eu localizar suas **propostas e contratos** com total sigilo e precisão no sistema, "
                        "por favor compartilhe seu número de **WhatsApp/Telefone** no botão abaixo (ou digite seu número com DDD)."
                    )
            elif nome_exibicao:
                msg_coleta = (
                    f"👋 Olá, {nome_exibicao}! Para te passar todas as informações detalhadas sobre nossos produtos, planos e condições personalizadas, "
                    "por favor compartilhe seu número de **WhatsApp/Telefone** no botão abaixo (ou digite seu número com DDD)."
                )
            else:
                msg_coleta = (
                    "👋 Olá! Seja muito bem-vindo ao nosso atendimento.\n\n"
                    "Para te passar todas as informações detalhadas sobre nossos produtos, planos e condições personalizadas, "
                    "por favor compartilhe seu número de **WhatsApp/Telefone** no botão abaixo (ou digite seu número com DDD)."
                )

            return msg_coleta, {
                "intencao": "COLETA_CONTATO",
                "cadastrado": False,
                "exibir_botao_contato": True,
                "documentos_usados": []
            }

        # --- AÇÃO: CADASTRO CONCLUÍDO NESSA MENSAGEM ---
        if intencao == "CADASTRO_CONCLUIDO":
            # Verifica se há pergunta anterior que ficou aguardando o contato
            pergunta_ant = self.obter_pergunta_anterior(chat_id=chat_id, empresa_id=empresa_id)
            if pergunta_ant:
                logger.info(f"[Orquestrador] Respondendo pergunta anterior pós-cadastro: '{pergunta_ant}'")
                resposta_pendente, meta_pendente = self.executar_orquestracao(
                    mensagem=pergunta_ant,
                    chat_id=chat_id,
                    empresa_id=empresa_id,
                    nome_telegram=contexto["nome"]
                )
                msg_ok = (
                    f"✅ Perfeito, {nome_exibicao}! Seu WhatsApp foi registrado com sucesso.\n\n"
                    f"Respondendo à sua dúvida anterior:\n\n{resposta_pendente}"
                )
                return msg_ok, {
                    "intencao": "CADASTRO_CONCLUIDO",
                    "cadastrado": True,
                    "remover_teclado": True,
                    "documentos_usados": meta_pendente.get("documentos_usados", []),
                    "telemetria_id": meta_pendente.get("telemetria_id", 0)
                }

            tel_fmt = contexto.get("telefone") or dados_contato.get("telefone") or ""
            msg_ok = (
                f"✅ Perfeito, {nome_exibicao}! Seu cadastro foi concluído com sucesso"
                f"{f' (WhatsApp: {tel_fmt})' if tel_fmt else ''}.\n\n"
                "Como posso te ajudar hoje? Fique à vontade para me perguntar sobre nossos produtos, planos, propostas e serviços!"
            )
            return msg_ok, {
                "intencao": "CADASTRO_CONCLUIDO",
                "cadastrado": True,
                "remover_teclado": True,
                "documentos_usados": []
            }

        # --- AÇÃO: SAUDAÇÃO INTELIGENTE COM CONTEXTO ---
        if intencao == "SAUDACAO":
            saudacao_nome = f", {nome_exibicao}" if nome_exibicao else ""

            # Personaliza a saudação se o cliente tiver propostas ou contratos
            propostas_cliente = contexto.get("propostas") or []
            contratos_cliente = contexto.get("contratos") or []
            neg_ativo = contexto.get("negocio_ativo")

            if contratos_cliente:
                c_top = contratos_cliente[0]
                servico = c_top.get("produto_nome") or "Serviço"
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! Que bom falar com você novamente.\n\n"
                    f"Vejo aqui o seu contrato ativo de **{servico}**. "
                    "Em que posso te ajudar hoje? Deseja consultar detalhes contratuais, propostas ou tirar alguma dúvida?"
                )
            elif propostas_cliente:
                p_top = propostas_cliente[0]
                prod = p_top.get("produto_nome") or "Solução"
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! É um prazer falar com você novamente.\n\n"
                    f"Temos registrada a sua proposta para **{prod}**. "
                    "Como posso te ajudar hoje? Gostaria de rever valores, condições ou tirar alguma dúvida?"
                )
            elif neg_ativo and neg_ativo.get("produto_nome"):
                prod = neg_ativo["produto_nome"]
                estagio = neg_ativo["estagio"]
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! É um prazer falar com você novamente.\n\n"
                    f"Vejo que temos em andamento o seu interesse em **{prod}** (estágio: *{estagio}*). "
                    "Como posso te ajudar hoje?"
                )
            elif contexto.get("produtos_vinculados"):
                prods_str = ", ".join(contexto["produtos_vinculados"][:2])
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! Que bom ter você por aqui.\n\n"
                    f"Em que posso te auxiliar hoje em relação a {prods_str} ou outros serviços da empresa?"
                )
            else:
                msg_saudacao = (
                    f"👋 Olá{saudacao_nome}! Tudo bem? Seja muito bem-vindo.\n\n"
                    "Como posso te ajudar hoje? Fique à vontade para me perguntar sobre nossos produtos, planos, propostas, horários ou localização!"
                )

            return msg_saudacao, {
                "intencao": "SAUDACAO",
                "cadastrado": contexto["cadastrado_completo"],
                "documentos_usados": []
            }

        # --- AÇÃO: DESPEDIDA / ENCERRAMENTO DINÂMICO ---
        if intencao == "DESPEDIDA":
            # Desativa flag de aguardando contato
            self.atualizar_dados_cliente(cliente_id=contexto["cliente_id"], aguardando=False)

            historico_recente = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=2, empresa_id=empresa_id)
            prompt_despedida = (
                f"O usuário {nome_exibicao or 'Cliente'} está se despedindo/encerrando a conversa com: '{mensagem}'. "
                "Responda com uma despedida elegante, simpática, moderna e calorosa no estilo ChatGPT/Claude. "
                "Agradeça pelo contato e se coloque à disposição para quando ele precisar. "
                "NÃO faça novas perguntas nem pergunte 'posso ajudar em algo mais?'."
            )
            try:
                from app.services.ai_engine_service import ai_engine
                msg_despedida, _ = ai_engine.gerar_resposta_orquestrada(
                    pergunta=prompt_despedida,
                    contexto="",
                    historico=historico_recente,
                    config_suporte=config_empresa,
                    empresa_id=empresa_id,
                    canal="telegram",
                    session_id=chat_id
                )
            except Exception:
                desp_nome = f", {nome_exibicao}" if nome_exibicao else ""
                msg_despedida = (
                    f"Foi um prazer falar com você{desp_nome}! 😊\n\n"
                    "Quando precisar de qualquer informação ou dar andamento ao seu projeto, basta me chamar por aqui. "
                    "Tenha um excelente dia e até breve!"
                )

            return msg_despedida, {
                "intencao": "DESPEDIDA",
                "cadastrado": contexto["cadastrado_completo"],
                "conversa_encerrada": True,
                "remover_teclado": True,
                "documentos_usados": []
            }

        # --- AÇÃO: TRANSBORDO HUMANO ---
        if intencao == "TRANSBORDO_HUMANO":
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

            # Criação automática do ticket de suporte para atendimento direto pelo Telegram
            from app.services.ticket_service import criar_ticket_automatico
            ticket = criar_ticket_automatico(
                empresa_id=empresa_id,
                cliente_id=contexto.get("cliente_id"),
                telegram_chat_id=chat_id,
                tipo="suporte",
                titulo="Solicitação de Atendimento Humano",
                ultima_mensagem_cliente=mensagem,
                cliente_nome=nome_exibicao,
                cliente_telefone=contexto.get("telefone", ""),
                prioridade="alta"
            )
            ticket_id = ticket.get("id") if ticket else None
            ticket_str = f" (**Ticket #{ticket_id}**)" if ticket_id else ""

            msg_transbordo = (
                f"👤 {nome_exibicao + ', ' if nome_exibicao else ''}seu atendimento foi transferido para a nossa equipe!\n\n"
                f"🎫 Já abri um chamado prioritário{ticket_str} para que um dos nossos atendentes dê continuidade ao seu atendimento. "
                "Eles responderão diretamente por aqui nesta conversa em instantes!"
            )
            return msg_transbordo, {
                "intencao": "TRANSBORDO_HUMANO",
                "status": "humano",
                "ticket_id": ticket_id,
                "remover_teclado": True,
                "documentos_usados": []
            }

        # --- AÇÃO: SOLICITAÇÃO DE PROPOSTA OU NEGOCIAÇÃO DE CONTRATO (GERAÇÃO DE TICKET IA) ---
        if intencao == "SOLICITACAO_PROPOSTA_NEGOCIACAO":
            tipo_ticket = classificacao.get("tipo_ticket", "proposta")
            titulo_ticket = (
                "Solicitação de Proposta Comercial"
                if tipo_ticket == "proposta"
                else "Negociação de Valores / Termos de Contrato"
            )

            # Marca a conversa como humano para que o operador dê continuidade pelo Telegram via Ticket
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

            from app.services.ticket_service import criar_ticket_automatico
            ticket = criar_ticket_automatico(
                empresa_id=empresa_id,
                cliente_id=contexto.get("cliente_id"),
                telegram_chat_id=chat_id,
                tipo=tipo_ticket,
                titulo=titulo_ticket,
                ultima_mensagem_cliente=mensagem,
                cliente_nome=nome_exibicao,
                cliente_telefone=contexto.get("telefone", ""),
                prioridade="alta"
            )
            ticket_id = ticket.get("id", 1)

            saudacao_nome = f", {nome_exibicao}" if nome_exibicao else ""
            if tipo_ticket == "proposta":
                msg_resposta = (
                    f"Perfeito{saudacao_nome}! Para elaboração de propostas comerciais personalizadas com todos os detalhes e "
                    "as melhores condições, nosso atendimento é conduzido diretamente por um dos nossos consultores especialistas.\n\n"
                    f"🎫 Já abri um chamado prioritário (**Ticket #{ticket_id}**) para nossa equipe comercial com o resumo do que você precisa. "
                    "Em instantes um de nossos atendentes dará continuidade por aqui mesmo!"
                )
            else:
                msg_resposta = (
                    f"Com certeza{saudacao_nome}! Para negociação de valores, condições especiais e termos de contrato, "
                    "nosso atendimento é realizado diretamente por um consultor comercial com autonomia para apresentar a melhor condição possível.\n\n"
                    f"🎫 Já registrei seu chamado prioritário (**Ticket #{ticket_id}**) para a nossa equipe. "
                    "Um dos nossos especialistas responderá por aqui em instantes para alinharmos todos os detalhes!"
                )

            return msg_resposta, {
                "intencao": "SOLICITACAO_PROPOSTA_NEGOCIACAO",
                "ticket_id": ticket_id,
                "status": "humano",
                "tipo": tipo_ticket,
                "cadastrado": contexto["cadastrado_completo"],
                "remover_teclado": True,
                "documentos_usados": []
            }

        # --- AÇÃO: CONSULTA COMERCIAL (PROPOSTAS, CONTRATOS E STATUS DO CLIENTE) ---
        if intencao == "CONSULTA_COMERCIAL":
            dossie = self.formatar_dossie_comercial_cliente(contexto)
            from app.routes.telegram import buscar_contexto_relevante
            contexto_publico, docs_usados = buscar_contexto_relevante(pergunta=mensagem, empresa_id=empresa_id)
            contexto_total = f"{dossie}\n\n[INFORMAÇÕES ADICIONAIS DA BASE DE CONHECIMENTO]:\n{contexto_publico}" if contexto_publico else dossie

            historico_recente = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=3, empresa_id=empresa_id)

            prefixo_cliente = (
                f"Cliente em atendimento: {nome_exibicao or 'Cliente'}. "
                "Responda diretamente em primeira pessoa com simpatia, naturalidade e precisão absoluta. "
                "Informe os números, valores, produtos e status das propostas e contratos dele conforme o dossiê fornecido.\n"
            )

            try:
                from app.services.ai_engine_service import ai_engine
                resposta_ia, telemetria_id = ai_engine.gerar_resposta_orquestrada(
                    pergunta=prefixo_cliente + mensagem,
                    contexto=contexto_total,
                    historico=historico_recente,
                    config_suporte=config_empresa,
                    empresa_id=empresa_id,
                    canal="telegram",
                    session_id=chat_id
                )
            except Exception as e:
                logger.warning(f"[Orquestrador] Fallback no ai_engine comercial: {e}")
                resposta_ia = self.ai_service.gerar_resposta(
                    pergunta=prefixo_cliente + mensagem,
                    contexto=contexto_total,
                    historico=historico_recente,
                    config_suporte=config_empresa,
                    empresa_id=empresa_id
                )
                telemetria_id = 0

            return resposta_ia, {
                "intencao": "CONSULTA_COMERCIAL",
                "cadastrado": contexto["cadastrado_completo"],
                "documentos_usados": docs_usados,
                "telemetria_id": telemetria_id,
                "contexto_publico": contexto_total
            }

        # --- AÇÃO: PERGUNTA LEGÍTIMA (RAG DA BASE DE CONHECIMENTO + CONTEXTO CLIENTE) ---
        from app.routes.telegram import buscar_contexto_relevante
        contexto_publico, docs_usados = buscar_contexto_relevante(pergunta=mensagem, empresa_id=empresa_id)

        # Se o cliente já tem propostas ou contratos, enriquece o contexto com o dossiê dele
        if contexto.get("propostas") or contexto.get("contratos") or contexto.get("negocio_ativo"):
            dossie = self.formatar_dossie_comercial_cliente(contexto)
            contexto_total = f"{dossie}\n\n[INFORMAÇÕES DA EMPRESA]:\n{contexto_publico}" if contexto_publico else dossie
        else:
            contexto_total = contexto_publico

        historico_recente = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=3, empresa_id=empresa_id)

        prompt_custom = config_empresa.get("ia_prompt_sistema")
        if prompt_custom and prompt_custom.strip():
            config_suporte = config_empresa.copy()
            config_suporte["mensagem_suporte"] = f"{config_empresa.get('mensagem_suporte', '')}\nDiretriz Adicional: {prompt_custom}"
        else:
            config_suporte = config_empresa

        prefixo_cliente = f"Cliente atendido: {nome_exibicao}. Chame-o pelo nome cordialmente.\n" if nome_exibicao else ""

        try:
            from app.services.ai_engine_service import ai_engine
            resposta_ia, telemetria_id = ai_engine.gerar_resposta_orquestrada(
                pergunta=prefixo_cliente + mensagem,
                contexto=contexto_total,
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
                contexto=contexto_total,
                historico=historico_recente,
                config_suporte=config_suporte,
                empresa_id=empresa_id
            )
            telemetria_id = 0

        remover_teclado = False
        if dados_contato.get("tem_contato"):
            if dados_contato.get("nome"):
                resposta_ia = f"Perfeito, {nome_exibicao}! Seus dados foram anotados com sucesso.\n\n" + resposta_ia
            if contexto.get("tem_telefone_valido"):
                remover_teclado = True

        return resposta_ia, {
            "intencao": "PERGUNTA_CONHECIMENTO",
            "cadastrado": contexto["cadastrado_completo"],
            "documentos_usados": docs_usados,
            "telemetria_id": telemetria_id,
            "contexto_publico": contexto_total,
            "exibir_botao_contato": False,
            "remover_teclado": remover_teclado
        }


# Instância global do Orquestrador
agent_orchestrator = AgentOrchestrator()
