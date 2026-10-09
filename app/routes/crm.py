import os
import re
import json
import logging
import hashlib
from typing import Optional, List
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Query, Body, Header, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.database.database import conectar, _cursor, _placeholder
from app.services.security_service import validar_perfil_admin_ou_master, exigir_perfil
from app.services.document_service import salvar_e_indexar_documento_texto, ler_documento
from app.services.chunk_service import dividir_texto
from app.services.embedding_service import gerar_embedding

logger = logging.getLogger(__name__)

router = APIRouter(tags=["CRM"])

# ============================================================================
# SCHEMAS PYDANTIC
# ============================================================================

class ProdutoCreate(BaseModel):
    empresa_id: int = 1
    nome: str
    descricao: Optional[str] = None
    preco: Optional[float] = 0.0
    ativo: bool = True
    pipeline_id: Optional[int] = None
    usuario_id: Optional[int] = None

class ProdutoUpdate(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    preco: Optional[float] = None
    ativo: Optional[bool] = None
    pipeline_id: Optional[int] = None
    usuario_id: Optional[int] = None

class ClienteCreate(BaseModel):
    empresa_id: int = 1
    nome: str
    telefone: Optional[str] = None
    email: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    origem: str = "portal"

class ClienteUpdate(BaseModel):
    nome: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    origem: Optional[str] = None

class EtapaCreate(BaseModel):
    nome: str
    ordem: Optional[int] = None
    cor: Optional[str] = "#3b82f6"
    usuario_id: Optional[int] = None

class EtapaUpdate(BaseModel):
    nome: Optional[str] = None
    ordem: Optional[int] = None
    cor: Optional[str] = None
    usuario_id: Optional[int] = None

class EtapasReordenarRequest(BaseModel):
    ordem_ids: List[int]
    usuario_id: Optional[int] = None

class PipelineCreate(BaseModel):
    empresa_id: int = 1
    nome: str
    produto_id: Optional[int] = None
    padrao: bool = False
    etapas: Optional[List[dict]] = None
    usuario_id: Optional[int] = None

class PipelineUpdate(BaseModel):
    nome: Optional[str] = None
    produto_id: Optional[int] = None
    padrao: Optional[bool] = None
    usuario_id: Optional[int] = None

class NegocioCreate(BaseModel):
    empresa_id: int = 1
    cliente_id: int
    produto_id: Optional[int] = None
    etapa_id: Optional[int] = None
    valor_estimado: float = 0.0
    estagio: Optional[str] = None

class NegocioUpdate(BaseModel):
    etapa_id: Optional[int] = None
    estagio: Optional[str] = None
    valor_estimado: Optional[float] = None
    produto_id: Optional[int] = None
    proposta_enviada: Optional[bool] = None

class ContratoUpdate(BaseModel):
    status: str  # 'pendente', 'assinado', 'cancelado'
    valor_contrato: Optional[float] = None

class GerarPropostaRequest(BaseModel):
    condicoes_pagamento: str
    validade_dias: int = 15
    enviar_email: bool = True

class PropostaDirectCreate(BaseModel):
    empresa_id: int = 1
    cliente_id: Optional[int] = None
    cliente_nome: str
    cliente_email: Optional[str] = None
    cliente_telefone: Optional[str] = None
    produto_id: Optional[int] = None
    produto_nome: Optional[str] = None
    valor: float = 0.0
    condicoes_pagamento: Optional[str] = "À vista via PIX ou em 12x no cartão de crédito."
    validade_dias: Optional[int] = 15
    descricao_itens: Optional[str] = None
    enviar_email: bool = False
    usuario_id: Optional[int] = None

class PropostaUpdate(BaseModel):
    valor: Optional[float] = None
    validade_dias: Optional[int] = None
    condicoes_pagamento: Optional[str] = None
    descricao_itens: Optional[str] = None
    produto_id: Optional[int] = None
    produto_nome: Optional[str] = None


# ============================================================================
# HELPERS DE E-MAIL E TEMPLATE (PARTE 27)
# ============================================================================

def gerar_template_proposta_html(
    cliente_nome: str,
    cliente_email: str,
    cliente_telefone: str,
    produto_nome: str,
    produto_descricao: str,
    valor: float,
    condicoes: str,
    validade_dias: int,
    empresa_nome: str,
    proposta_id: int
) -> str:
    hoje = datetime.now().strftime("%d/%m/%Y")
    data_validade = (datetime.now() + timedelta(days=validade_dias)).strftime("%d/%m/%Y")
    valor_fmt = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1e293b; background: #f8fafc; padding: 24px; margin: 0; }}
  .card {{ max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 16px rgba(0,0,0,0.06); padding: 36px; }}
  .header {{ border-bottom: 2px solid #22c55e; padding-bottom: 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }}
  .title {{ font-size: 24px; font-weight: 800; color: #0f172a; margin: 0; }}
  .badge {{ background: #dcfce7; color: #15803d; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 999px; text-transform: uppercase; }}
  .meta {{ font-size: 13px; color: #64748b; margin-bottom: 20px; line-height: 1.6; }}
  .section {{ margin-bottom: 22px; }}
  .section-title {{ font-size: 13px; font-weight: 700; text-transform: uppercase; color: #475569; margin-bottom: 8px; border-bottom: 1px solid #f1f5f9; padding-bottom: 4px; }}
  .highlight-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; }}
  .price {{ font-size: 28px; font-weight: 800; color: #15803d; margin: 10px 0 4px; }}
  .footer {{ margin-top: 36px; padding-top: 18px; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; text-align: center; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <div>
      <h1 class="title">Proposta Comercial #{proposta_id}</h1>
      <div style="font-size:14px; color:#64748b; margin-top:4px;">{empresa_nome}</div>
    </div>
    <span class="badge">Válida até {data_validade}</span>
  </div>

  <div class="meta">
    <strong>Data de Emissão:</strong> {hoje}<br>
    <strong>Cliente Destinatário:</strong> {cliente_nome}<br>
    <strong>Contato:</strong> {cliente_email or 'Sem email'} · {cliente_telefone or 'Sem telefone'}
  </div>

  <div class="section">
    <div class="section-title">Item / Solução Proposta</div>
    <div class="highlight-box">
      <div style="font-size:18px; font-weight:700; color:#0f172a;">{produto_nome}</div>
      <div style="font-size:14px; color:#475569; margin-top:6px; line-height:1.5;">{produto_descricao or 'Solução personalizada conforme alinhamento com consultor.'}</div>
      <div class="price">{valor_fmt}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Condições de Pagamento e Prazos</div>
    <div style="font-size:14px; color:#334155; line-height:1.6; background:#fff; padding:14px; border:1px solid #e2e8f0; border-radius:8px;">
      {condicoes}
    </div>
  </div>

  <div class="section">
    <div class="section-title">Validade da Oferta</div>
    <div style="font-size:13px; color:#64748b;">
      Esta proposta comercial possui validade garantida por <strong>{validade_dias} dias</strong> a partir de {hoje}.
    </div>
  </div>

  <div class="footer">
    Documento gerado automaticamente pelo CRM FluxIA em nome de {empresa_nome}.
  </div>
</div>
</body>
</html>"""
    return html


def disparar_email_proposta(destinatario: str, assunto: str, html_corpo: str, empresa_nome: str) -> tuple[bool, str]:
    """Envia a proposta por e-mail via smtplib se configurado no ambiente."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT") or 587)
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")

    if not host or not user or not password:
        logger.info(f"[Propostas] SMTP não configurado (HOST/USER/PASSWORD). Proposta salva e envio pulado.")
        return False, "SMTP não configurado no servidor (variáveis SMTP_HOST/USER). A proposta foi gerada, salva e integrada à base de conhecimento."

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{empresa_nome} <{user}>"
        msg["To"] = destinatario

        part_html = MIMEText(html_corpo, "html", "utf-8")
        msg.attach(part_html)

        server = smtplib.SMTP(host, port, timeout=12)
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [destinatario], msg.as_string())
        server.quit()
        logger.info(f"[Propostas] E-mail de proposta enviado com sucesso para {destinatario}.")
        return True, "E-mail da proposta enviado com sucesso ao cliente!"
    except Exception as e:
        logger.error(f"[Propostas] Falha ao enviar e-mail para {destinatario}: {e}")
        return False, f"Proposta salva no sistema, mas houve falha no envio do e-mail: {str(e)}"


# ============================================================================
# ENDPOINTS: PRODUTOS (CRUD com proteção RBAC)
# ============================================================================

@router.get("/produtos")
def listar_produtos(request: Request = None, empresa_id: Optional[int] = Query(None), apenas_ativos: bool = Query(False)):
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        where = f"WHERE p.empresa_id = {ph}"
        params = [target_empresa]
        if apenas_ativos:
            where += f" AND p.ativo = {ph}"
            params.append(True)

        cursor.execute(f"""
            SELECT p.id, p.empresa_id, p.nome, p.descricao, p.preco, p.ativo, p.pipeline_id,
                   pipe.nome AS pipeline_nome
            FROM produtos p
            LEFT JOIN pipelines pipe ON p.pipeline_id = pipe.id
            {where}
            ORDER BY p.id ASC
        """, tuple(params))
        produtos = cursor.fetchall()
        return {"total": len(produtos), "produtos": [dict(p) for p in produtos]}
    finally:
        conexao.close()


@router.post("/produtos", status_code=status.HTTP_201_CREATED)
def criar_produto(dados: ProdutoCreate, request: Request = None):
    operador = exigir_perfil(request, dados.empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (dados.empresa_id or 1)

    nome_limpo = dados.nome.strip()
    if not nome_limpo:
        raise HTTPException(status_code=400, detail="Nome do produto é obrigatório.")

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # Unicidade de nome por empresa
        cursor.execute(f"""
            SELECT id FROM produtos
            WHERE empresa_id = {ph} AND LOWER(TRIM(nome)) = {ph}
        """, (target_empresa, nome_limpo.lower()))
        if cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Já existe um produto com este nome.")

        pipeline_id = dados.pipeline_id
        if pipeline_id:
            cursor.execute(f"SELECT id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
            if not cursor.fetchone():
                pipeline_id = None

        cursor.execute(f"""
            INSERT INTO produtos (empresa_id, nome, descricao, preco, ativo, pipeline_id)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
        """, (target_empresa, nome_limpo, dados.descricao, float(dados.preco or 0.0), dados.ativo, pipeline_id))

        prod_id = cursor.fetchone()["id"]
        if pipeline_id:
            cursor.execute(f"UPDATE pipelines SET produto_id = {ph} WHERE id = {ph} AND empresa_id = {ph}", (prod_id, pipeline_id, target_empresa))

        conexao.commit()
        return {"id": prod_id, "mensagem": f"Produto '{nome_limpo}' cadastrado com sucesso!"}
    finally:
        conexao.close()


@router.put("/produtos/{produto_id}")
def atualizar_produto(produto_id: int, dados: ProdutoUpdate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM produtos WHERE id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Produto não encontrado.")

        campos = []
        valores = []
        if dados.nome is not None:
            nome_limpo = dados.nome.strip()
            if not nome_limpo:
                raise HTTPException(status_code=400, detail="Nome do produto não pode ser vazio.")
            # Unicidade de nome por empresa em outro registro
            cursor.execute(f"""
                SELECT id FROM produtos
                WHERE empresa_id = {ph} AND LOWER(TRIM(nome)) = {ph} AND id != {ph}
            """, (target_empresa, nome_limpo.lower(), produto_id))
            if cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Já existe um produto com este nome.")

            campos.append(f"nome = {ph}")
            valores.append(nome_limpo)

        if dados.descricao is not None:
            campos.append(f"descricao = {ph}")
            valores.append(dados.descricao)
        if dados.preco is not None:
            campos.append(f"preco = {ph}")
            valores.append(float(dados.preco))
        if dados.ativo is not None:
            campos.append(f"ativo = {ph}")
            valores.append(dados.ativo)
        if dados.pipeline_id is not None:
            pipe_id = dados.pipeline_id if dados.pipeline_id > 0 else None
            if pipe_id:
                cursor.execute(f"SELECT id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipe_id, target_empresa))
                if not cursor.fetchone():
                    pipe_id = None
            campos.append(f"pipeline_id = {ph}")
            valores.append(pipe_id)
            if pipe_id:
                cursor.execute(f"UPDATE pipelines SET produto_id = {ph} WHERE id = {ph} AND empresa_id = {ph}", (produto_id, pipe_id, target_empresa))

        if not campos:
            return {"mensagem": "Nenhum campo para atualizar."}

        valores.extend([produto_id, target_empresa])
        cursor.execute(f"""
            UPDATE produtos SET {', '.join(campos)} WHERE id = {ph} AND empresa_id = {ph}
        """, tuple(valores))

        conexao.commit()
        return {"mensagem": f"Produto ID {produto_id} atualizado com sucesso."}
    finally:
        conexao.close()


@router.patch("/produtos/{produto_id}/toggle-ativo")
def alternar_ativo_produto(produto_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id, ativo, nome FROM produtos WHERE id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
        prod = cursor.fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Produto não encontrado.")

        novo_status = not bool(prod["ativo"])
        cursor.execute(f"UPDATE produtos SET ativo = {ph} WHERE id = {ph} AND empresa_id = {ph}", (novo_status, produto_id, target_empresa))
        conexao.commit()
        return {"mensagem": f"Produto '{prod['nome']}' agora está {'ativo' if novo_status else 'inativo'}.", "ativo": novo_status}
    finally:
        conexao.close()


@router.delete("/produtos/{produto_id}")
def excluir_produto(produto_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id, nome FROM produtos WHERE id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
        prod = cursor.fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Produto não encontrado.")

        cursor.execute(f"SELECT COUNT(*) as qtd FROM negocios WHERE produto_id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
        qtd_negocios = cursor.fetchone()["qtd"]

        cursor.execute(f"SELECT COUNT(*) as qtd FROM propostas WHERE produto_id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
        qtd_propostas = cursor.fetchone()["qtd"]

        if qtd_negocios > 0 or qtd_propostas > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Não é possível excluir o produto pois existem {qtd_negocios} negócio(s) e {qtd_propostas} proposta(s) vinculados a ele. Sugerimos desativar o produto."
            )

        # Se só houver o pipeline do produto (sem negócios): excluir etapas, pipeline e o produto
        cursor.execute(f"""
            DELETE FROM etapas_pipeline 
            WHERE pipeline_id IN (SELECT id FROM pipelines WHERE produto_id = {ph} AND empresa_id = {ph})
        """, (produto_id, target_empresa))
        cursor.execute(f"DELETE FROM pipelines WHERE produto_id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
        cursor.execute(f"DELETE FROM produtos WHERE id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))

        conexao.commit()
        return {"sucesso": True, "mensagem": f"Produto '{prod['nome']}' e seu pipeline foram excluídos com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: CLIENTES (CRM CRUD COMPLETO)
# ============================================================================

@router.get("/clientes")
def listar_clientes(request: Request = None, empresa_id: Optional[int] = Query(None), busca: Optional[str] = Query(None)):
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        filtros = [f"empresa_id = {ph}"]
        params = [target_empresa]

        if busca and busca.strip():
            termo = f"%{busca.strip()}%"
            filtros.append(f"(nome ILIKE {ph} OR email ILIKE {ph} OR telefone ILIKE {ph} OR telegram_chat_id ILIKE {ph})")
            params.extend([termo, termo, termo, termo])

        where = "WHERE " + " AND ".join(filtros)
        cursor.execute(f"""
            SELECT id, empresa_id, nome, telefone, email, telegram_chat_id, origem, criado_em
            FROM clientes
            {where}
            ORDER BY id DESC
        """, tuple(params))
        clientes = cursor.fetchall()
        return {"total": len(clientes), "clientes": [dict(c) for c in clientes]}
    finally:
        conexao.close()


@router.post("/clientes", status_code=status.HTTP_201_CREATED)
def criar_cliente(dados: ClienteCreate, request: Request = None):
    operador = exigir_perfil(request, dados.empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (dados.empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            INSERT INTO clientes (empresa_id, nome, telefone, email, telegram_chat_id, origem)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
        """, (target_empresa, dados.nome, dados.telefone, dados.email, dados.telegram_chat_id, dados.origem))
        row = cursor.fetchone()
        novo_id = row["id"] if isinstance(row, dict) else row[0]

        conexao.commit()
        return {"id": novo_id, "mensagem": f"Cliente '{dados.nome}' cadastrado com sucesso!"}
    finally:
        conexao.close()


@router.get("/clientes/{cliente_id}")
def obter_cliente(cliente_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome, telefone, email, telegram_chat_id, origem, criado_em
            FROM clientes WHERE id = {ph} AND empresa_id = {ph}
        """, (cliente_id, target_empresa))
        cliente = cursor.fetchone()
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente não encontrado.")

        cursor.execute(f"""
            SELECT n.id, n.valor_estimado, n.estagio, n.etapa_id, n.proposta_enviada, n.ultima_interacao_em,
                   ep.nome AS etapa_nome, ep.cor AS etapa_cor, p.nome AS produto_nome
            FROM negocios n
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            WHERE n.cliente_id = {ph} AND n.empresa_id = {ph}
            ORDER BY n.id DESC
        """, (cliente_id, target_empresa))
        negocios = cursor.fetchall()

        res = dict(cliente)
        res["negocios"] = [dict(n) for n in negocios]
        return res
    finally:
        conexao.close()


@router.put("/clientes/{cliente_id}")
def atualizar_cliente(cliente_id: int, dados: ClienteUpdate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM clientes WHERE id = {ph} AND empresa_id = {ph}", (cliente_id, target_empresa))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cliente não encontrado.")

        campos = []
        valores = []
        if dados.nome is not None:
            campos.append(f"nome = {ph}")
            valores.append(dados.nome)
        if dados.telefone is not None:
            campos.append(f"telefone = {ph}")
            valores.append(dados.telefone)
        if dados.email is not None:
            campos.append(f"email = {ph}")
            valores.append(dados.email)
        if dados.telegram_chat_id is not None:
            campos.append(f"telegram_chat_id = {ph}")
            valores.append(dados.telegram_chat_id)
        if dados.origem is not None:
            campos.append(f"origem = {ph}")
            valores.append(dados.origem)

        if not campos:
            return {"mensagem": "Nenhum dado para atualizar."}

        valores.extend([cliente_id, target_empresa])
        cursor.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id = {ph} AND empresa_id = {ph}", tuple(valores))
        conexao.commit()
        return {"mensagem": "Cliente atualizado com sucesso."}
    finally:
        conexao.close()


@router.delete("/clientes/{cliente_id}")
def excluir_cliente(cliente_id: int, request: Request = None, empresa_id: Optional[int] = Query(None), confirmar: bool = Query(False)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id, nome FROM clientes WHERE id = {ph} AND empresa_id = {ph}", (cliente_id, target_empresa))
        cli = cursor.fetchone()
        if not cli:
            raise HTTPException(status_code=404, detail="Cliente não encontrado.")

        # Bloqueia se existir contrato assinado
        cursor.execute(f"""
            SELECT COUNT(*) as qtd FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            WHERE n.cliente_id = {ph} AND c.status = 'assinado'
        """, (cliente_id,))
        if cursor.fetchone()["qtd"] > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Não é possível excluir o cliente pois existem contratos assinados vinculados a ele."
            )

        cursor.execute(f"SELECT COUNT(*) as qtd FROM negocios WHERE cliente_id = {ph} AND empresa_id = {ph}", (cliente_id, target_empresa))
        qtd_neg = cursor.fetchone()["qtd"]

        cursor.execute(f"""
            SELECT COUNT(*) as qtd FROM propostas 
            WHERE (cliente_id = {ph} OR negocio_id IN (SELECT id FROM negocios WHERE cliente_id = {ph}))
              AND empresa_id = {ph}
        """, (cliente_id, cliente_id, target_empresa))
        qtd_prop = cursor.fetchone()["qtd"]

        cursor.execute(f"""
            SELECT COUNT(*) as qtd FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            WHERE n.cliente_id = {ph} AND n.empresa_id = {ph}
        """, (cliente_id, target_empresa))
        qtd_cont = cursor.fetchone()["qtd"]

        if not confirmar and (qtd_neg > 0 or qtd_prop > 0 or qtd_cont > 0):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Este cliente possui {qtd_neg} negócio(s), {qtd_prop} proposta(s) e {qtd_cont} contrato(s) que também serão excluídos. Confirme a exclusão para prosseguir."
            )

        # Buscar IDs de contratos e propostas vinculados
        cursor.execute(f"""
            SELECT c.id FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            WHERE n.cliente_id = {ph}
        """, (cliente_id,))
        contratos_ids = [r["id"] for r in cursor.fetchall()]

        cursor.execute(f"""
            SELECT id FROM propostas 
            WHERE (cliente_id = {ph} OR negocio_id IN (SELECT id FROM negocios WHERE cliente_id = {ph}))
              AND empresa_id = {ph}
        """, (cliente_id, cliente_id, target_empresa))
        propostas_ids = [r["id"] for r in cursor.fetchall()]

        # Excluir documentos sintéticos e seus chunks
        if contratos_ids:
            cursor.execute(f"""
                DELETE FROM chunks WHERE documento_id IN (
                    SELECT id FROM documentos WHERE ref_tipo = 'contrato' AND ref_id = ANY({ph})
                )
            """, (contratos_ids,))
            cursor.execute(f"DELETE FROM documentos WHERE ref_tipo = 'contrato' AND ref_id = ANY({ph})", (contratos_ids,))
            cursor.execute(f"DELETE FROM contratos WHERE id = ANY({ph})", (contratos_ids,))

        if propostas_ids:
            cursor.execute(f"""
                DELETE FROM chunks WHERE documento_id IN (
                    SELECT id FROM documentos WHERE ref_tipo = 'proposta' AND ref_id = ANY({ph})
                )
            """, (propostas_ids,))
            cursor.execute(f"DELETE FROM documentos WHERE ref_tipo = 'proposta' AND ref_id = ANY({ph})", (propostas_ids,))
            cursor.execute(f"DELETE FROM propostas WHERE id = ANY({ph})", (propostas_ids,))

        cursor.execute(f"DELETE FROM negocios WHERE cliente_id = {ph} AND empresa_id = {ph}", (cliente_id, target_empresa))
        cursor.execute(f"DELETE FROM clientes WHERE id = {ph} AND empresa_id = {ph}", (cliente_id, target_empresa))

        conexao.commit()
        return {"sucesso": True, "mensagem": f"Cliente '{cli['nome']}' e seus registros vinculados foram excluídos com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: PIPELINES & ETAPAS (PARTE 22 - PIPELINES CONFIGURÁVEIS)
# ============================================================================

@router.get("/pipelines")
def listar_pipelines(request: Request = None, empresa_id: Optional[int] = Query(None)):
    """Lista todos os pipelines da empresa, cada um com suas etapas ordenadas."""
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT p.id, p.empresa_id, p.nome, p.produto_id, p.padrao, p.criado_em,
                   prod.nome AS produto_nome
            FROM pipelines p
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            WHERE p.empresa_id = {ph}
            ORDER BY p.padrao DESC, p.id ASC
        """, (target_empresa,))
        pipelines_raw = cursor.fetchall()

        resultado = []
        for p in pipelines_raw:
            pipe_dict = dict(p)
            cursor.execute(f"""
                SELECT id, pipeline_id, nome, ordem, cor
                FROM etapas_pipeline
                WHERE pipeline_id = {ph}
                ORDER BY ordem ASC, id ASC
            """, (p["id"],))
            pipe_dict["etapas"] = [dict(e) for e in cursor.fetchall()]
            resultado.append(pipe_dict)

        return {"total": len(resultado), "pipelines": resultado}
    finally:
        conexao.close()


@router.post("/pipelines", status_code=status.HTTP_201_CREATED)
def criar_pipeline(dados: PipelineCreate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, dados.empresa_id or empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (dados.empresa_id or empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # Validação 1 pipeline por produto (se vinculado diretamente ao produto no pipeline)
        if dados.produto_id:
            cursor.execute(f"SELECT id FROM pipelines WHERE empresa_id = {ph} AND produto_id = {ph}", (target_empresa, dados.produto_id))
            if cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este produto já possui um pipeline.")

        # Se for marcado como padrão, desmarca outros pipelines da mesma empresa
        if dados.padrao:
            cursor.execute(f"UPDATE pipelines SET padrao = {ph} WHERE empresa_id = {ph}", (False, target_empresa))

        cursor.execute(f"""
            INSERT INTO pipelines (empresa_id, nome, produto_id, padrao)
            VALUES ({ph}, {ph}, {ph}, {ph})
            RETURNING id
        """, (target_empresa, dados.nome, dados.produto_id, dados.padrao))
        pipe_id = cursor.fetchone()["id"]

        # Cria etapas
        etapas_para_criar = dados.etapas or [
            {"nome": "Novo", "ordem": 1, "cor": "#3b82f6"},
            {"nome": "Qualificado", "ordem": 2, "cor": "#6366f1"},
            {"nome": "Proposta", "ordem": 3, "cor": "#a855f7"},
            {"nome": "Negociação", "ordem": 4, "cor": "#f59e0b"},
            {"nome": "Fechado", "ordem": 5, "cor": "#22c55e"},
            {"nome": "Perdido", "ordem": 6, "cor": "#ef4444"}
        ]

        for i, et in enumerate(etapas_para_criar, 1):
            nome_etapa = et.get("nome", f"Etapa {i}")
            ordem_etapa = et.get("ordem", i)
            cor_etapa = et.get("cor", "#3b82f6")
            cursor.execute(f"""
                INSERT INTO etapas_pipeline (pipeline_id, nome, ordem, cor)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (pipe_id, nome_etapa, ordem_etapa, cor_etapa))

        conexao.commit()
        return {"sucesso": True, "id": pipe_id, "mensagem": f"Pipeline '{dados.nome}' criado com sucesso!"}
    finally:
        conexao.close()


@router.patch("/pipelines/{pipeline_id}")
def atualizar_pipeline(pipeline_id: int, dados: PipelineUpdate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id, empresa_id, produto_id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        p = cursor.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Pipeline não encontrado.")

        if dados.produto_id is not None:
            novo_prod_id = dados.produto_id if dados.produto_id > 0 else None
            if novo_prod_id:
                cursor.execute(f"SELECT id FROM pipelines WHERE empresa_id = {ph} AND produto_id = {ph} AND id != {ph}", (target_empresa, novo_prod_id, pipeline_id))
                if cursor.fetchone():
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este produto já possui um pipeline.")

        if dados.padrao:
            cursor.execute(f"UPDATE pipelines SET padrao = {ph} WHERE empresa_id = {ph}", (False, target_empresa))

        campos = []
        valores = []
        if dados.nome is not None:
            campos.append(f"nome = {ph}")
            valores.append(dados.nome)
        if dados.produto_id is not None:
            campos.append(f"produto_id = {ph}")
            valores.append(dados.produto_id if dados.produto_id > 0 else None)
        if dados.padrao is not None:
            campos.append(f"padrao = {ph}")
            valores.append(dados.padrao)

        if campos:
            valores.extend([pipeline_id, target_empresa])
            cursor.execute(f"UPDATE pipelines SET {', '.join(campos)} WHERE id = {ph} AND empresa_id = {ph}", tuple(valores))
            conexao.commit()

        return {"sucesso": True, "mensagem": "Pipeline atualizado com sucesso."}
    finally:
        conexao.close()


@router.delete("/pipelines/{pipeline_id}")
def excluir_pipeline(pipeline_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id, nome, padrao, empresa_id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        pipe = cursor.fetchone()
        if not pipe:
            raise HTTPException(status_code=404, detail="Pipeline não encontrado.")

        if pipe["padrao"]:
            cursor.execute(f"SELECT COUNT(*) AS total FROM pipelines WHERE empresa_id = {ph}", (target_empresa,))
            if cursor.fetchone()["total"] <= 1:
                raise HTTPException(status_code=400, detail="Não é possível excluir o único pipeline da empresa.")

        # Verifica se há negócios associados às etapas deste pipeline
        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM negocios
            WHERE etapa_id IN (SELECT id FROM etapas_pipeline WHERE pipeline_id = {ph})
        """, (pipeline_id,))
        total_negocios = cursor.fetchone()["total"]
        if total_negocios > 0:
            raise HTTPException(status_code=400, detail=f"Este pipeline possui {total_negocios} negócio(s) vinculado(s). Mova os negócios antes de excluir.")

        cursor.execute(f"DELETE FROM etapas_pipeline WHERE pipeline_id = {ph}", (pipeline_id,))
        cursor.execute(f"DELETE FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        conexao.commit()
        return {"sucesso": True, "mensagem": f"Pipeline '{pipe['nome']}' excluído com sucesso."}
    finally:
        conexao.close()


@router.post("/pipelines/{pipeline_id}/etapas")
def criar_etapa_pipeline(pipeline_id: int, dados: EtapaCreate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Pipeline não encontrado.")

        ordem = dados.ordem
        if ordem is None:
            cursor.execute(f"SELECT COALESCE(MAX(ordem), 0) + 1 AS proxima FROM etapas_pipeline WHERE pipeline_id = {ph}", (pipeline_id,))
            ordem = cursor.fetchone()["proxima"]

        cursor.execute(f"""
            INSERT INTO etapas_pipeline (pipeline_id, nome, ordem, cor)
            VALUES ({ph}, {ph}, {ph}, {ph})
        """, (pipeline_id, dados.nome, ordem, dados.cor or "#3b82f6"))

        conexao.commit()
        return {"sucesso": True, "mensagem": f"Etapa '{dados.nome}' adicionada ao pipeline."}
    finally:
        conexao.close()


@router.patch("/pipelines/{pipeline_id}/etapas/{etapa_id}")
def atualizar_etapa_pipeline(pipeline_id: int, etapa_id: int, dados: EtapaUpdate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Pipeline não encontrado.")

        campos = []
        valores = []
        if dados.nome is not None:
            campos.append(f"nome = {ph}")
            valores.append(dados.nome)
        if dados.ordem is not None:
            campos.append(f"ordem = {ph}")
            valores.append(dados.ordem)
        if dados.cor is not None:
            campos.append(f"cor = {ph}")
            valores.append(dados.cor)

        if not campos:
            return {"mensagem": "Nenhum campo para atualizar."}

        valores.extend([etapa_id, pipeline_id])
        cursor.execute(f"UPDATE etapas_pipeline SET {', '.join(campos)} WHERE id = {ph} AND pipeline_id = {ph}", tuple(valores))
        conexao.commit()
        return {"sucesso": True, "mensagem": "Etapa atualizada com sucesso."}
    finally:
        conexao.close()


@router.delete("/pipelines/{pipeline_id}/etapas/{etapa_id}")
def excluir_etapa_pipeline(pipeline_id: int, etapa_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Pipeline não encontrado.")

        cursor.execute(f"SELECT COUNT(*) AS total FROM negocios WHERE etapa_id = {ph}", (etapa_id,))
        total = cursor.fetchone()["total"]
        if total > 0:
            raise HTTPException(status_code=400, detail=f"Esta etapa possui {total} negócio(s) ativo(s). Mova-os para outra etapa antes de excluir.")

        cursor.execute(f"DELETE FROM etapas_pipeline WHERE id = {ph} AND pipeline_id = {ph}", (etapa_id, pipeline_id))
        conexao.commit()
        return {"sucesso": True, "mensagem": "Etapa excluída com sucesso."}
    finally:
        conexao.close()


@router.put("/pipelines/{pipeline_id}/etapas/reordenar")
def reordenar_etapas_pipeline(pipeline_id: int, dados: EtapasReordenarRequest, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # Verifica pertencimento do pipeline à empresa
        cursor.execute(f"SELECT id FROM pipelines WHERE id = {ph} AND empresa_id = {ph}", (pipeline_id, target_empresa))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Pipeline não encontrado.")

        # Busca etapas existentes do pipeline
        cursor.execute(f"SELECT id FROM etapas_pipeline WHERE pipeline_id = {ph}", (pipeline_id,))
        etapas_existentes = [r["id"] for r in cursor.fetchall()]
        set_existentes = set(etapas_existentes)
        set_recebidos = set(dados.ordem_ids)

        # Validação estrita: sem faltar, sem repetir, sem id de outro pipeline
        if len(dados.ordem_ids) != len(etapas_existentes) or set_recebidos != set_existentes or len(set_recebidos) != len(dados.ordem_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A lista de etapas para reordenação deve conter exatamente todas as etapas deste pipeline, sem faltar, sem repetir e sem etapas externas."
            )

        for idx, et_id in enumerate(dados.ordem_ids, 1):
            cursor.execute(f"UPDATE etapas_pipeline SET ordem = {ph} WHERE id = {ph} AND pipeline_id = {ph}", (idx, et_id, pipeline_id))
        conexao.commit()
        return {"sucesso": True, "mensagem": "Ordem das etapas salva com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: NEGÓCIOS (Pipeline & Kanban com etapas dinâmicas)
# ============================================================================

@router.get("/negocios")
def listar_negocios(
    pipeline_id: Optional[int] = Query(None),
    empresa_id: Optional[int] = Query(None),
    etapa_id: Optional[int] = Query(None),
    estagio: Optional[str] = Query(None),
    cliente_id: Optional[int] = Query(None),
    request: Request = None
):
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        filtros = [f"n.empresa_id = {ph}"]
        params = [target_empresa]

        if pipeline_id:
            filtros.append(f"ep.pipeline_id = {ph}")
            params.append(pipeline_id)

        if etapa_id:
            filtros.append(f"n.etapa_id = {ph}")
            params.append(etapa_id)

        if estagio:
            filtros.append(f"(n.estagio = {ph} OR LOWER(ep.nome) = {ph})")
            params.extend([estagio.strip().lower(), estagio.strip().lower()])

        if cliente_id:
            filtros.append(f"n.cliente_id = {ph}")
            params.append(cliente_id)

        where = "WHERE " + " AND ".join(filtros)

        cursor.execute(f"""
            SELECT 
                n.id, n.empresa_id, n.cliente_id, n.produto_id, n.etapa_id,
                n.valor_estimado, n.estagio, n.proposta_enviada,
                n.ultima_interacao_em, n.ultimo_followup_em, n.criado_em,
                c.nome AS cliente_nome, c.telefone AS cliente_telefone, c.email AS cliente_email, c.telegram_chat_id,
                p.nome AS produto_nome,
                COALESCE(ep.nome, n.estagio, 'Novo') AS etapa_nome,
                COALESCE(ep.cor, '#3b82f6') AS etapa_cor,
                ep.pipeline_id,
                pl.nome AS pipeline_nome
            FROM negocios n
            JOIN clientes c ON n.cliente_id = c.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            LEFT JOIN pipelines pl ON ep.pipeline_id = pl.id
            {where}
            ORDER BY n.ultima_interacao_em DESC
        """, tuple(params))

        negocios = cursor.fetchall()
        return {"total": len(negocios), "negocios": [dict(n) for n in negocios]}
    finally:
        conexao.close()


@router.post("/negocios", status_code=status.HTTP_201_CREATED)
def criar_negocio(dados: NegocioCreate, request: Request = None):
    operador = exigir_perfil(request, dados.empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (dados.empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        etapa_id = dados.etapa_id
        estagio_nome = dados.estagio or "novo"

        if not etapa_id:
            # 1. Tenta pipeline do produto
            if dados.produto_id:
                cursor.execute(f"""
                    SELECT ep.id, ep.nome
                    FROM etapas_pipeline ep
                    JOIN pipelines p ON ep.pipeline_id = p.id
                    JOIN produtos prod ON (prod.pipeline_id = p.id OR p.produto_id = prod.id)
                    WHERE p.empresa_id = {ph} AND prod.id = {ph}
                    ORDER BY ep.ordem ASC, ep.id ASC
                    LIMIT 1
                """, (target_empresa, dados.produto_id))
                et_prod = cursor.fetchone()
                if et_prod:
                    etapa_id = et_prod["id"]
                    estagio_nome = et_prod["nome"]

            # 2. Tenta pipeline padrão da empresa
            if not etapa_id:
                cursor.execute(f"""
                    SELECT ep.id, ep.nome
                    FROM etapas_pipeline ep
                    JOIN pipelines p ON ep.pipeline_id = p.id
                    WHERE p.empresa_id = {ph} AND p.padrao = {ph}
                    ORDER BY ep.ordem ASC, ep.id ASC
                    LIMIT 1
                """, (target_empresa, True))
                et_padrao = cursor.fetchone()
                if et_padrao:
                    etapa_id = et_padrao["id"]
                    estagio_nome = et_padrao["nome"]

            # 3. Fallback: qualquer primeira etapa de qualquer pipeline da empresa
            if not etapa_id:
                cursor.execute(f"""
                    SELECT ep.id, ep.nome
                    FROM etapas_pipeline ep
                    JOIN pipelines p ON ep.pipeline_id = p.id
                    WHERE p.empresa_id = {ph}
                    ORDER BY ep.ordem ASC, ep.id ASC
                    LIMIT 1
                """, (target_empresa,))
                et_any = cursor.fetchone()
                if et_any:
                    etapa_id = et_any["id"]
                    estagio_nome = et_any["nome"]

        pipeline_id_resolvido = None
        if etapa_id:
            cursor.execute(f"SELECT pipeline_id FROM etapas_pipeline WHERE id = {ph}", (etapa_id,))
            row_p = cursor.fetchone()
            if row_p:
                pipeline_id_resolvido = row_p["pipeline_id"] if isinstance(row_p, dict) else row_p[0]

        cursor.execute(f"""
            INSERT INTO negocios (empresa_id, cliente_id, produto_id, etapa_id, valor_estimado, estagio)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
        """, (target_empresa, dados.cliente_id, dados.produto_id, etapa_id, dados.valor_estimado, estagio_nome.lower()))
        row = cursor.fetchone()
        novo_id = row["id"] if isinstance(row, dict) else row[0]

        conexao.commit()
        return {
            "id": novo_id,
            "etapa_id": etapa_id,
            "pipeline_id": pipeline_id_resolvido,
            "estagio": estagio_nome,
            "sucesso": True,
            "mensagem": "Negócio cadastrado no pipeline com sucesso!"
        }
    finally:
        conexao.close()


@router.patch("/negocios/{negocio_id}")
def atualizar_negocio(negocio_id: int, dados: NegocioUpdate, request: Request = None, empresa_id: Optional[int] = Query(None)):
    operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id, etapa_id, estagio, valor_estimado, empresa_id, produto_id FROM negocios WHERE id = {ph} AND empresa_id = {ph}", (negocio_id, target_empresa))
        negocio = cursor.fetchone()
        if not negocio:
            raise HTTPException(status_code=404, detail="Negócio não encontrado.")

        campos = ["ultima_interacao_em = CURRENT_TIMESTAMP"]
        valores = []

        novo_etapa_nome = None

        # Se o produto mudar e o pipeline for outro: move o negócio para a primeira etapa do novo pipeline
        if dados.produto_id is not None and dados.produto_id != negocio.get("produto_id"):
            novo_prod = dados.produto_id if dados.produto_id > 0 else None
            campos.append(f"produto_id = {ph}")
            valores.append(novo_prod)

            if dados.etapa_id is None:
                pipe_row = None
                if novo_prod:
                    cursor.execute(f"SELECT id FROM pipelines WHERE produto_id = {ph} AND empresa_id = {ph} LIMIT 1", (novo_prod, target_empresa))
                    pipe_row = cursor.fetchone()

                if not pipe_row:
                    cursor.execute(f"SELECT id FROM pipelines WHERE produto_id IS NULL AND empresa_id = {ph} LIMIT 1", (target_empresa,))
                    pipe_row = cursor.fetchone()

                if pipe_row:
                    cursor.execute(f"SELECT id, nome FROM etapas_pipeline WHERE pipeline_id = {ph} ORDER BY ordem ASC, id ASC LIMIT 1", (pipe_row["id"],))
                    primeira_et = cursor.fetchone()
                    if primeira_et:
                        campos.append(f"etapa_id = {ph}")
                        valores.append(primeira_et["id"])
                        campos.append(f"estagio = {ph}")
                        valores.append(primeira_et["nome"].lower())
                        novo_etapa_nome = primeira_et["nome"]

        if dados.etapa_id is not None:
            cursor.execute(f"SELECT id, nome FROM etapas_pipeline WHERE id = {ph}", (dados.etapa_id,))
            et_row = cursor.fetchone()
            if not et_row:
                raise HTTPException(status_code=400, detail="Etapa informada não existe.")
            campos.append(f"etapa_id = {ph}")
            valores.append(dados.etapa_id)
            campos.append(f"estagio = {ph}")
            valores.append(et_row["nome"].lower())
            novo_etapa_nome = et_row["nome"]

        elif dados.estagio is not None and novo_etapa_nome is None:
            estagio_fmt = dados.estagio.strip().lower()
            cursor.execute(f"""
                SELECT ep.id, ep.nome
                FROM etapas_pipeline ep
                JOIN pipelines p ON ep.pipeline_id = p.id
                WHERE p.empresa_id = {ph} AND LOWER(ep.nome) = {ph}
                ORDER BY ep.id ASC LIMIT 1
            """, (target_empresa, estagio_fmt))
            et_row = cursor.fetchone()
            if et_row:
                campos.append(f"etapa_id = {ph}")
                valores.append(et_row["id"])
                novo_etapa_nome = et_row["nome"]
            campos.append(f"estagio = {ph}")
            valores.append(estagio_fmt)
            if not novo_etapa_nome:
                novo_etapa_nome = estagio_fmt

        if dados.valor_estimado is not None:
            campos.append(f"valor_estimado = {ph}")
            valores.append(dados.valor_estimado)

        if dados.proposta_enviada is not None:
            campos.append(f"proposta_enviada = {ph}")
            valores.append(dados.proposta_enviada)

        valores.extend([negocio_id, target_empresa])
        cursor.execute(f"UPDATE negocios SET {', '.join(campos)} WHERE id = {ph} AND empresa_id = {ph}", tuple(valores))

        # Se a etapa virou 'fechado', garante criação automática de contrato pendente
        if novo_etapa_nome and novo_etapa_nome.strip().lower() == "fechado":
            val = dados.valor_estimado if dados.valor_estimado is not None else (negocio["valor_estimado"] or 0)
            cursor.execute(f"""
                INSERT INTO contratos (negocio_id, status, valor_contrato)
                VALUES ({ph}, 'pendente', {ph})
                ON CONFLICT (negocio_id) DO NOTHING
            """, (negocio_id, val))

        conexao.commit()
        return {"sucesso": True, "mensagem": f"Negócio {negocio_id} atualizado com sucesso."}
    finally:
        conexao.close()


@router.delete("/negocios/{negocio_id}")
def excluir_negocio(negocio_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    """Exclui negócio e limpa propostas, contratos e documentos sintéticos associados. Bloqueia se contrato assinado."""
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM negocios WHERE id = {ph} AND empresa_id = {ph}", (negocio_id, target_empresa))
        neg = cursor.fetchone()
        if not neg:
            raise HTTPException(status_code=404, detail="Negócio não encontrado.")

        # Bloqueia se houver contrato assinado
        cursor.execute(f"SELECT COUNT(*) as qtd FROM contratos WHERE negocio_id = {ph} AND status = 'assinado'", (negocio_id,))
        if cursor.fetchone()["qtd"] > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Não é possível excluir o negócio pois possui contratos assinados vinculados."
            )

        # Buscar IDs de contratos e propostas do negócio
        cursor.execute(f"SELECT id FROM contratos WHERE negocio_id = {ph}", (negocio_id,))
        contratos_ids = [r["id"] for r in cursor.fetchall()]

        cursor.execute(f"SELECT id FROM propostas WHERE negocio_id = {ph}", (negocio_id,))
        propostas_ids = [r["id"] for r in cursor.fetchall()]

        # Excluir documentos sintéticos vinculados e chunks
        if contratos_ids:
            cursor.execute(f"""
                DELETE FROM chunks WHERE documento_id IN (
                    SELECT id FROM documentos WHERE ref_tipo = 'contrato' AND ref_id = ANY({ph})
                )
            """, (contratos_ids,))
            cursor.execute(f"DELETE FROM documentos WHERE ref_tipo = 'contrato' AND ref_id = ANY({ph})", (contratos_ids,))
            cursor.execute(f"DELETE FROM contratos WHERE id = ANY({ph})", (contratos_ids,))

        if propostas_ids:
            cursor.execute(f"""
                DELETE FROM chunks WHERE documento_id IN (
                    SELECT id FROM documentos WHERE ref_tipo = 'proposta' AND ref_id = ANY({ph})
                )
            """, (propostas_ids,))
            cursor.execute(f"DELETE FROM documentos WHERE ref_tipo = 'proposta' AND ref_id = ANY({ph})", (propostas_ids,))
            cursor.execute(f"DELETE FROM propostas WHERE id = ANY({ph})", (propostas_ids,))

        cursor.execute(f"DELETE FROM negocios WHERE id = {ph} AND empresa_id = {ph}", (negocio_id, target_empresa))
        conexao.commit()
        return {"sucesso": True, "mensagem": f"Negócio {negocio_id} e seus dados vinculados foram excluídos com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: CONTRATOS & BASE DE CONHECIMENTO (PARTE 26)
# ============================================================================

@router.get("/contratos")
def listar_contratos(request: Request = None, empresa_id: Optional[int] = Query(None)):
    """Lista contratos da empresa com dados do negócio, cliente e produto."""
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException:
            pass
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT c.id, c.negocio_id, c.status, c.valor_contrato, c.data_assinatura, c.criado_em,
                   n.estagio, n.empresa_id, cli.nome AS cliente_nome, p.nome AS produto_nome
            FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            JOIN clientes cli ON n.cliente_id = cli.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            WHERE n.empresa_id = {ph}
            ORDER BY c.id DESC
        """, (target_empresa,))
        contratos = cursor.fetchall()
        return {"total": len(contratos), "contratos": [dict(c) for c in contratos]}
    finally:
        conexao.close()


@router.get("/contratos/{negocio_id}")
def obter_contrato(negocio_id: int):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT c.id, c.negocio_id, c.status, c.valor_contrato, c.data_assinatura, c.criado_em,
                   n.estagio, n.empresa_id, cli.nome AS cliente_nome, p.nome AS produto_nome
            FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            JOIN clientes cli ON n.cliente_id = cli.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            WHERE c.negocio_id = {ph}
        """, (negocio_id,))
        contrato = cursor.fetchone()
        if not contrato:
            raise HTTPException(status_code=404, detail="Nenhum contrato gerado para este negócio.")
        return dict(contrato)
    finally:
        conexao.close()


@router.patch("/contratos/{contrato_id}")
def atualizar_contrato(contrato_id: int, dados: ContratoUpdate):
    """
    Atualiza status do contrato.
    PARTE 26: Se status virar 'assinado', gera automaticamente um documento interno
    com os dados estruturados do contrato e vetoriza no RAG para consultas da IA.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        campos = [f"status = {ph}"]
        valores = [dados.status]

        if dados.status == "assinado":
            campos.append("data_assinatura = CURRENT_TIMESTAMP")
        elif dados.status in ("pendente", "cancelado"):
            campos.append("data_assinatura = NULL")

        if dados.valor_contrato is not None:
            campos.append(f"valor_contrato = {ph}")
            valores.append(dados.valor_contrato)

        valores.append(contrato_id)
        cursor.execute(f"UPDATE contratos SET {', '.join(campos)} WHERE id = {ph}", tuple(valores))
        conexao.commit()

        # PARTE 26: Se assinado, indexa automaticamente na Base de Conhecimento RAG
        if dados.status == "assinado":
            try:
                cursor.execute(f"""
                    SELECT c.id, c.negocio_id, c.valor_contrato, c.data_assinatura,
                           n.empresa_id, cli.nome AS cliente_nome, cli.email AS cliente_email,
                           cli.telefone AS cliente_telefone, p.nome AS produto_nome,
                           emp.nome AS empresa_nome
                    FROM contratos c
                    JOIN negocios n ON c.negocio_id = n.id
                    JOIN clientes cli ON n.cliente_id = cli.id
                    LEFT JOIN produtos p ON n.produto_id = p.id
                    LEFT JOIN empresas emp ON n.empresa_id = emp.id
                    WHERE c.id = {ph}
                """, (contrato_id,))
                detalhes = cursor.fetchone()
                if detalhes:
                    texto_contrato = f"""TERMO E REGISTRO DE CONTRATO CORPORATIVO ASSINADO
Identificador: Contrato #{detalhes['id']}
Status: Assinado e Ativo
Data de Homologação: {datetime.now().strftime('%d/%m/%Y %H:%M')}
Empresa Contratada: {detalhes['empresa_nome'] or 'FluxIA'} (Ambiente #{detalhes['empresa_id']})
Cliente Contratante: {detalhes['cliente_nome']}
E-mail: {detalhes['cliente_email'] or 'Não informado'}
Telefone: {detalhes['cliente_telefone'] or 'Não informado'}
Produto / Serviço Licenciado: {detalhes['produto_nome'] or 'Serviço Personalizado'}
Valor Total Fechado: R$ {float(detalhes['valor_contrato'] or 0):,.2f}
Referência CRM: Negócio #{detalhes['negocio_id']}
Condições: Contrato fechado com aceite formal do cliente."""

                    nome_doc = f"Contrato_{contrato_id}_{re.sub(r'[^a-zA-Z0-9]', '_', str(detalhes['cliente_nome']))}.txt"
                    salvar_e_indexar_documento_texto(
                        empresa_id=detalhes["empresa_id"],
                        nome_arquivo=nome_doc,
                        conteudo_texto=texto_contrato,
                        tipo_arquivo=".txt",
                        nivel_acesso="interno",
                        origem="sistema",
                        ref_tipo="contrato",
                        ref_id=contrato_id
                    )
            except Exception as e:
                logger.error(f"[Contratos] Erro ao indexar contrato {contrato_id} na base de conhecimento: {e}")

        return {"mensagem": f"Contrato {contrato_id} atualizado para status '{dados.status}'."}
    finally:
        conexao.close()


@router.post("/contratos/importar", status_code=status.HTTP_201_CREATED)
async def importar_contrato(
    arquivo: UploadFile = File(...),
    cliente_id: Optional[int] = Form(None),
    negocio_id: Optional[int] = Form(None),
    valor: Optional[float] = Form(0.0),
    empresa_id: Optional[int] = Form(None),
    request: Request = None
):
    """
    Importação de arquivo de contrato.
    Aberta para qualquer perfil (funcionario, admin, master).
    Suporta PDF, DOCX, TXT e imagens.
    Gera o contrato, marca como assinado e indexa automaticamente no RAG.
    """
    operador = exigir_perfil(request, empresa_id, ["funcionario", "admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    extensao = Path(arquivo.filename).suffix.lower()
    extensoes_permitidas = {".pdf", ".txt", ".docx", ".png", ".jpg", ".jpeg"}
    if extensao not in extensoes_permitidas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato '{extensao}' não suportado. Use PDF, DOCX, TXT ou Imagem."
        )

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        n_id = negocio_id
        cli_id = cliente_id

        if not n_id:
            if not cli_id:
                cursor.execute(f"SELECT id FROM clientes WHERE empresa_id = {ph} ORDER BY id ASC LIMIT 1", (target_empresa,))
                row_cli = cursor.fetchone()
                if row_cli:
                    cli_id = row_cli["id"]
                else:
                    cursor.execute(f"INSERT INTO clientes (empresa_id, nome, origem) VALUES ({ph}, 'Cliente Contrato Importado', 'contrato') RETURNING id", (target_empresa,))
                    cli_id = cursor.fetchone()["id"]

            cursor.execute(f"""
                INSERT INTO negocios (empresa_id, cliente_id, estagio, valor_estimado)
                VALUES ({ph}, {ph}, 'fechado', {ph})
                RETURNING id
            """, (target_empresa, cli_id, float(valor or 0)))
            n_id = cursor.fetchone()["id"]

        cursor.execute(f"""
            INSERT INTO contratos (negocio_id, status, valor_contrato, data_assinatura)
            VALUES ({ph}, 'assinado', {ph}, CURRENT_TIMESTAMP)
            RETURNING id
        """, (n_id, float(valor or 0)))
        contrato_id = cursor.fetchone()["id"]
        conexao.commit()

        # Salva o arquivo em uploads e indexa no RAG
        nome_salvo = f"contrato_{contrato_id}_{uuid4().hex[:8]}{extensao}"
        caminho_arquivo = Path("uploads") / nome_salvo
        caminho_arquivo.parent.mkdir(exist_ok=True)
        conteudo_bytes = await arquivo.read()
        with open(caminho_arquivo, "wb") as f:
            f.write(conteudo_bytes)

        try:
            conteudo_texto = ler_documento(str(caminho_arquivo))
        except Exception as e_ler:
            logger.warning(f"Erro ao extrair texto do arquivo importado: {e_ler}")
            conteudo_texto = f"Contrato Comercial #{contrato_id} importado: {arquivo.filename}"

        if not conteudo_texto or not conteudo_texto.strip():
            conteudo_texto = f"Contrato Comercial #{contrato_id} importado: {arquivo.filename}"

        hash_conteudo = hashlib.md5(conteudo_texto.encode("utf-8")).hexdigest()

        cursor.execute(f"""
            INSERT INTO documentos (
                empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo,
                conteudo_texto, hash_conteudo, nivel_acesso, data_upload,
                origem, ref_tipo, ref_id
            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, 'interno', CURRENT_TIMESTAMP, 'upload', 'contrato', {ph})
            RETURNING id
        """, (target_empresa, arquivo.filename, extensao, str(caminho_arquivo), conteudo_texto, hash_conteudo, contrato_id))
        doc_id = cursor.fetchone()["id"]
        conexao.commit()

        # Quebra em chunks e gera embeddings
        try:
            chunks = dividir_texto(conteudo_texto)
            for num, chunk in enumerate(chunks, 1):
                emb = gerar_embedding(chunk)
                cursor.execute(f"""
                    INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding)
                    VALUES ({ph}, {ph}, {ph}, {ph})
                """, (doc_id, num, chunk, json.dumps(emb) if emb else None))
            conexao.commit()
        except Exception as e:
            logger.error(f"[Contratos] Erro ao vetorizar chunks do contrato {contrato_id}: {e}")

        return {
            "sucesso": True,
            "contrato_id": contrato_id,
            "documento_id": doc_id,
            "mensagem": f"Contrato #{contrato_id} ('{arquivo.filename}') importado e indexado com sucesso!"
        }
    finally:
        conexao.close()


@router.delete("/contratos/{contrato_id}")
def excluir_contrato(contrato_id: int, request: Request = None, empresa_id: Optional[int] = Query(None)):
    """Exclui um contrato e seus documentos/chunks vinculados. Apenas para admins e master."""
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT c.id FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            WHERE c.id = {ph} AND n.empresa_id = {ph}
        """, (contrato_id, target_empresa))
        c = cursor.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Contrato não encontrado.")

        # Remove chunks e documento associado
        cursor.execute(f"""
            DELETE FROM chunks WHERE documento_id IN (
                SELECT id FROM documentos WHERE ref_tipo = 'contrato' AND ref_id = {ph}
            )
        """, (contrato_id,))
        cursor.execute(f"DELETE FROM documentos WHERE ref_tipo = 'contrato' AND ref_id = {ph}", (contrato_id,))
        cursor.execute(f"DELETE FROM contratos WHERE id = {ph}", (contrato_id,))
        conexao.commit()
        return {"sucesso": True, "mensagem": f"Contrato #{contrato_id} excluído com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: PROPOSTAS COMERCIAIS (PARTE 27)
# ============================================================================

@router.post("/negocios/{negocio_id}/previa-proposta")
def previsualizar_proposta(negocio_id: int, dados: GerarPropostaRequest):
    """Gera o HTML de prévia da proposta comercial para conferência antes do envio."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT n.id, n.empresa_id, n.valor_estimado,
                   cli.nome AS cliente_nome, cli.email AS cliente_email, cli.telefone AS cliente_telefone,
                   p.nome AS produto_nome, p.descricao AS produto_descricao,
                   emp.nome AS empresa_nome
            FROM negocios n
            JOIN clientes cli ON n.cliente_id = cli.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            LEFT JOIN empresas emp ON n.empresa_id = emp.id
            WHERE n.id = {ph}
        """, (negocio_id,))
        neg = cursor.fetchone()
        if not neg:
            raise HTTPException(status_code=404, detail="Negócio não encontrado.")

        html = gerar_template_proposta_html(
            cliente_nome=neg["cliente_nome"],
            cliente_email=neg["cliente_email"] or "",
            cliente_telefone=neg["cliente_telefone"] or "",
            produto_nome=neg["produto_nome"] or "Solução Sob Demanda",
            produto_descricao=neg["produto_descricao"] or "",
            valor=float(neg["valor_estimado"] or 0),
            condicoes=dados.condicoes_pagamento,
            validade_dias=dados.validade_dias,
            empresa_nome=neg["empresa_nome"] or "FluxIA",
            proposta_id=negocio_id
        )

        return {
            "html": html,
            "cliente_nome": neg["cliente_nome"],
            "cliente_email": neg["cliente_email"],
            "tem_email": bool(neg.get("cliente_email") and neg["cliente_email"].strip())
        }
    finally:
        conexao.close()


@router.post("/negocios/{negocio_id}/gerar-proposta")
def gerar_e_enviar_proposta(negocio_id: int, dados: GerarPropostaRequest):
    """
    PARTE 27:
    1. Bloqueia envio se clientes.email estiver vazio.
    2. Gera documento HTML da proposta a partir do template.
    3. Indexa automaticamente na base de conhecimento como documento 'sistema' / 'interno' (PARTE 26).
    4. Marca negocios.proposta_enviada = TRUE.
    5. Dispara envio por e-mail via smtplib se configurado.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT n.id, n.empresa_id, n.cliente_id, n.produto_id, n.valor_estimado,
                   cli.nome AS cliente_nome, cli.email AS cliente_email, cli.telefone AS cliente_telefone,
                   p.nome AS produto_nome, p.descricao AS produto_descricao,
                   emp.nome AS empresa_nome
            FROM negocios n
            JOIN clientes cli ON n.cliente_id = cli.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            LEFT JOIN empresas emp ON n.empresa_id = emp.id
            WHERE n.id = {ph}
        """, (negocio_id,))
        neg = cursor.fetchone()
        if not neg:
            raise HTTPException(status_code=404, detail="Negócio não encontrado.")

        email_cliente = (neg.get("cliente_email") or "").strip()
        if not email_cliente and dados.enviar_email:
            raise HTTPException(
                status_code=400,
                detail="Cliente sem e-mail cadastrado — complete o cadastro antes de enviar a proposta."
            )

        # 1. Cria registro de proposta
        cursor.execute(f"""
            INSERT INTO propostas (negocio_id, empresa_id, cliente_id, produto_id, valor, condicoes_pagamento, validade_dias, email_destinatario)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
        """, (negocio_id, neg["empresa_id"], neg["cliente_id"], neg["produto_id"], float(neg["valor_estimado"] or 0), dados.condicoes_pagamento, dados.validade_dias, email_cliente))

        proposta_id = cursor.fetchone()["id"]
        conexao.commit()

        # 2. Gera HTML da Proposta
        html_proposta = gerar_template_proposta_html(
            cliente_nome=neg["cliente_nome"],
            cliente_email=email_cliente,
            cliente_telefone=neg.get("cliente_telefone") or "",
            produto_nome=neg.get("produto_nome") or "Solução Especializada",
            produto_descricao=neg.get("produto_descricao") or "",
            valor=float(neg["valor_estimado"] or 0),
            condicoes=dados.condicoes_pagamento,
            validade_dias=dados.validade_dias,
            empresa_nome=neg.get("empresa_nome") or "FluxIA",
            proposta_id=proposta_id
        )

        # 3. PARTE 26: Salva e indexa documento sintético no RAG interno
        doc_id = None
        try:
            texto_estruturado_rag = f"""PROPOSTA COMERCIAL #{proposta_id}
Cliente: {neg['cliente_nome']}
Email: {email_cliente}
Produto: {neg.get('produto_nome') or 'Solução'}
Valor Proposto: R$ {float(neg['valor_estimado'] or 0):,.2f}
Condições de Pagamento: {dados.condicoes_pagamento}
Validade: {dados.validade_dias} dias
Empresa: {neg.get('empresa_nome') or 'FluxIA'}
Status: Emitida para o cliente"""

            nome_arquivo_doc = f"Proposta_{proposta_id}_{re.sub(r'[^a-zA-Z0-9]', '_', str(neg['cliente_nome']))}.html"
            doc_id = salvar_e_indexar_documento_texto(
                empresa_id=neg["empresa_id"],
                nome_arquivo=nome_arquivo_doc,
                conteudo_texto=texto_estruturado_rag,
                tipo_arquivo=".html",
                nivel_acesso="interno",
                origem="sistema",
                ref_tipo="proposta",
                ref_id=proposta_id
            )
        except Exception as e:
            logger.error(f"[Propostas] Falha ao indexar proposta no RAG: {e}")

        # 4. Atualiza proposta com documento_id
        cursor.execute(f"UPDATE propostas SET documento_id = {ph} WHERE id = {ph}", (doc_id, proposta_id))

        # 5. Marca negocio como proposta enviada
        cursor.execute(f"UPDATE negocios SET proposta_enviada = {ph}, ultima_interacao_em = CURRENT_TIMESTAMP WHERE id = {ph}", (True, negocio_id))
        conexao.commit()

        # 6. Disparo de e-mail via smtplib
        email_sucesso = False
        msg_email = "Proposta gerada e registrada no sistema."
        if dados.enviar_email and email_cliente:
            assunto = f"Proposta Comercial #{proposta_id} - {neg.get('empresa_nome') or 'FluxIA'}"
            email_sucesso, msg_email = disparar_email_proposta(
                destinatario=email_cliente,
                assunto=assunto,
                html_corpo=html_proposta,
                empresa_nome=neg.get("empresa_nome") or "FluxIA"
            )
            novo_status_envio = "enviada" if email_sucesso else "pendente_envio"
            cursor.execute(f"UPDATE propostas SET status_envio = {ph} WHERE id = {ph}", (novo_status_envio, proposta_id))
            conexao.commit()

        return {
            "sucesso": True,
            "proposta_id": proposta_id,
            "documento_id": doc_id,
            "email_enviado": email_sucesso,
            "mensagem": f"Proposta #{proposta_id} gerada com sucesso! {msg_email}"
        }
    finally:
        conexao.close()


@router.get("/negocios/{negocio_id}/propostas")
def listar_propostas_negocio(negocio_id: int):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT p.id, p.negocio_id, p.empresa_id, p.cliente_id, p.produto_id, p.valor,
                   p.condicoes_pagamento, p.validade_dias, p.status_envio, p.email_destinatario,
                   p.documento_id, p.criado_em,
                   cli.nome AS cliente_nome, prod.nome AS produto_nome
            FROM propostas p
            JOIN clientes cli ON p.cliente_id = cli.id
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            WHERE p.negocio_id = {ph}
            ORDER BY p.id DESC
        """, (negocio_id,))
        propostas = cursor.fetchall()
        return {"total": len(propostas), "propostas": [dict(p) for p in propostas]}
    finally:
        conexao.close()


@router.get("/propostas")
def listar_todas_propostas(request: Request = None, empresa_id: Optional[int] = Query(None)):
    """Lista todas as propostas da empresa para a tela de Propostas no CRM."""
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT p.id, p.negocio_id, p.empresa_id, p.cliente_id, p.produto_id, p.valor,
                   p.condicoes_pagamento, p.validade_dias, p.status_envio, p.email_destinatario,
                   p.documento_id, p.criado_em, p.descricao_itens,
                   cli.nome AS cliente_nome, cli.telefone AS cliente_telefone,
                   prod.nome AS produto_nome,
                   n.estagio AS negocio_estagio
            FROM propostas p
            JOIN clientes cli ON p.cliente_id = cli.id
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            LEFT JOIN negocios n ON p.negocio_id = n.id
            WHERE p.empresa_id = {ph}
            ORDER BY p.id DESC
        """, (target_empresa,))
        propostas = cursor.fetchall()
        return {"total": len(propostas), "propostas": [dict(p) for p in propostas]}
    finally:
        conexao.close()


@router.post("/propostas", status_code=status.HTTP_201_CREATED)
def criar_proposta_direta(dados: PropostaDirectCreate, request: Request = None):
    operador = exigir_perfil(request, dados.empresa_id, ["admin", "funcionario", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (dados.empresa_id or 1)
    """
    Cria uma proposta comercial completa diretamente com campos preenchíveis:
    1. Localiza ou cadastra o cliente automaticamente.
    2. Localiza ou cadastra o produto se necessário.
    3. Cria ou associa a oportunidade (negócio) no CRM em estágio 'Proposta'.
    4. Gera o documento HTML institucional estilizado.
    5. Salva e indexa o documento na pasta uploads/ e na tabela 'documentos' (RAG).
    6. Registra a proposta na tabela 'propostas' associada ao 'documento_id'.
    7. Dispara e-mail se solicitado e SMTP configurado.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # 1. Obter nome da empresa
        cursor.execute(f"SELECT nome FROM empresas WHERE id = {ph}", (target_empresa,))
        emp_row = cursor.fetchone()
        empresa_nome = emp_row["nome"] if emp_row else "FluxIA"

        # 2. Localizar ou criar cliente
        cliente_id = dados.cliente_id
        cliente_nome = dados.cliente_nome.strip()
        cliente_email = (dados.cliente_email or "").strip()
        cliente_telefone = (dados.cliente_telefone or "").strip()

        if cliente_id:
            cursor.execute(f"SELECT id, nome, email, telefone FROM clientes WHERE id = {ph} AND empresa_id = {ph}", (cliente_id, target_empresa))
            cli_row = cursor.fetchone()
            if cli_row:
                if not cliente_nome:
                    cliente_nome = cli_row["nome"]
                up_campos = []
                up_vals = []
                if cliente_email and not cli_row.get("email"):
                    up_campos.append(f"email = {ph}")
                    up_vals.append(cliente_email)
                if cliente_telefone and not cli_row.get("telefone"):
                    up_campos.append(f"telefone = {ph}")
                    up_vals.append(cliente_telefone)
                if up_campos:
                    up_vals.append(cliente_id)
                    cursor.execute(f"UPDATE clientes SET {', '.join(up_campos)} WHERE id = {ph}", tuple(up_vals))
            else:
                cliente_id = None

        if not cliente_id:
            if cliente_email:
                cursor.execute(f"SELECT id FROM clientes WHERE empresa_id = {ph} AND email = {ph} LIMIT 1", (target_empresa, cliente_email))
                cli_existente = cursor.fetchone()
                if cli_existente:
                    cliente_id = cli_existente["id"]
            if not cliente_id:
                cursor.execute(f"""
                    INSERT INTO clientes (empresa_id, nome, email, telefone, origem)
                    VALUES ({ph}, {ph}, {ph}, {ph}, 'proposta')
                    RETURNING id
                """, (target_empresa, cliente_nome, cliente_email or None, cliente_telefone or None))
                cliente_id = cursor.fetchone()["id"]

        # 3. Localizar ou criar produto
        produto_id = dados.produto_id
        produto_nome = (dados.produto_nome or "Solução Especializada").strip()
        produto_desc = (dados.descricao_itens or "").strip()

        if produto_id:
            cursor.execute(f"SELECT id, nome, descricao FROM produtos WHERE id = {ph} AND empresa_id = {ph}", (produto_id, target_empresa))
            prod_row = cursor.fetchone()
            if prod_row:
                produto_nome = prod_row["nome"]
                if not produto_desc:
                    produto_desc = prod_row.get("descricao") or ""
            else:
                produto_id = None

        if not produto_id and produto_nome:
            cursor.execute(f"SELECT id, descricao FROM produtos WHERE empresa_id = {ph} AND nome = {ph} LIMIT 1", (target_empresa, produto_nome))
            p_exist = cursor.fetchone()
            if p_exist:
                produto_id = p_exist["id"]
                if not produto_desc:
                    produto_desc = p_exist.get("descricao") or ""
            else:
                cursor.execute(f"""
                    INSERT INTO produtos (empresa_id, nome, descricao, preco, ativo)
                    VALUES ({ph}, {ph}, {ph}, {ph}, TRUE)
                    RETURNING id
                """, (target_empresa, produto_nome, produto_desc or None, float(dados.valor)))
                produto_id = cursor.fetchone()["id"]

        # 4. Localizar ou criar negócio no CRM
        cursor.execute(f"""
            SELECT id, etapa_id FROM negocios
            WHERE cliente_id = {ph} AND empresa_id = {ph} AND produto_id = {ph} AND estagio NOT IN ('fechado', 'perdido')
            ORDER BY id DESC LIMIT 1
        """, (cliente_id, target_empresa, produto_id))
        neg_row = cursor.fetchone()
        if not neg_row:
            cursor.execute(f"""
                SELECT id, etapa_id FROM negocios
                WHERE cliente_id = {ph} AND empresa_id = {ph} AND produto_id IS NULL AND estagio NOT IN ('fechado', 'perdido')
                ORDER BY id DESC LIMIT 1
            """, (cliente_id, target_empresa))
            neg_row = cursor.fetchone()

        # Determina a melhor etapa no pipeline associado a este produto
        etapa_id = None
        if produto_id:
            cursor.execute(f"""
                SELECT ep.id 
                FROM etapas_pipeline ep
                JOIN pipelines p ON ep.pipeline_id = p.id
                JOIN produtos prod ON (prod.pipeline_id = p.id OR p.produto_id = prod.id)
                WHERE p.empresa_id = {ph} AND prod.id = {ph} AND LOWER(ep.nome) LIKE {ph}
                ORDER BY ep.ordem ASC LIMIT 1
            """, (target_empresa, produto_id, '%proposta%'))
            et_p = cursor.fetchone()
            if not et_p:
                cursor.execute(f"""
                    SELECT ep.id 
                    FROM etapas_pipeline ep
                    JOIN pipelines p ON ep.pipeline_id = p.id
                    JOIN produtos prod ON (prod.pipeline_id = p.id OR p.produto_id = prod.id)
                    WHERE p.empresa_id = {ph} AND prod.id = {ph}
                    ORDER BY ep.ordem ASC LIMIT 1
                """, (target_empresa, produto_id))
                et_p = cursor.fetchone()
            if et_p:
                etapa_id = et_p["id"]

        if not etapa_id:
            cursor.execute(f"""
                SELECT ep.id 
                FROM etapas_pipeline ep
                JOIN pipelines p ON ep.pipeline_id = p.id
                WHERE p.empresa_id = {ph} AND LOWER(ep.nome) LIKE {ph}
                ORDER BY p.padrao DESC, ep.ordem ASC
                LIMIT 1
            """, (target_empresa, '%proposta%'))
            et_def = cursor.fetchone()
            etapa_id = et_def["id"] if et_def else None

        if neg_row:
            negocio_id = neg_row["id"]
            cursor.execute(f"""
                UPDATE negocios 
                SET valor_estimado = {ph},
                    produto_id = {ph},
                    etapa_id = COALESCE({ph}, etapa_id),
                    estagio = 'Proposta',
                    proposta_enviada = TRUE,
                    ultima_interacao_em = CURRENT_TIMESTAMP
                WHERE id = {ph} AND empresa_id = {ph}
            """, (float(dados.valor), produto_id, etapa_id, negocio_id, target_empresa))
        else:
            cursor.execute(f"""
                INSERT INTO negocios (empresa_id, cliente_id, produto_id, etapa_id, valor_estimado, estagio, proposta_enviada)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, 'Proposta', TRUE)
                RETURNING id
            """, (target_empresa, cliente_id, produto_id, etapa_id, float(dados.valor)))
            negocio_id = cursor.fetchone()["id"]

        # 5. Inserir registro na tabela 'propostas'
        cursor.execute(f"""
            INSERT INTO propostas (negocio_id, empresa_id, cliente_id, produto_id, valor, condicoes_pagamento, validade_dias, email_destinatario, status_envio, descricao_itens)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, 'pendente', {ph})
            RETURNING id
        """, (negocio_id, target_empresa, cliente_id, produto_id, float(dados.valor), dados.condicoes_pagamento, dados.validade_dias or 15, cliente_email or None, dados.descricao_itens or None))
        proposta_id = cursor.fetchone()["id"]

        conexao.commit()

        # 6. Gerar documento HTML da proposta
        html_proposta = gerar_template_proposta_html(
            cliente_nome=cliente_nome,
            cliente_email=cliente_email,
            cliente_telefone=cliente_telefone,
            produto_nome=produto_nome,
            produto_descricao=produto_desc,
            valor=float(dados.valor),
            condicoes=dados.condicoes_pagamento or "À vista via PIX ou em 12x",
            validade_dias=dados.validade_dias or 15,
            empresa_nome=empresa_nome,
            proposta_id=proposta_id
        )

        # 7. Salvar e indexar como documento interno no RAG / Base de Conhecimento
        doc_id = None
        try:
            texto_rag = f"""PROPOSTA COMERCIAL #{proposta_id}
Cliente: {cliente_nome}
Email: {cliente_email or 'Não informado'}
Telefone: {cliente_telefone or 'Não informado'}
Produto / Solução: {produto_nome}
Descrição / Escopo: {produto_desc or 'Conforme detalhamento comercial'}
Valor Proposto: R$ {float(dados.valor):,.2f}
Condições de Pagamento: {dados.condicoes_pagamento}
Validade: {dados.validade_dias} dias
Empresa Emissora: {empresa_nome}
Status: Proposta Oficial Gerada"""

            nome_arquivo_doc = f"Proposta_{proposta_id}_{re.sub(r'[^a-zA-Z0-9]', '_', cliente_nome)}.html"
            doc_id = salvar_e_indexar_documento_texto(
                empresa_id=target_empresa,
                nome_arquivo=nome_arquivo_doc,
                conteudo_texto=texto_rag,
                tipo_arquivo=".html",
                nivel_acesso="interno",
                origem="sistema",
                ref_tipo="proposta",
                ref_id=proposta_id
            )
            cursor.execute(f"UPDATE propostas SET documento_id = {ph} WHERE id = {ph}", (doc_id, proposta_id))
            conexao.commit()
        except Exception as e:
            logger.error(f"[Propostas] Falha ao indexar proposta no RAG: {e}")

        # 8. Envio opcional por e-mail se solicitado
        email_sucesso = False
        msg_extra = "Proposta gerada com sucesso e integrada à base de conhecimento!"
        if dados.enviar_email and cliente_email:
            assunto = f"Proposta Comercial #{proposta_id} - {empresa_nome}"
            email_sucesso, msg_mail = disparar_email_proposta(
                destinatario=cliente_email,
                assunto=assunto,
                html_corpo=html_proposta,
                empresa_nome=empresa_nome
            )
            novo_status = "enviado" if email_sucesso else "falha_envio"
            cursor.execute(f"UPDATE propostas SET status_envio = {ph} WHERE id = {ph}", (novo_status, proposta_id))
            conexao.commit()
            msg_extra += f" {msg_mail}"

        return {
            "sucesso": True,
            "proposta_id": proposta_id,
            "documento_id": doc_id,
            "cliente_id": cliente_id,
            "negocio_id": negocio_id,
            "email_enviado": email_sucesso,
            "mensagem": msg_extra
        }
    finally:
        conexao.close()


@router.post("/propostas/previa")
def previa_proposta_direta(dados: PropostaDirectCreate, request: Request = None):
    """Gera o HTML de prévia da proposta comercial a partir dos dados preenchidos no formulário."""
    operador = None
    if request:
        try:
            operador = exigir_perfil(request, dados.empresa_id, ["admin", "funcionario", "master"])
        except HTTPException as he:
            if he.status_code == 403:
                raise
    target_empresa = operador["empresa_id"] if (operador and operador["perfil"] != "master") else (dados.empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    empresa_nome = "FluxIA"
    try:
        cursor.execute(f"SELECT nome FROM empresas WHERE id = {ph}", (target_empresa,))
        row = cursor.fetchone()
        if row:
            empresa_nome = row["nome"]
    finally:
        conexao.close()

    html = gerar_template_proposta_html(
        cliente_nome=dados.cliente_nome or "Cliente Exemplo",
        cliente_email=dados.cliente_email or "",
        cliente_telefone=dados.cliente_telefone or "",
        produto_nome=dados.produto_nome or "Solução Especializada",
        produto_descricao=dados.descricao_itens or "",
        valor=float(dados.valor or 0),
        condicoes=dados.condicoes_pagamento or "À vista via PIX ou em 12x",
        validade_dias=dados.validade_dias or 15,
        empresa_nome=empresa_nome,
        proposta_id=0
    )
    return {"html": html}


@router.get("/propostas/{proposta_id}/html")
def obter_html_proposta(
    proposta_id: int,
    request: Request = None,
    format: Optional[str] = Query(None)
):
    """
    Retorna o documento HTML completo de uma proposta comercial existente para visualização no portal.
    Se requisitado diretamente pelo navegador ou format='html', responde com HTMLResponse.
    Se format='json', responde com JSON contendo id, html e documento_id.
    """
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT p.id, p.valor, p.condicoes_pagamento, p.validade_dias, p.documento_id,
                   cli.nome AS cliente_nome, cli.email AS cliente_email, cli.telefone AS cliente_telefone,
                   prod.nome AS produto_nome, prod.descricao AS produto_descricao,
                   emp.nome AS empresa_nome
            FROM propostas p
            JOIN clientes cli ON p.cliente_id = cli.id
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            LEFT JOIN empresas emp ON p.empresa_id = emp.id
            WHERE p.id = {ph}
        """, (proposta_id,))
        p = cursor.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Proposta não encontrada.")

        html = gerar_template_proposta_html(
            cliente_nome=p["cliente_nome"],
            cliente_email=p.get("cliente_email") or "",
            cliente_telefone=p.get("cliente_telefone") or "",
            produto_nome=p.get("produto_nome") or "Solução Especializada",
            produto_descricao=p.get("produto_descricao") or "",
            valor=float(p["valor"] or 0),
            condicoes=p.get("condicoes_pagamento") or "À vista",
            validade_dias=p.get("validade_dias") or 15,
            empresa_nome=p.get("empresa_nome") or "FluxIA",
            proposta_id=p["id"]
        )

        accept = request.headers.get("accept", "") if request else ""
        if format == "html" or ("text/html" in accept and format != "json"):
            return HTMLResponse(content=html, media_type="text/html")

        return {"id": proposta_id, "html": html, "documento_id": p.get("documento_id")}
    finally:
        conexao.close()


@router.put("/propostas/{proposta_id}")
def atualizar_proposta(
    proposta_id: int,
    dados: PropostaUpdate,
    request: Request = None,
    empresa_id: Optional[int] = Query(None)
):
    """Edita uma proposta comercial existente. Apenas para administradores e master."""
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT p.*, cli.nome AS cliente_nome, cli.email AS cliente_email, cli.telefone AS cliente_telefone,
                   prod.nome AS prod_nome, prod.descricao AS prod_descricao,
                   emp.nome AS empresa_nome
            FROM propostas p
            JOIN clientes cli ON p.cliente_id = cli.id
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            LEFT JOIN empresas emp ON p.empresa_id = emp.id
            WHERE p.id = {ph} AND p.empresa_id = {ph}
        """, (proposta_id, target_empresa))
        prop = cursor.fetchone()
        if not prop:
            raise HTTPException(status_code=404, detail="Proposta não encontrada.")

        campos = []
        valores = []
        if dados.valor is not None:
            campos.append(f"valor = {ph}")
            valores.append(float(dados.valor))
        if dados.validade_dias is not None:
            campos.append(f"validade_dias = {ph}")
            valores.append(int(dados.validade_dias))
        if dados.condicoes_pagamento is not None:
            campos.append(f"condicoes_pagamento = {ph}")
            valores.append(dados.condicoes_pagamento.strip())
        if dados.descricao_itens is not None:
            campos.append(f"descricao_itens = {ph}")
            valores.append(dados.descricao_itens.strip())
        if dados.produto_id is not None:
            campos.append(f"produto_id = {ph}")
            valores.append(dados.produto_id)

        if campos:
            valores.extend([proposta_id, target_empresa])
            cursor.execute(f"UPDATE propostas SET {', '.join(campos)} WHERE id = {ph} AND empresa_id = {ph}", tuple(valores))
            conexao.commit()

        # Atualiza negócio vinculado se houver alteração de valor
        if dados.valor is not None and prop.get("negocio_id"):
            cursor.execute(f"UPDATE negocios SET valor_estimado = {ph} WHERE id = {ph} AND empresa_id = {ph}", (float(dados.valor), prop["negocio_id"], target_empresa))
            conexao.commit()

        # Regenera HTML e atualiza documento RAG se existir
        novo_valor = float(dados.valor) if dados.valor is not None else float(prop["valor"] or 0)
        novo_prod_nome = dados.produto_nome or prop.get("prod_nome") or "Solução Especializada"
        novo_cond = dados.condicoes_pagamento or prop.get("condicoes_pagamento") or "À vista"
        novo_val_dias = dados.validade_dias if dados.validade_dias is not None else (prop.get("validade_dias") or 15)

        html_atualizado = gerar_template_proposta_html(
            cliente_nome=prop["cliente_nome"],
            cliente_email=prop.get("cliente_email") or "",
            cliente_telefone=prop.get("cliente_telefone") or "",
            produto_nome=novo_prod_nome,
            produto_descricao=dados.descricao_itens or prop.get("prod_descricao") or "",
            valor=novo_valor,
            condicoes=novo_cond,
            validade_dias=novo_val_dias,
            empresa_nome=prop.get("empresa_nome") or "FluxIA",
            proposta_id=proposta_id
        )

        cursor.execute(f"""
            UPDATE documentos 
            SET conteudo_texto = {ph}
            WHERE ref_tipo = 'proposta' AND ref_id = {ph} AND empresa_id = {ph}
        """, (html_atualizado, proposta_id, target_empresa))
        conexao.commit()

        return {"sucesso": True, "proposta_id": proposta_id, "mensagem": "Proposta comercial atualizada com sucesso!"}
    finally:
        conexao.close()


@router.delete("/propostas/{proposta_id}")
def excluir_proposta(
    proposta_id: int,
    request: Request = None,
    empresa_id: Optional[int] = Query(None)
):
    """Exclui uma proposta comercial e seus documentos/chunks vinculados. Apenas para administradores e master."""
    operador = exigir_perfil(request, empresa_id, ["admin", "master"])
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT id FROM propostas WHERE id = {ph} AND empresa_id = {ph}", (proposta_id, target_empresa))
        p = cursor.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Proposta não encontrada.")

        # Remove chunks e documento associado
        cursor.execute(f"""
            DELETE FROM chunks WHERE documento_id IN (
                SELECT id FROM documentos WHERE ref_tipo = 'proposta' AND ref_id = {ph}
            )
        """, (proposta_id,))
        cursor.execute(f"DELETE FROM documentos WHERE ref_tipo = 'proposta' AND ref_id = {ph}", (proposta_id,))
        cursor.execute(f"DELETE FROM propostas WHERE id = {ph} AND empresa_id = {ph}", (proposta_id, target_empresa))
        conexao.commit()

        return {"sucesso": True, "mensagem": f"Proposta #{proposta_id} excluída com sucesso."}
    finally:
        conexao.close()



# ============================================================================
# ENDPOINTS: DASHBOARD & SÉRIE TEMPORAL (PARTE 24)
# ============================================================================

@router.get("/dashboard/metricas")
def obter_metricas_dashboard(
    request: Request = None,
    empresa_id: Optional[int] = Query(None)
):
    """Retorna métricas calculadas em tempo real do CRM para o Dashboard com filtro por empresa."""
    operador = exigir_perfil(request, ["master", "admin", "funcionario"], empresa_id=empresa_id)
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"SELECT COUNT(*) AS total FROM clientes WHERE empresa_id = {ph}", (target_empresa,))
        total_clientes = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT COUNT(*) AS total FROM clientes 
            WHERE empresa_id = {ph} AND (LOWER(COALESCE(origem, '')) = 'telegram' OR (telegram_chat_id IS NOT NULL AND telegram_chat_id != ''))
        """, (target_empresa,))
        total_leads_telegram = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT COUNT(DISTINCT chat_id) AS total FROM (
                SELECT telegram_chat_id AS chat_id FROM historico_conversas WHERE empresa_id = {ph}
                UNION
                SELECT chat_id FROM conversas_telegram WHERE empresa_id = {ph}
            ) c WHERE chat_id IS NOT NULL AND chat_id != ''
        """, (target_empresa, target_empresa))
        conversas_ativas = cursor.fetchone()["total"]

        cursor.execute(f"SELECT COUNT(*) AS total FROM negocios WHERE empresa_id = {ph}", (target_empresa,))
        total_negocios = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM negocios n
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            WHERE n.empresa_id = {ph} AND (LOWER(n.estagio) = 'fechado' OR LOWER(ep.nome) = 'fechado')
        """, (target_empresa,))
        negocios_fechados = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM negocios n
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            WHERE n.empresa_id = {ph} AND (LOWER(n.estagio) = 'perdido' OR LOWER(ep.nome) = 'perdido')
        """, (target_empresa,))
        negocios_perdidos = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT COALESCE(SUM(n.valor_estimado), 0) AS receita_pipeline
            FROM negocios n
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            WHERE n.empresa_id = {ph}
              AND (n.estagio IS NULL OR LOWER(n.estagio) NOT IN ('fechado', 'perdido'))
              AND (ep.nome IS NULL OR LOWER(ep.nome) NOT IN ('fechado', 'perdido'))
        """, (target_empresa,))
        receita_pipeline = float(cursor.fetchone()["receita_pipeline"] or 0)

        cursor.execute(f"""
            SELECT COALESCE(SUM(n.valor_estimado), 0) AS receita_fechada
            FROM negocios n
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            WHERE n.empresa_id = {ph} AND (LOWER(n.estagio) = 'fechado' OR LOWER(ep.nome) = 'fechado')
        """, (target_empresa,))
        receita_fechada = float(cursor.fetchone()["receita_fechada"] or 0)

        total_validos = total_negocios - negocios_perdidos
        if total_validos > 0:
            taxa_conversao = round((negocios_fechados / total_validos) * 100, 1)
        elif total_negocios > 0:
            taxa_conversao = round((negocios_fechados / total_negocios) * 100, 1)
        else:
            taxa_conversao = 0.0

        cursor.execute(f"""
            SELECT COALESCE(ep.nome, n.estagio, 'Novo') AS estagio_label,
                   COUNT(*) AS qtd, COALESCE(SUM(n.valor_estimado), 0) AS soma_valor
            FROM negocios n
            LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
            WHERE n.empresa_id = {ph}
            GROUP BY COALESCE(ep.nome, n.estagio, 'Novo')
        """, (target_empresa,))
        estagios_raw = cursor.fetchall()
        estagios = {r["estagio_label"]: {"quantidade": r["qtd"], "valor": float(r["soma_valor"])} for r in estagios_raw}

        return {
            "empresa_id": target_empresa,
            "total_clientes": total_clientes,
            "total_leads_telegram": total_leads_telegram,
            "conversas_ativas": conversas_ativas,
            "total_negocios": total_negocios,
            "negocios_fechados": negocios_fechados,
            "negocios_perdidos": negocios_perdidos,
            "receita_pipeline": receita_pipeline,
            "receita_fechada": receita_fechada,
            "receita_total": receita_pipeline + receita_fechada,
            "taxa_conversao": taxa_conversao,
            "estagios": estagios
        }
    finally:
        conexao.close()


@router.get("/dashboard/serie-temporal")
def obter_serie_temporal(
    request: Request = None,
    empresa_id: Optional[int] = Query(None),
    metrica: str = Query("clientes"),  # 'clientes' ou 'vendas'
    meses: int = Query(12)
):
    """
    PARTE 24: Agregação mensal para série temporal:
    - 'clientes': novos clientes criados por mês (COUNT agrupado por ano/mês de clientes.criado_em)
    - 'vendas': valor de negócios fechados por mês (SUM de valor_estimado onde estágio é 'fechado')
    Isolado estritamente por empresa_id.
    """
    operador = exigir_perfil(request, ["master", "admin", "funcionario"], empresa_id=empresa_id)
    target_empresa = operador["empresa_id"] if operador["perfil"] != "master" else (empresa_id or 1)

    metrica = metrica.lower().strip()
    if metrica not in ("clientes", "vendas"):
        raise HTTPException(status_code=400, detail="Métrica inválida. Use 'clientes' ou 'vendas'.")

    meses = max(1, min(meses, 36))
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    nomes_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    try:
        # Gera lista dos últimos N meses em ordem cronológica
        hoje = datetime.now()
        meses_chaves = []
        for i in range(meses - 1, -1, -1):
            ano = hoje.year
            mes = hoje.month - i
            while mes <= 0:
                mes += 12
                ano -= 1
            chave = f"{ano:04d}-{mes:02d}"
            rotulo = f"{nomes_meses[mes - 1]}/{str(ano)[2:]}"
            meses_chaves.append({"chave": chave, "rotulo": rotulo, "valor": 0.0})

        mapa_valores = {m["chave"]: 0.0 for m in meses_chaves}

        if metrica == "clientes":
            cursor.execute(f"""
                SELECT SUBSTRING(TO_CHAR(criado_em, 'YYYY-MM') FROM 1 FOR 7) AS mes_ano,
                       COUNT(*) AS total
                FROM clientes
                WHERE empresa_id = {ph}
                GROUP BY mes_ano
            """, (target_empresa,))
            for r in cursor.fetchall():
                chave = str(r["mes_ano"])[:7]
                if chave in mapa_valores:
                    mapa_valores[chave] = float(r["total"])

        elif metrica == "vendas":
            cursor.execute(f"""
                SELECT SUBSTRING(TO_CHAR(COALESCE(n.ultima_interacao_em, n.criado_em), 'YYYY-MM') FROM 1 FOR 7) AS mes_ano,
                       COALESCE(SUM(n.valor_estimado), 0) AS total_vendas
                FROM negocios n
                LEFT JOIN etapas_pipeline ep ON n.etapa_id = ep.id
                WHERE n.empresa_id = {ph}
                  AND (LOWER(n.estagio) = 'fechado' OR LOWER(ep.nome) = 'fechado')
                GROUP BY mes_ano
            """, (target_empresa,))
            for r in cursor.fetchall():
                chave = str(r["mes_ano"])[:7]
                if chave in mapa_valores:
                    mapa_valores[chave] = float(r["total_vendas"])

        serie = []
        for m in meses_chaves:
            val = mapa_valores.get(m["chave"], 0.0)
            serie.append({
                "mes_ano": m["chave"],
                "rotulo": m["rotulo"],
                "valor": round(val, 2)
            })

        total_acumulado = sum(s["valor"] for s in serie)
        media_mensal = round(total_acumulado / len(serie), 2) if serie else 0.0

        return {
            "empresa_id": target_empresa,
            "metrica": metrica,
            "meses": meses,
            "total_acumulado": total_acumulado,
            "media_mensal": media_mensal,
            "dados": serie,
            "serie": serie
        }
    finally:
        conexao.close()


@router.post("/crm/executar-followup")
def acionar_followup_manual(horas_inatividade: int = Query(24)):
    """Dispara a rotina de reengajamento de leads inativos manualmente."""
    from app.services.followup_service import executar_followup_automatico
    resultado = executar_followup_automatico(horas_inatividade=horas_inatividade)
    return resultado
