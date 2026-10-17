from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.services.ticket_service import (
    listar_tickets,
    obter_ticket_por_id,
    responder_ticket,
    atualizar_status_ticket,
    obter_metricas_tickets,
    criar_ticket_automatico,
    listar_mensagens_ticket,
    marcar_ticket_como_lido
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["Tickets & Tarefas IA"])


class ResponderTicketRequest(BaseModel):
    texto: str
    atendente_id: Optional[int] = None
    novo_status: Optional[str] = "em_atendimento"
    empresa_id: Optional[int] = None


class AtualizarStatusRequest(BaseModel):
    status: str
    atendente_id: Optional[int] = None
    empresa_id: Optional[int] = None


class CriarTicketManualRequest(BaseModel):
    empresa_id: int = 1
    cliente_id: Optional[int] = None
    telegram_chat_id: str
    titulo: str
    tipo: str = "proposta"
    ultima_mensagem: str
    cliente_nome: Optional[str] = ""
    cliente_telefone: Optional[str] = ""
    prioridade: Optional[str] = "alta"


@router.get("")
@router.get("/")
def listar_tickets_endpoint(
    empresa_id: int = Query(1, description="ID da empresa"),
    status: Optional[str] = Query(None, description="Filtro por status (aberto, em_atendimento, resolvido)"),
    tipo: Optional[str] = Query(None, description="Filtro por tipo (proposta, negociacao, suporte)"),
    busca: Optional[str] = Query(None, description="Termo de busca textual"),
    limite: int = Query(50, description="Limite de registros")
):
    """Lista tickets gerados pela IA ou atendentes para a empresa especificada."""
    try:
        tickets = listar_tickets(
            empresa_id=empresa_id,
            status=status,
            tipo=tipo,
            busca=busca,
            limite=limite
        )
        return {
            "sucesso": True,
            "total": len(tickets),
            "tickets": tickets
        }
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao listar tickets: {e}")
        raise HTTPException(status_code=500, detail=f"Falha ao carregar tickets: {str(e)}")


@router.get("/metricas")
def obter_metricas_endpoint(empresa_id: int = Query(1, description="ID da empresa")):
    """Retorna o sumário de métricas de tickets (abertos, em atendimento, resolvidos)."""
    try:
        metricas = obter_metricas_tickets(empresa_id=empresa_id)
        return {"sucesso": True, **metricas}
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao obter métricas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{ticket_id}")
def obter_ticket_endpoint(
    ticket_id: int,
    empresa_id: Optional[int] = Query(None, description="ID da empresa")
):
    """Obtém detalhes de um ticket específico."""
    ticket = obter_ticket_por_id(ticket_id=ticket_id, empresa_id=empresa_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket não encontrado.")
    return {"sucesso": True, "ticket": ticket}


@router.get("/{ticket_id}/mensagens")
def obter_mensagens_ticket_endpoint(
    ticket_id: int,
    empresa_id: int = Query(1, description="ID da empresa")
):
    """Retorna o histórico cronológico completo de mensagens trocadas dentro deste ticket."""
    try:
        mensagens = listar_mensagens_ticket(ticket_id=ticket_id, empresa_id=empresa_id)
        # Ao abrir as mensagens, marca automaticamente como lido
        marcar_ticket_como_lido(ticket_id=ticket_id, empresa_id=empresa_id)
        return {
            "sucesso": True,
            "ticket_id": ticket_id,
            "total": len(mensagens),
            "mensagens": mensagens
        }
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao listar mensagens do ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{ticket_id}/marcar-lido")
def marcar_ticket_lido_endpoint(
    ticket_id: int,
    empresa_id: int = Query(1, description="ID da empresa")
):
    """Marca o ticket como lido para atualizar contadores e badges."""
    try:
        marcar_ticket_como_lido(ticket_id=ticket_id, empresa_id=empresa_id)
        return {"sucesso": True, "ticket_id": ticket_id, "nao_lido": False}
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao marcar ticket como lido: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ticket_id}/responder")
def responder_ticket_endpoint(
    ticket_id: int,
    dados: ResponderTicketRequest,
    empresa_id: Optional[int] = Query(None)
):
    """
    Operador humano responde ao cliente através do ticket.
    A mensagem é enviada ao Telegram do cliente e o ticket é atualizado.
    """
    emp_id = dados.empresa_id or empresa_id or 1
    atend_id = dados.atendente_id or 1

    if not dados.texto or not dados.texto.strip():
        raise HTTPException(status_code=400, detail="O texto da resposta não pode ser vazio.")

    try:
        resultado = responder_ticket(
            ticket_id=ticket_id,
            texto_resposta=dados.texto.strip(),
            atendente_id=atend_id,
            empresa_id=emp_id,
            novo_status=dados.novo_status or "em_atendimento"
        )
        return {
            "sucesso": True,
            "mensagem": "Resposta enviada ao cliente com sucesso via Telegram!",
            "ticket": resultado
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao responder ticket: {e}")
        raise HTTPException(status_code=500, detail=f"Falha ao enviar resposta: {str(e)}")


@router.patch("/{ticket_id}/status")
def atualizar_status_endpoint(
    ticket_id: int,
    dados: AtualizarStatusRequest,
    empresa_id: Optional[int] = Query(None)
):
    """Atualiza o status de um ticket (aberto, em_atendimento, resolvido)."""
    emp_id = dados.empresa_id or empresa_id or 1
    status_permitidos = ["aberto", "em_atendimento", "resolvido"]
    if dados.status not in status_permitidos:
        raise HTTPException(
            status_code=400,
            detail=f"Status inválido. Escolha entre: {', '.join(status_permitidos)}"
        )

    try:
        resultado = atualizar_status_ticket(
            ticket_id=ticket_id,
            novo_status=dados.status,
            empresa_id=emp_id,
            atendente_id=dados.atendente_id
        )
        return {"sucesso": True, "ticket": resultado}
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao atualizar status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gerar-manual")
def criar_ticket_manual_endpoint(dados: CriarTicketManualRequest):
    """Cria um ticket manualmente para fins de teste ou transbordo direto."""
    try:
        ticket = criar_ticket_automatico(
            empresa_id=dados.empresa_id,
            cliente_id=dados.cliente_id,
            telegram_chat_id=dados.telegram_chat_id,
            tipo=dados.tipo,
            titulo=dados.titulo,
            ultima_mensagem_cliente=dados.ultima_mensagem,
            cliente_nome=dados.cliente_nome or "",
            cliente_telefone=dados.cliente_telefone or "",
            prioridade=dados.prioridade or "alta"
        )
        return {"sucesso": True, "ticket": ticket}
    except Exception as e:
        logger.error(f"[TicketsRoute] Erro ao criar ticket manual: {e}")
        raise HTTPException(status_code=500, detail=str(e))
