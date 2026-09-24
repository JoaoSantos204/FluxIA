from fastapi import APIRouter, HTTPException, status, Query, Body, Header
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.database.database import conectar, _cursor, _placeholder
from app.services.security_service import validar_perfil_admin_ou_master

router = APIRouter(tags=["CRM"])

# ============================================================================
# SCHEMAS PYDANTIC
# ============================================================================

class ProdutoCreate(BaseModel):
    empresa_id: int = 1
    nome: str
    descricao: Optional[str] = None
    ativo: bool = True
    usuario_id: int

class ProdutoUpdate(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    ativo: Optional[bool] = None
    usuario_id: int

class ClienteCreate(BaseModel):
    empresa_id: int = 1
    nome: str
    telefone: Optional[str] = None
    email: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    origem: str = "portal"

class NegocioCreate(BaseModel):
    empresa_id: int = 1
    cliente_id: int
    produto_id: Optional[int] = None
    valor_estimado: float = 0.0
    estagio: str = "novo"

class NegocioUpdate(BaseModel):
    estagio: Optional[str] = None
    valor_estimado: Optional[float] = None
    produto_id: Optional[int] = None
    proposta_enviada: Optional[bool] = None

class ContratoUpdate(BaseModel):
    status: str  # 'pendente', 'assinado', 'cancelado'
    valor_contrato: Optional[float] = None


# ============================================================================
# ENDPOINTS: PRODUTOS (CRUD com proteção RBAC)
# ============================================================================

@router.get("/produtos")
def listar_produtos(empresa_id: int = Query(1), apenas_ativos: bool = Query(True)):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        where = f"WHERE empresa_id = {ph}"
        params = [empresa_id]
        if apenas_ativos:
            where += f" AND ativo = {ph}"
            params.append(True)

        cursor.execute(f"SELECT id, empresa_id, nome, descricao, ativo FROM produtos {where} ORDER BY id ASC", tuple(params))
        produtos = cursor.fetchall()
        return {"total": len(produtos), "produtos": [dict(p) for p in produtos]}
    finally:
        conexao.close()


@router.post("/produtos", status_code=status.HTTP_201_CREATED)
def criar_produto(dados: ProdutoCreate):
    validar_perfil_admin_ou_master(dados.usuario_id)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            INSERT INTO produtos (empresa_id, nome, descricao, ativo)
            VALUES ({ph}, {ph}, {ph}, {ph})
        """, (dados.empresa_id, dados.nome, dados.descricao, dados.ativo))

        conexao.commit()
        return {"mensagem": f"Produto '{dados.nome}' cadastrado com sucesso!"}
    finally:
        conexao.close()


@router.put("/produtos/{produto_id}")
def atualizar_produto(produto_id: int, dados: ProdutoUpdate):
    validar_perfil_admin_ou_master(dados.usuario_id)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        campos = []
        valores = []
        if dados.nome is not None:
            campos.append(f"nome = {ph}")
            valores.append(dados.nome)
        if dados.descricao is not None:
            campos.append(f"descricao = {ph}")
            valores.append(dados.descricao)
        if dados.ativo is not None:
            campos.append(f"ativo = {ph}")
            valores.append(dados.ativo)

        if not campos:
            return {"mensagem": "Nenhum campo para atualizar."}

        valores.append(produto_id)
        cursor.execute(f"""
            UPDATE produtos SET {', '.join(campos)} WHERE id = {ph}
        """, tuple(valores))

        conexao.commit()
        return {"mensagem": f"Produto ID {produto_id} atualizado com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: CLIENTES (CRM)
# ============================================================================

@router.get("/clientes")
def listar_clientes(empresa_id: int = Query(1)):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome, telefone, email, telegram_chat_id, origem, criado_em
            FROM clientes
            WHERE empresa_id = {ph}
            ORDER BY id DESC
        """, (empresa_id,))
        clientes = cursor.fetchall()
        return {"total": len(clientes), "clientes": [dict(c) for c in clientes]}
    finally:
        conexao.close()


@router.post("/clientes", status_code=status.HTTP_201_CREATED)
def criar_cliente(dados: ClienteCreate):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            INSERT INTO clientes (empresa_id, nome, telefone, email, telegram_chat_id, origem)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (dados.empresa_id, dados.nome, dados.telefone, dados.email, dados.telegram_chat_id, dados.origem))

        conexao.commit()
        return {"mensagem": f"Cliente '{dados.nome}' cadastrado com sucesso!"}
    finally:
        conexao.close()


@router.get("/clientes/{cliente_id}")
def obter_cliente(cliente_id: int):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome, telefone, email, telegram_chat_id, origem, criado_em
            FROM clientes WHERE id = {ph}
        """, (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente não encontrado.")

        cursor.execute(f"""
            SELECT id, valor_estimado, estagio, proposta_enviada, ultima_interacao_em
            FROM negocios WHERE cliente_id = {ph}
        """, (cliente_id,))
        negocios = cursor.fetchall()

        res = dict(cliente)
        res["negocios"] = [dict(n) for n in negocios]
        return res
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: NEGÓCIOS (Pipeline & Kanban)
# ============================================================================

@router.get("/negocios")
def listar_negocios(
    empresa_id: int = Query(1),
    estagio: Optional[str] = Query(None),
    cliente_id: Optional[int] = Query(None)
):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        filtros = [f"n.empresa_id = {ph}"]
        params = [empresa_id]

        if estagio:
            filtros.append(f"n.estagio = {ph}")
            params.append(estagio.strip().lower())

        if cliente_id:
            filtros.append(f"n.cliente_id = {ph}")
            params.append(cliente_id)

        where = "WHERE " + " AND ".join(filtros)

        cursor.execute(f"""
            SELECT 
                n.id, n.empresa_id, n.cliente_id, n.produto_id, 
                n.valor_estimado, n.estagio, n.proposta_enviada,
                n.ultima_interacao_em, n.ultimo_followup_em, n.criado_em,
                c.nome AS cliente_nome, c.telefone AS cliente_telefone, c.telegram_chat_id,
                p.nome AS produto_nome
            FROM negocios n
            JOIN clientes c ON n.cliente_id = c.id
            LEFT JOIN produtos p ON n.produto_id = p.id
            {where}
            ORDER BY n.ultima_interacao_em DESC
        """, tuple(params))

        negocios = cursor.fetchall()
        return {"total": len(negocios), "negocios": [dict(n) for n in negocios]}
    finally:
        conexao.close()


@router.post("/negocios", status_code=status.HTTP_201_CREATED)
def criar_negocio(dados: NegocioCreate):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            INSERT INTO negocios (empresa_id, cliente_id, produto_id, valor_estimado, estagio)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
        """, (dados.empresa_id, dados.cliente_id, dados.produto_id, dados.valor_estimado, dados.estagio))

        conexao.commit()
        return {"mensagem": "Negócio cadastrado no pipeline com sucesso!"}
    finally:
        conexao.close()


@router.patch("/negocios/{negocio_id}")
def atualizar_negocio(negocio_id: int, dados: NegocioUpdate):
    """Permite ao atendente editar manualmente o estágio, valor ou produto do negócio."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # Busca estado atual
        cursor.execute(f"SELECT id, estagio, valor_estimado, empresa_id FROM negocios WHERE id = {ph}", (negocio_id,))
        negocio = cursor.fetchone()
        if not negocio:
            raise HTTPException(status_code=404, detail="Negócio não encontrado.")

        campos = ["ultima_interacao_em = CURRENT_TIMESTAMP"]
        valores = []

        if dados.estagio is not None:
            estagio_fmt = dados.estagio.strip().lower()
            if estagio_fmt not in ('novo','qualificado','proposta','negociacao','fechado','perdido'):
                raise HTTPException(status_code=400, detail="Estágio inválido.")
            campos.append(f"estagio = {ph}")
            valores.append(estagio_fmt)

        if dados.valor_estimado is not None:
            campos.append(f"valor_estimado = {ph}")
            valores.append(dados.valor_estimado)

        if dados.produto_id is not None:
            campos.append(f"produto_id = {ph}")
            valores.append(dados.produto_id)

        if dados.proposta_enviada is not None:
            campos.append(f"proposta_enviada = {ph}")
            valores.append(dados.proposta_enviada)

        valores.append(negocio_id)
        cursor.execute(f"UPDATE negocios SET {', '.join(campos)} WHERE id = {ph}", tuple(valores))

        # Se o estágio virou 'fechado', garante criação automática de contrato pendente
        if dados.estagio and dados.estagio.strip().lower() == "fechado":
            val = dados.valor_estimado if dados.valor_estimado is not None else (negocio["valor_estimado"] or 0)
            cursor.execute(f"""
                INSERT INTO contratos (negocio_id, status, valor_contrato)
                VALUES ({ph}, 'pendente', {ph})
                ON CONFLICT (negocio_id) DO NOTHING
            """, (negocio_id, val))

        conexao.commit()
        return {"mensagem": f"Negócio {negocio_id} atualizado com sucesso."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: CONTRATOS (CRM)
# ============================================================================

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
        return {"mensagem": f"Contrato {contrato_id} atualizado para status '{dados.status}'."}
    finally:
        conexao.close()


# ============================================================================
# ENDPOINTS: DASHBOARD & MÉTRICAS
# ============================================================================

@router.get("/dashboard/metricas")
def obter_metricas_dashboard(empresa_id: int = Query(1)):
    """Retorna métricas calculadas em tempo real do CRM para o Dashboard."""
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # Total de clientes
        cursor.execute(f"SELECT COUNT(*) AS total FROM clientes WHERE empresa_id = {ph}", (empresa_id,))
        total_clientes = cursor.fetchone()["total"]

        # Total de negócios
        cursor.execute(f"SELECT COUNT(*) AS total FROM negocios WHERE empresa_id = {ph}", (empresa_id,))
        total_negocios = cursor.fetchone()["total"]

        # Negócios fechados
        cursor.execute(f"SELECT COUNT(*) AS total FROM negocios WHERE empresa_id = {ph} AND estagio = 'fechado'", (empresa_id,))
        negocios_fechados = cursor.fetchone()["total"]

        # Negócios perdidos
        cursor.execute(f"SELECT COUNT(*) AS total FROM negocios WHERE empresa_id = {ph} AND estagio = 'perdido'", (empresa_id,))
        negocios_perdidos = cursor.fetchone()["total"]

        # Receita em pipeline (ativos)
        cursor.execute(f"""
            SELECT COALESCE(SUM(valor_estimado), 0) AS receita_pipeline
            FROM negocios
            WHERE empresa_id = {ph} AND estagio NOT IN ('fechado', 'perdido')
        """, (empresa_id,))
        receita_pipeline = float(cursor.fetchone()["receita_pipeline"] or 0)

        # Receita total fechada
        cursor.execute(f"""
            SELECT COALESCE(SUM(valor_estimado), 0) AS receita_fechada
            FROM negocios
            WHERE empresa_id = {ph} AND estagio = 'fechado'
        """, (empresa_id,))
        receita_fechada = float(cursor.fetchone()["receita_fechada"] or 0)

        # Taxa de conversão: fechados / (total_negocios - perdidos) ou fechados / total
        total_validos = total_negocios - negocios_perdidos
        if total_validos > 0:
            taxa_conversao = round((negocios_fechados / total_validos) * 100, 1)
        elif total_negocios > 0:
            taxa_conversao = round((negocios_fechados / total_negocios) * 100, 1)
        else:
            taxa_conversao = 0.0

        # Contagem por estágio
        cursor.execute(f"""
            SELECT estagio, COUNT(*) AS qtd, COALESCE(SUM(valor_estimado), 0) AS soma_valor
            FROM negocios
            WHERE empresa_id = {ph}
            GROUP BY estagio
        """, (empresa_id,))
        estagios_raw = cursor.fetchall()
        estagios = {r["estagio"]: {"quantidade": r["qtd"], "valor": float(r["soma_valor"])} for r in estagios_raw}

        return {
            "total_clientes": total_clientes,
            "total_negocios": total_negocios,
            "negocios_fechados": negocios_fechados,
            "negocios_perdidos": negocios_perdidos,
            "receita_pipeline": receita_pipeline,
            "receita_fechada": receita_fechada,
            "taxa_conversao": taxa_conversao,
            "estagios": estagios
        }
    finally:
        conexao.close()


@router.post("/crm/executar-followup")
def acionar_followup_manual(horas_inatividade: int = Query(24)):
    """Dispara a rotina de reengajamento de leads inativos manualmente."""
    from app.services.followup_service import executar_followup_automatico
    resultado = executar_followup_automatico(horas_inatividade=horas_inatividade)
    return resultado

