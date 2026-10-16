import io
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from app.database.database import conectar, _cursor, _placeholder

logger = logging.getLogger(__name__)


def buscar_dados_proposta(proposta_id: int, empresa_id: int) -> Optional[Dict[str, Any]]:
    """Busca todos os dados necessários da proposta, cliente, produto e empresa para compor o PDF."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT 
                p.id, p.valor, p.condicoes_pagamento, p.validade_dias, p.descricao_itens, p.criado_em,
                cl.nome AS cliente_nome, cl.email AS cliente_email, cl.telefone AS cliente_telefone,
                COALESCE(prod.nome, prod_n.nome, 'Solução Especializada') AS produto_nome,
                COALESCE(prod.descricao, prod_n.descricao, 'Solução e assessoria sob medida.') AS produto_descricao,
                e.nome AS empresa_nome
            FROM propostas p
            JOIN empresas e ON p.empresa_id = e.id
            LEFT JOIN clientes cl ON p.cliente_id = cl.id
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            LEFT JOIN negocios n ON p.negocio_id = n.id
            LEFT JOIN produtos prod_n ON n.produto_id = prod_n.id
            WHERE p.id = {ph} AND (p.empresa_id = {ph} OR p.empresa_id IS NULL)
        """, (proposta_id, empresa_id))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[ProposalPDF] Erro ao buscar proposta #{proposta_id}: {e}")
        return None
    finally:
        conexao.close()


def gerar_pdf_proposta_bytes(proposta_id: int, empresa_id: int) -> Optional[bytes]:
    """
    Gera em memória os bytes de um documento PDF timbrado, elegante e profissional
    para a proposta comercial especificada.
    """
    dados = buscar_dados_proposta(proposta_id, empresa_id)
    if not dados:
        logger.warning(f"[ProposalPDF] Proposta #{proposta_id} não encontrada para empresa {empresa_id}.")
        return None

    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=40,
            leftMargin=40,
            topMargin=40,
            bottomMargin=40
        )

        styles = getSampleStyleSheet()

        # Paleta de cores corporativa
        COR_PRIMARIA = colors.HexColor("#1e293b")  # Slate 800
        COR_SECUNDARIA = colors.HexColor("#2563eb")  # Blue 600
        COR_TEXTO = colors.HexColor("#334155")  # Slate 700
        COR_FUNDO_CLARO = colors.HexColor("#f8fafc")  # Slate 50
        COR_BORDA = colors.HexColor("#e2e8f0")  # Slate 200

        # Estilos customizados
        style_empresa = ParagraphStyle(
            "EmpresaHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=COR_SECUNDARIA,
            spaceAfter=2
        )
        style_sub_empresa = ParagraphStyle(
            "EmpresaSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.HexColor("#64748b"),
            spaceAfter=15
        )
        style_titulo_doc = ParagraphStyle(
            "TituloDoc",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=COR_PRIMARIA,
            alignment=2,  # Direita
            spaceAfter=2
        )
        style_meta_doc = ParagraphStyle(
            "MetaDoc",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.HexColor("#64748b"),
            alignment=2,
            spaceAfter=15
        )
        style_secao_titulo = ParagraphStyle(
            "SecaoTitulo",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=COR_PRIMARIA,
            spaceBefore=12,
            spaceAfter=6
        )
        style_corpo = ParagraphStyle(
            "CorpoTexto",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=COR_TEXTO,
            leading=14
        )
        style_destaque = ParagraphStyle(
            "DestaqueTexto",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=COR_PRIMARIA,
            leading=14
        )

        elements = []

        # 1. Cabeçalho com Empresa à esquerda e Dados da Proposta à direita
        empresa_nome = dados.get("empresa_nome") or "FluxIA Plataforma Corporativa"
        cliente_nome = dados.get("cliente_nome") or "Cliente Especial"
        cliente_email = dados.get("cliente_email") or "Não informado"
        cliente_telefone = dados.get("cliente_telefone") or "Atendimento Telegram"

        criado_em = dados.get("criado_em")
        data_formatada = criado_em.strftime("%d/%m/%Y") if isinstance(criado_em, datetime) else str(criado_em)[:10]
        validade_dias = int(dados.get("validade_dias") or 15)

        col_esq = [
            Paragraph(f"<b>{empresa_nome.upper()}</b>", style_empresa),
            Paragraph("Soluções Inteligentes em CRM e Automação de Atendimento", style_sub_empresa),
        ]
        col_dir = [
            Paragraph("PROPOSTA COMERCIAL", style_titulo_doc),
            Paragraph(f"Emissão: {data_formatada} | Validade: {validade_dias} dias", style_meta_doc),
        ]

        tabela_header = Table([[col_esq, col_dir]], colWidths=[280, 250])
        tabela_header.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ]))
        elements.append(tabela_header)
        elements.append(HRFlowable(width="100%", thickness=1.5, color=COR_SECUNDARIA, spaceAfter=15))

        # 2. Dados do Destinatário
        elements.append(Paragraph("1. DESTINATÁRIO", style_secao_titulo))
        dados_destinatario = [
            [
                Paragraph("<b>Cliente:</b>", style_destaque), Paragraph(cliente_nome, style_corpo),
                Paragraph("<b>Telefone/Whats:</b>", style_destaque), Paragraph(cliente_telefone, style_corpo)
            ],
            [
                Paragraph("<b>E-mail:</b>", style_destaque), Paragraph(cliente_email, style_corpo),
                Paragraph("<b>Canal Oficial:</b>", style_destaque), Paragraph("Telegram Direto", style_corpo)
            ]
        ]
        tabela_cli = Table(dados_destinatario, colWidths=[70, 190, 90, 180])
        tabela_cli.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), COR_FUNDO_CLARO),
            ('BOX', (0,0), (-1,-1), 0.5, COR_BORDA),
            ('INNERGRID', (0,0), (-1,-1), 0.5, COR_BORDA),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        elements.append(tabela_cli)
        elements.append(Spacer(1, 15))

        # 3. Escopo e Solução Proposta
        elements.append(Paragraph("2. ESCOPO DOS SERVIÇOS & PRODUTOS", style_secao_titulo))
        produto_nome = dados.get("produto_nome") or "Solução Corporativa Especializada"
        produto_desc = dados.get("produto_descricao") or ""
        descricao_itens = dados.get("descricao_itens") or ""

        detalhe_escopo = descricao_itens or produto_desc or "Implantação e acompanhamento completo conforme diretrizes acordadas."
        texto_produto = f"<b>{produto_nome}</b><br/><br/>{detalhe_escopo}"
        
        tabela_escopo = Table([[Paragraph(texto_produto, style_corpo)]], colWidths=[530])
        tabela_escopo.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), COR_FUNDO_CLARO),
            ('BOX', (0,0), (-1,-1), 0.5, COR_BORDA),
            ('PADDING', (0,0), (-1,-1), 10),
        ]))
        elements.append(tabela_escopo)
        elements.append(Spacer(1, 15))

        # 4. Condições Comerciais e Investimento
        elements.append(Paragraph("3. CONDIÇÕES COMERCIAIS & INVESTIMENTO", style_secao_titulo))
        valor = float(dados.get("valor") or 0.0)
        valor_formatado = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        condicoes = dados.get("condicoes_pagamento") or "À vista via PIX ou em até 12x no cartão de crédito"

        tabela_precos_dados = [
            [
                Paragraph("<b>Item / Descrição</b>", style_destaque),
                Paragraph("<b>Condições de Pagamento</b>", style_destaque),
                Paragraph("<b>Total Proposto</b>", style_destaque)
            ],
            [
                Paragraph(produto_nome, style_corpo),
                Paragraph(condicoes, style_corpo),
                Paragraph(f"<b>{valor_formatado}</b>", style_destaque)
            ]
        ]
        tabela_precos = Table(tabela_precos_dados, colWidths=[200, 200, 130])
        tabela_precos.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f1f5f9")),
            ('BOX', (0,0), (-1,-1), 0.5, COR_BORDA),
            ('INNERGRID', (0,0), (-1,-1), 0.5, COR_BORDA),
            ('ALIGN', (2,0), (2,-1), 'RIGHT'),
            ('PADDING', (0,0), (-1,-1), 8),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        elements.append(tabela_precos)
        elements.append(Spacer(1, 15))

        # 5. Validade e Termos de Aceite
        elements.append(Paragraph("4. ACEITE & PRÓXIMOS PASSOS", style_secao_titulo))
        texto_aceite = (
            f"• <b>Validade da Proposta:</b> Esta condição comercial é válida por <b>{validade_dias} dias</b> a contar da data de emissão.<br/>"
            "• <b>Aceite Imediato:</b> Para aprovar esta proposta e dar início imediato ao seu atendimento ou implantação, "
            "basta responder <b>'Aprovar proposta'</b> ou <b>'Vamos fechar'</b> diretamente nesta conversa do Telegram.<br/>"
            "• <b>Dúvidas ou Ajustes:</b> Nossa equipe de especialistas e consultores comerciais está à disposição diretamente por este canal."
        )
        tabela_termos = Table([[Paragraph(texto_aceite, style_corpo)]], colWidths=[530])
        tabela_termos.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
            ('BOX', (0,0), (-1,-1), 0.5, COR_BORDA),
            ('PADDING', (0,0), (-1,-1), 10),
        ]))
        elements.append(tabela_termos)
        elements.append(Spacer(1, 25))

        # 6. Rodapé com linha de assinatura / autenticidade
        texto_rodape = f"Documento oficial emitido eletronicamente pela plataforma {empresa_nome}."
        elements.append(Paragraph(texto_rodape, style_meta_doc))

        # Gera o PDF
        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        logger.info(f"[ProposalPDF] Proposta #{proposta_id} gerada em PDF ({len(pdf_bytes)} bytes).")
        return pdf_bytes

    except Exception as e:
        logger.error(f"[ProposalPDF] Falha ao renderizar PDF da proposta #{proposta_id}: {e}")
        return None
