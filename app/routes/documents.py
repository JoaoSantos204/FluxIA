import os
import json
import hashlib
import time
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Header, Query
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from app.database.database import conectar, _cursor, _placeholder
from app.services.document_service import ler_documento
from app.services.chunk_service import dividir_texto
from app.services.embedding_service import gerar_embedding
from app.services.security_service import validar_perfil_admin_ou_master

router = APIRouter(
    prefix="/documents",
    tags=["Documentos"]
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

EXTENSOES_PERMITIDAS = {".pdf", ".txt", ".docx", ".pptx", ".png", ".jpg", ".jpeg"}
TAMANHO_MAXIMO = 10 * 1024 * 1024


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def enviar_documento(
    arquivo: UploadFile = File(...),
    nivel_acesso: str = Form("publico"),
    empresa_id: int = Form(1),
    usuario_id: int = Form(...)
):
    """
    Upload de Documento com RBAC Real:
    Exige usuario_id de quem chama e valida no banco se o perfil é 'admin' ou 'master'.
    Retorna 403 para 'funcionario' e 'cliente'.
    Suporta .pdf, .txt, .docx, .pptx e imagens (.png, .jpg, .jpeg via Gemini multimodal).
    """
    # 1. Validação de Segurança RBAC
    usuario = validar_perfil_admin_ou_master(usuario_id)

    # 2. Validação da Extensão
    extensao = Path(arquivo.filename).suffix.lower()
    if extensao not in EXTENSOES_PERMITIDAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de arquivo não permitido: '{extensao}'. Formatos suportados: PDF, TXT, DOCX, PPTX, PNG, JPG, JPEG."
        )

    # 3. Validação do Nível de Acesso
    nivel_formatado = (nivel_acesso or "publico").strip().lower()
    if nivel_formatado not in {"publico", "interno"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nível de acesso inválido. Use 'publico' ou 'interno'."
        )

    conteudo = await arquivo.read()

    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O arquivo excede o tamanho máximo permitido de 10 MB."
        )

    hash_conteudo = hashlib.sha256(conteudo).hexdigest()

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    caminho_arquivo = None

    try:
        # 4. Checagem de Duplicados
        cursor.execute(f"""
            SELECT id, nome_arquivo
            FROM documentos
            WHERE (nome_arquivo = {ph} OR hash_conteudo = {ph}) AND empresa_id = {ph}
        """, (arquivo.filename, hash_conteudo, empresa_id))
        doc_existente = cursor.fetchone()

        if doc_existente:
            if doc_existente["nome_arquivo"] == arquivo.filename:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"O arquivo '{arquivo.filename}' já foi enviado anteriormente para esta empresa."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Um arquivo com o mesmo conteúdo já existe cadastrado nesta empresa."
                )

        # 5. Salvamento do Arquivo Físico
        nome_seguro = f"{uuid4()}{extensao}"
        caminho_arquivo = UPLOAD_DIR / nome_seguro

        with open(caminho_arquivo, "wb") as arquivo_salvo:
            arquivo_salvo.write(conteudo)

        # 6. Extração do Conteúdo (Texto, Slides PPTX ou OCR de Imagem com Gemini)
        try:
            texto_documento = ler_documento(caminho_arquivo)
        except Exception as err_leitura:
            if caminho_arquivo and caminho_arquivo.exists():
                caminho_arquivo.unlink()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Falha ao processar conteúdo do arquivo '{arquivo.filename}': {err_leitura}"
            )

        if not texto_documento or not texto_documento.strip():
            if caminho_arquivo and caminho_arquivo.exists():
                caminho_arquivo.unlink()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Não foi possível extrair nenhum conteúdo útil do arquivo '{arquivo.filename}'. Verifique se o arquivo não está vazio ou corrompido."
            )

        # 7. Divisão em Chunks
        chunks = dividir_texto(texto_documento)
        data_upload = datetime.now().isoformat()

        # 8. Inserção do Documento no Banco
        cursor.execute(f"""
            INSERT INTO documentos (
                empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo,
                conteudo_texto, hash_conteudo, nivel_acesso, data_upload
            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (empresa_id, arquivo.filename, extensao, str(caminho_arquivo), texto_documento, hash_conteudo, nivel_formatado, data_upload))

        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            documento_id = cursor.lastrowid
        else:
            cursor.execute(f"SELECT id FROM documentos WHERE hash_conteudo = {ph} AND empresa_id = {ph}", (hash_conteudo, empresa_id))
            row = cursor.fetchone()
            documento_id = row["id"] if row else None

        # 9. Geração de Embeddings e Inserção dos Chunks
        total_chunks = 0
        for numero, chunk in enumerate(chunks, start=1):
            embedding = gerar_embedding(chunk)

            cursor.execute(f"""
                INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (documento_id, numero, chunk, json.dumps(embedding)))
            total_chunks += 1

        conexao.commit()

        return {
            "id": documento_id,
            "empresa_id": empresa_id,
            "nome_arquivo": arquivo.filename,
            "tipo_arquivo": extensao,
            "nivel_acesso": nivel_formatado,
            "total_chunks": total_chunks,
            "mensagem": f"Documento '{arquivo.filename}' ({nivel_formatado.upper()}) processado com sucesso por {usuario['nome']}."
        }

    except HTTPException:
        conexao.rollback()
        raise
    except Exception as e:
        conexao.rollback()
        if caminho_arquivo and caminho_arquivo.exists():
            caminho_arquivo.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno ao processar o documento: {str(e)}"
        )
    finally:
        conexao.close()


@router.get("/")
def listar_documentos(empresa_id: int | None = None, nivel_acesso: str | None = None):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        filtros = []
        parametros = []

        if empresa_id is not None:
            filtros.append(f"empresa_id = {ph}")
            parametros.append(empresa_id)

        if nivel_acesso is not None:
            filtros.append(f"nivel_acesso = {ph}")
            parametros.append(nivel_acesso.strip().lower())

        where_clause = f"WHERE {' AND '.join(filtros)}" if filtros else ""

        cursor.execute(f"""
            SELECT id, empresa_id, nome_arquivo, tipo_arquivo, nivel_acesso, data_upload
            FROM documentos
            {where_clause}
            ORDER BY id DESC
        """, tuple(parametros))

        documentos = cursor.fetchall()

        return {
            "total": len(documentos),
            "documentos": [
                {
                    "id": doc["id"],
                    "empresa_id": doc["empresa_id"] or 1,
                    "nome_arquivo": doc["nome_arquivo"],
                    "tipo_arquivo": doc["tipo_arquivo"],
                    "nivel_acesso": doc["nivel_acesso"] or "publico",
                    "data_upload": str(doc["data_upload"])
                }
                for doc in documentos
            ]
        }
    finally:
        conexao.close()


@router.get("/{documento_id}")
def obter_documento(documento_id: int):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"""
            SELECT id, empresa_id, nome_arquivo, tipo_arquivo, conteudo_texto, nivel_acesso, data_upload
            FROM documentos
            WHERE id = {ph}
        """, (documento_id,))
        documento = cursor.fetchone()

        if documento is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento não encontrado."
            )

        return {
            "id": documento["id"],
            "empresa_id": documento["empresa_id"] or 1,
            "nome_arquivo": documento["nome_arquivo"],
            "tipo_arquivo": documento["tipo_arquivo"],
            "conteudo_texto": documento["conteudo_texto"],
            "nivel_acesso": documento["nivel_acesso"] or "publico",
            "data_upload": str(documento["data_upload"])
        }
    finally:
        conexao.close()


@router.delete("/{documento_id}")
def deletar_documento(
    documento_id: int,
    usuario_id: int | None = Query(None),
    x_user_id: int | None = Header(None, alias="X-User-Id")
):
    """Exclui documento e seus chunks. Exige perfil admin ou master."""
    uid = usuario_id or x_user_id
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identificação do usuário (usuario_id ou cabeçalho X-User-Id) é obrigatória para exclusão."
        )

    validar_perfil_admin_ou_master(uid)

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        cursor.execute(f"SELECT caminho_arquivo, nome_arquivo FROM documentos WHERE id = {ph}", (documento_id,))
        documento = cursor.fetchone()

        if documento is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento não encontrado."
            )

        caminho = Path(documento["caminho_arquivo"])
        if caminho.exists():
            caminho.unlink()

        cursor.execute(f"DELETE FROM chunks WHERE documento_id = {ph}", (documento_id,))
        cursor.execute(f"DELETE FROM documentos WHERE id = {ph}", (documento_id,))

        conexao.commit()

        return {
            "mensagem": f"Documento '{documento['nome_arquivo']}' e seus chunks foram excluídos com sucesso."
        }
    except Exception as e:
        conexao.rollback()
        raise e
    finally:
        conexao.close()