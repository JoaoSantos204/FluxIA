import logging
import json
import re
from typing import Optional, List, Dict, Any
from app.database.database import conectar, _cursor, _placeholder
from app.services.history_service import obter_ultimas_interacoes, salvar_interacao

logger = logging.getLogger(__name__)


def gerar_resumo_ia_ticket(
    chat_id: str,
    empresa_id: int,
    cliente_nome: str,
    cliente_telefone: str,
    ultima_mensagem: str,
    tipo: str = "proposta"
) -> str:
    """
    Gera uma síntese executiva da conversa com o cliente usando o Gemini.
    O resumo é formatado em tópicos claros para que o operador humano se ambiente
    imediatamente ao abrir o ticket.
    """
    try:
        from app.services.ai_service import AIService
        ai_svc = AIService()
        client = ai_svc.obter_client(empresa_id=empresa_id)

        # Recupera histórico recente de mensagens
        historico = obter_ultimas_interacoes(telegram_chat_id=chat_id, limite=8, empresa_id=empresa_id)
        conversa_formatada = ""
        for h in historico:
            u_msg = h.get("mensagem_usuario", "")
            ia_msg = h.get("resposta_ia", "")
            if u_msg:
                conversa_formatada += f"Cliente: {u_msg}\n"
            if ia_msg:
                conversa_formatada += f"IA: {ia_msg[:160]}...\n"

        if not conversa_formatada:
            conversa_formatada = f"Cliente: {ultima_mensagem}\n"

        prompt = f"""
        Você é o sintetizador de CRM e atendimento comercial do FluxIA.
        Um cliente no Telegram atingiu o estágio em que precisa de atendimento HUMANO para:
        TIPO DE DEMANDA: {'Elaboração de Proposta Comercial' if tipo == 'proposta' else 'Negociação de Valores / Termos de Contrato'}.

        DADOS DO CLIENTE:
        - Nome: {cliente_nome or 'Não identificado'}
        - WhatsApp / Telefone: {cliente_telefone or 'Não informado'}
        - Chat ID: {chat_id}
        - Última Mensagem: "{ultima_mensagem}"

        HISTÓRICO RECENTE DA CONVERSA:
        {conversa_formatada}

        Gere um RESUMO EXECUTIVO estruturado e direto ao ponto para que o operador comercial humano
        leia em menos de 10 segundos e saiba exatamente o que fazer.
        Estruture OBRIGATORIAMENTE no seguinte formato com marcadores:

        • **Cliente:** {cliente_nome or 'Lead'} {f'({cliente_telefone})' if cliente_telefone else ''}
        • **Interesse:** [Qual produto, plano ou serviço o cliente demonstrou interesse]
        • **Demanda do Ticket:** [O que o cliente pediu especificamente: proposta formal, desconto, formas de pagamento, detalhes do contrato]
        • **Histórico da Conversa:** [Resumo em 2 a 3 frases dos principais pontos conversados]
        • **Ação Recomendada:** [Instrução prática e direta para o operador fechar o negócio ou enviar a proposta]
        """

        resposta = client.models.generate_content(
            model=ai_svc.primary_model,
            contents=prompt
        )
        if resposta and resposta.text:
            return resposta.text.strip()
    except Exception as e:
        logger.warning(f"[TicketService] Falha ao gerar resumo IA com Gemini: {e}")

    # Fallback estruturado caso ocorra erro na API do Gemini
    return (
        f"• **Cliente:** {cliente_nome or 'Lead'} {f'({cliente_telefone})' if cliente_telefone else ''}\n"
        f"• **Interesse:** Demanda identificada via atendimento autônomo\n"
        f"• **Demanda do Ticket:** {'Solicitação de elaboração de proposta comercial' if tipo == 'proposta' else 'Negociação de contrato e condições comerciais'}\n"
        f"• **Histórico da Conversa:** Cliente solicitou atendimento humano após dialogar com o bot no Telegram. Última mensagem: \"{ultima_mensagem}\".\n"
        f"• **Ação Recomendada:** Entrar em contato com o cliente via Telegram ou WhatsApp para apresentar proposta personalizada e avançar na negociação."
    )


def criar_ticket_automatico(
    empresa_id: int,
    cliente_id: Optional[int],
    telegram_chat_id: str,
    tipo: str,
    titulo: str,
    ultima_mensagem_cliente: str,
    cliente_nome: str = "",
    cliente_telefone: str = "",
    prioridade: str = "alta"
) -> Dict[str, Any]:
    """
    Cria um novo ticket/tarefa de atendimento no banco de dados com resumo de IA.
    Evita duplicar tickets 'abertos' idênticos para o mesmo chat nos últimos 10 minutos.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        # Verifica se já existe um ticket aberto recentemente para este chat
        cursor.execute(f"""
            SELECT id, titulo, status, criado_em
            FROM tickets
            WHERE telegram_chat_id = {ph} AND empresa_id = {ph} AND status = 'aberto'
            ORDER BY id DESC LIMIT 1
        """, (str(telegram_chat_id), empresa_id))
        ticket_existente = cursor.fetchone()

        # Gera o resumo da conversa via IA
        resumo_ia = gerar_resumo_ia_ticket(
            chat_id=telegram_chat_id,
            empresa_id=empresa_id,
            cliente_nome=cliente_nome,
            cliente_telefone=cliente_telefone,
            ultima_mensagem=ultima_mensagem_cliente,
            tipo=tipo
        )

        if ticket_existente:
            # Atualiza o ticket aberto existente com a nova mensagem e resumo atualizado
            cursor.execute(f"""
                UPDATE tickets
                SET ultima_mensagem_cliente = {ph},
                    resumo_conversa = {ph},
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = {ph}
                RETURNING id, titulo, tipo, status, prioridade, resumo_conversa, criado_em
            """, (ultima_mensagem_cliente, resumo_ia, ticket_existente["id"]))
            row = cursor.fetchone()
            conexao.commit()
            return dict(row)

        cursor.execute(f"""
            INSERT INTO tickets (
                empresa_id, cliente_id, telegram_chat_id, titulo, tipo, status,
                prioridade, resumo_conversa, ultima_mensagem_cliente, canal
            ) VALUES (
                {ph}, {ph}, {ph}, {ph}, {ph}, 'aberto',
                {ph}, {ph}, {ph}, 'telegram'
            )
            RETURNING id, titulo, tipo, status, prioridade, resumo_conversa, criado_em
        """, (
            empresa_id,
            cliente_id,
            str(telegram_chat_id),
            titulo,
            tipo,
            prioridade,
            resumo_ia,
            ultima_mensagem_cliente
        ))
        row = cursor.fetchone()
        conexao.commit()
        return dict(row)

    except Exception as e:
        conexao.rollback()
        logger.error(f"[TicketService] Erro ao criar ticket: {e}")
        raise e
    finally:
        conexao.close()


def listar_tickets(
    empresa_id: int,
    status: Optional[str] = None,
    tipo: Optional[str] = None,
    busca: Optional[str] = None,
    limite: int = 50
) -> List[Dict[str, Any]]:
    """
    Lista tickets com dados enriquecidos do cliente e atendente.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        filtros = [f"t.empresa_id = {ph}"]
        params = [empresa_id]

        if status and status != "todos":
            filtros.append(f"t.status = {ph}")
            params.append(status)

        if tipo and tipo != "todos":
            filtros.append(f"t.tipo = {ph}")
            params.append(tipo)

        if busca and busca.strip():
            termo = f"%{busca.strip()}%"
            filtros.append(f"(t.titulo ILIKE {ph} OR t.resumo_conversa ILIKE {ph} OR c.nome ILIKE {ph} OR c.telefone ILIKE {ph} OR t.telegram_chat_id ILIKE {ph})")
            params.extend([termo, termo, termo, termo, termo])

        where_sql = f"WHERE {' AND '.join(filtros)}"

        query = f"""
            SELECT 
                t.id, t.empresa_id, t.cliente_id, t.telegram_chat_id,
                t.titulo, t.tipo, t.status, t.prioridade,
                t.resumo_conversa, t.ultima_mensagem_cliente,
                t.atendente_id, t.resposta_atendente, t.canal,
                t.criado_em, t.atualizado_em,
                c.nome AS cliente_nome,
                c.telefone AS cliente_telefone,
                c.email AS cliente_email,
                u.nome AS atendente_nome
            FROM tickets t
            LEFT JOIN clientes c ON c.id = t.cliente_id
            LEFT JOIN usuarios u ON u.id = t.atendente_id
            {where_sql}
            ORDER BY 
                CASE 
                    WHEN t.status = 'aberto' THEN 1 
                    WHEN t.status = 'em_atendimento' THEN 2 
                    ELSE 3 
                END,
                t.atualizado_em DESC
            LIMIT {ph}
        """
        params.append(limite)
        cursor.execute(query, tuple(params))
        linhas = cursor.fetchall()

        resultado = []
        for row in linhas:
            item = dict(row)
            item["criado_em"] = str(item["criado_em"]) if item.get("criado_em") else ""
            item["atualizado_em"] = str(item["atualizado_em"]) if item.get("atualizado_em") else ""
            resultado.append(item)

        return resultado

    finally:
        conexao.close()


def obter_ticket_por_id(ticket_id: int, empresa_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Recupera detalhes de um ticket específico."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        filtros = [f"t.id = {ph}"]
        params = [ticket_id]
        if empresa_id:
            filtros.append(f"t.empresa_id = {ph}")
            params.append(empresa_id)

        cursor.execute(f"""
            SELECT 
                t.*,
                c.nome AS cliente_nome,
                c.telefone AS cliente_telefone,
                c.email AS cliente_email,
                u.nome AS atendente_nome
            FROM tickets t
            LEFT JOIN clientes c ON c.id = t.cliente_id
            LEFT JOIN usuarios u ON u.id = t.atendente_id
            WHERE {' AND '.join(filtros)}
        """, tuple(params))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["criado_em"] = str(res["criado_em"]) if res.get("criado_em") else ""
        res["atualizado_em"] = str(res["atualizado_em"]) if res.get("atualizado_em") else ""
        return res
    finally:
        conexao.close()


def responder_ticket(
    ticket_id: int,
    texto_resposta: str,
    atendente_id: int,
    empresa_id: int,
    novo_status: str = "em_atendimento"
) -> Dict[str, Any]:
    """
    Envia a resposta do atendente humano diretamente para o cliente via Telegram,
    registra no histórico de conversas, vincula o operador e atualiza o ticket.
    """
    from app.routes.telegram import enviar_mensagem_telegram

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"SELECT * FROM tickets WHERE id = {ph} AND empresa_id = {ph}", (ticket_id, empresa_id))
        ticket = cursor.fetchone()
        if not ticket:
            raise ValueError("Ticket não encontrado ou não pertence a esta empresa.")

        chat_id = ticket["telegram_chat_id"]

        # 1. Envia a mensagem pelo Telegram
        if chat_id:
            enviar_mensagem_telegram(
                chat_id=chat_id,
                texto=texto_resposta,
                empresa_id=empresa_id
            )

            # 2. Grava no histórico de conversas
            salvar_interacao(
                telegram_chat_id=chat_id,
                mensagem_usuario="",
                resposta_ia=texto_resposta,
                lida=True,
                empresa_id=empresa_id
            )

            # 3. Define atendimento como humano na tabela de conversas
            cursor.execute(f"""
                INSERT INTO conversas_telegram (chat_id, status, atendente_id, empresa_id)
                VALUES ({ph}, 'humano', {ph}, {ph})
                ON CONFLICT (chat_id) DO UPDATE SET 
                    status = 'humano', 
                    atendente_id = {ph}, 
                    atualizado_em = CURRENT_TIMESTAMP
            """, (chat_id, atendente_id, empresa_id, atendente_id))

        # 4. Atualiza o ticket
        cursor.execute(f"""
            UPDATE tickets
            SET status = {ph},
                atendente_id = {ph},
                resposta_atendente = {ph},
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = {ph}
            RETURNING id, status, atendente_id, resposta_atendente, atualizado_em
        """, (novo_status, atendente_id, texto_resposta, ticket_id))
        atualizado = cursor.fetchone()
        conexao.commit()

        return dict(atualizado)

    except Exception as e:
        conexao.rollback()
        logger.error(f"[TicketService] Falha ao responder ticket {ticket_id}: {e}")
        raise e
    finally:
        conexao.close()


def atualizar_status_ticket(
    ticket_id: int,
    novo_status: str,
    empresa_id: int,
    atendente_id: Optional[int] = None
) -> Dict[str, Any]:
    """Atualiza o status de um ticket (aberto, em_atendimento, resolvido)."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        set_clauses = [f"status = {ph}", "atualizado_em = CURRENT_TIMESTAMP"]
        params = [novo_status]

        if atendente_id:
            set_clauses.append(f"atendente_id = {ph}")
            params.append(atendente_id)

        params.extend([ticket_id, empresa_id])

        cursor.execute(f"""
            UPDATE tickets
            SET {', '.join(set_clauses)}
            WHERE id = {ph} AND empresa_id = {ph}
            RETURNING id, status, atendente_id, atualizado_em
        """, tuple(params))
        row = cursor.fetchone()
        conexao.commit()

        if not row:
            raise ValueError("Ticket não encontrado.")

        return dict(row)
    finally:
        conexao.close()


def obter_metricas_tickets(empresa_id: int) -> Dict[str, int]:
    """Retorna contadores de tickets para badges e dashboard."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT 
                COUNT(*) AS total,
                COUNT(CASE WHEN status = 'aberto' THEN 1 END) AS abertos,
                COUNT(CASE WHEN status = 'em_atendimento' THEN 1 END) AS em_atendimento,
                COUNT(CASE WHEN status = 'resolvido' THEN 1 END) AS resolvidos
            FROM tickets
            WHERE empresa_id = {ph}
        """, (empresa_id,))
        row = cursor.fetchone()
        if not row:
            return {"total": 0, "abertos": 0, "em_atendimento": 0, "resolvidos": 0}
        return {
            "total": int(row["total"] or 0),
            "abertos": int(row["abertos"] or 0),
            "em_atendimento": int(row["em_atendimento"] or 0),
            "resolvidos": int(row["resolvidos"] or 0)
        }
    finally:
        conexao.close()
