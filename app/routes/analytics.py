import json
import logging
from fastapi import APIRouter, HTTPException, Query, Header, status
from typing import Optional, List

from app.database.database import conectar, _cursor, _placeholder
from app.services.security_service import validar_perfil_admin_ou_master

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


def registrar_pergunta_historico(
    canal: str,
    empresa_id: int,
    pergunta: str,
    resposta: str,
    usuario_id: Optional[int] = None,
    telegram_chat_id: Optional[str] = None,
    documentos_utilizados: Optional[List[dict]] = None,
    teve_contexto: bool = False,
    fonte_resposta: Optional[str] = "base_conhecimento"
):
    """
    Função utilitária para persistir cada pergunta respondida no canal Telegram ou Portal.
    """
    try:
        conexao = conectar()
        cursor = _cursor(conexao)
        ph = _placeholder()

        docs_json = json.dumps(documentos_utilizados or [], ensure_ascii=False)

        cursor.execute(f"""
            INSERT INTO perguntas_historico (
                canal, empresa_id, usuario_id, telegram_chat_id,
                pergunta, resposta, documentos_utilizados, teve_contexto, fonte_resposta
            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (
            canal, empresa_id, usuario_id, telegram_chat_id,
            pergunta, resposta, docs_json, teve_contexto, fonte_resposta
        ))

        conexao.commit()
        conexao.close()
    except Exception as e:
        logger.error(f"[Analytics] Falha ao registrar pergunta no histórico: {e}")


def _validar_acesso_analytics(
    usuario_id: Optional[int] = None,
    x_user_id: Optional[int] = None,
    usuario_perfil: Optional[str] = None
):
    if usuario_perfil:
        if usuario_perfil.strip().lower() not in ("admin", "master"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas administradores podem acessar o Analytics RAG."
            )
        return
    uid = usuario_id or x_user_id
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identificação do usuário (usuario_id, cabeçalho X-User-Id ou usuario_perfil) é obrigatória para acessar o Analytics."
        )
    validar_perfil_admin_ou_master(uid)


@router.get("/documentos-mais-usados")
def documentos_mais_usados(
    empresa_id: int = Query(1),
    usuario_id: Optional[int] = Query(None),
    usuario_perfil: Optional[str] = Query(None),
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """Retorna os documentos mais acionados pelo RAG com frequência e similaridade média. Restrito a admin/master."""
    _validar_acesso_analytics(usuario_id, x_user_id, usuario_perfil)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        cursor.execute(f"""
            SELECT documentos_utilizados
            FROM perguntas_historico
            WHERE empresa_id = {ph} AND teve_contexto = {ph}
        """, (empresa_id, True))

        linhas = cursor.fetchall()
        ranking = {}

        for linha in linhas:
            docs_raw = linha.get("documentos_utilizados")
            if not docs_raw:
                continue
            try:
                docs = json.loads(docs_raw)
            except Exception:
                continue

            for d in docs:
                nome = d.get("nome_arquivo")
                sim = float(d.get("similaridade", 0.0))
                if not nome:
                    continue

                if nome not in ranking:
                    ranking[nome] = {"nome_arquivo": nome, "total_consultas": 0, "soma_similaridade": 0.0}
                ranking[nome]["total_consultas"] += 1
                ranking[nome]["soma_similaridade"] += sim

        resultado = []
        for nome, dados in ranking.items():
            qtd = dados["total_consultas"]
            media = round(dados["soma_similaridade"] / qtd, 3) if qtd > 0 else 0.0
            resultado.append({
                "nome_arquivo": nome,
                "total_consultas": qtd,
                "similaridade_media": media
            })

        resultado.sort(key=lambda x: x["total_consultas"], reverse=True)
        return {"total_documentos_rankeados": len(resultado), "ranking": resultado}
    finally:
        conexao.close()


@router.get("/assertividade")
def metricas_assertividade(
    empresa_id: int = Query(1),
    usuario_id: Optional[int] = Query(None),
    usuario_perfil: Optional[str] = Query(None),
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """Calcula taxa de cobertura e assertividade da base de conhecimento vs conhecimento geral. Restrito a admin/master."""
    _validar_acesso_analytics(usuario_id, x_user_id, usuario_perfil)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        # Total geral de perguntas registradas
        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM perguntas_historico
            WHERE empresa_id = {ph}
        """, (empresa_id,))
        total_perguntas = cursor.fetchone()["total"]

        # Total com contexto encontrado
        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM perguntas_historico
            WHERE empresa_id = {ph} AND teve_contexto = {ph}
        """, (empresa_id, True))
        perguntas_com_contexto = cursor.fetchone()["total"]

        # Por fonte
        cursor.execute(f"""
            SELECT fonte_resposta, COUNT(*) AS qtd
            FROM perguntas_historico
            WHERE empresa_id = {ph}
            GROUP BY fonte_resposta
        """, (empresa_id,))
        por_fonte_rows = cursor.fetchall()
        por_fonte = {r["fonte_resposta"]: r["qtd"] for r in por_fonte_rows}

        taxa_assertividade = 0.0
        if total_perguntas > 0:
            taxa_assertividade = round((perguntas_com_contexto / total_perguntas) * 100, 1)

        return {
            "total_perguntas": total_perguntas,
            "perguntas_com_contexto": perguntas_com_contexto,
            "taxa_assertividade": taxa_assertividade,
            "distribuicao_fonte": {
                "base_conhecimento": por_fonte.get("base_conhecimento", 0),
                "conhecimento_geral": por_fonte.get("conhecimento_geral", 0)
            }
        }
    finally:
        conexao.close()


@router.get("/historico-perguntas")
def listar_historico_perguntas(
    empresa_id: int = Query(1),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=100),
    canal: Optional[str] = Query(None),
    usuario_id: Optional[int] = Query(None),
    usuario_perfil: Optional[str] = Query(None),
    x_user_id: Optional[int] = Header(None, alias="X-User-Id")
):
    """Lista paginada do histórico completo de perguntas e respostas com metadados e autor. Restrito a admin/master."""
    _validar_acesso_analytics(usuario_id, x_user_id, usuario_perfil)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()
    try:
        filtros = [f"p.empresa_id = {ph}"]
        params = [empresa_id]

        if canal:
            filtros.append(f"p.canal = {ph}")
            params.append(canal.strip().lower())

        where = "WHERE " + " AND ".join(filtros)
        offset = (pagina - 1) * limite

        cursor.execute(f"SELECT COUNT(*) AS total FROM perguntas_historico p {where}", tuple(params))
        total_registros = cursor.fetchone()["total"]

        params.extend([limite, offset])
        cursor.execute(f"""
            SELECT p.id, p.canal, p.empresa_id, p.usuario_id, p.telegram_chat_id,
                   p.pergunta, p.resposta, p.documentos_utilizados, p.teve_contexto,
                   p.fonte_resposta, p.criado_em,
                   u.nome AS usuario_nome, u.email AS usuario_email
            FROM perguntas_historico p
            LEFT JOIN usuarios u ON u.id = p.usuario_id
            {where}
            ORDER BY p.id DESC
            LIMIT {ph} OFFSET {ph}
        """, tuple(params))

        linhas = cursor.fetchall()
        itens = []
        for r in linhas:
            item = dict(r)
            if item.get("documentos_utilizados"):
                try:
                    item["documentos_utilizados"] = json.loads(item["documentos_utilizados"])
                except Exception:
                    pass
            itens.append(item)

        return {
            "pagina_atual": pagina,
            "limite": limite,
            "total_registros": total_registros,
            "itens": itens
        }
    finally:
        conexao.close()
