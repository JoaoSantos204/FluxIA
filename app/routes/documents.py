import os
import json
import hashlib
import time
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Security, Depends
from fastapi.security import APIKeyHeader
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from app.database.database import conectar, _cursor, _placeholder
from app.services.document_service import ler_documento
from app.services.chunk_service import dividir_texto
from app.services.embedding_service import gerar_embedding
from app.services.security_service import verificar_admin_api_key, api_key_header

router = APIRouter(
    prefix="/documents",
    tags=["Documentos"]
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

EXTENSOES_PERMITIDAS = {".pdf", ".txt", ".docx"}
TAMANHO_MAXIMO = 10 * 1024 * 1024


@router.post("/upload", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verificar_admin_api_key)])
async def enviar_documento(
    arquivo: UploadFile = File(...),
    nivel_acesso: str = Form("publico"),
    empresa_id: int = Form(1)
):
    extensao = Path(arquivo.filename).suffix.lower()

    if extensao not in EXTENSOES_PERMITIDAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipo de arquivo não permitido. Envie um arquivo PDF, TXT ou DOCX."
        )

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

    try:
        # 1. Checagem de Duplicados
        cursor.execute(f"""
            SELECT id, nome_arquivo
            FROM documentos
            WHERE nome_arquivo = {ph} OR hash_conteudo = {ph}
        """, (arquivo.filename, hash_conteudo))
        doc_existente = cursor.fetchone()

        if doc_existente:
            if doc_existente["nome_arquivo"] == arquivo.filename:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"O arquivo '{arquivo.filename}' já foi enviado anteriormente."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Um arquivo com o mesmo conteúdo já existe no sistema."
                )

        # 2. Salvamento do Arquivo Físico
        nome_seguro = f"{uuid4()}{extensao}"
        caminho_arquivo = UPLOAD_DIR / nome_seguro

        with open(caminho_arquivo, "wb") as arquivo_salvo:
            arquivo_salvo.write(conteudo)

        # 3. Extração do Texto
        texto_documento = ler_documento(caminho_arquivo)

        if not texto_documento.strip():
            if caminho_arquivo.exists():
                caminho_arquivo.unlink()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não foi possível extrair conteúdo textual do documento enviado."
            )

        # 4. Divisão em Chunks
        chunks = dividir_texto(texto_documento)
        data_upload = datetime.now().isoformat()

        # 5. Inserção do Documento no Banco
        cursor.execute(f"""
            INSERT INTO documentos (
                empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo,
                conteudo_texto, hash_conteudo, nivel_acesso, data_upload
            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """, (empresa_id, arquivo.filename, extensao, str(caminho_arquivo), texto_documento, hash_conteudo, nivel_formatado, data_upload))

        # Obter documento_id (lastrowid para SQLite; fallback para Postgres)
        if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
            documento_id = cursor.lastrowid
        else:
            cursor.execute(f"SELECT id FROM documentos WHERE hash_conteudo = {ph}", (hash_conteudo,))
            row = cursor.fetchone()
            documento_id = row["id"] if row else None

        # 6. Geração de Embeddings e Inserção dos Chunks
        for numero, chunk in enumerate(chunks, start=1):
            embedding = gerar_embedding(chunk)

            cursor.execute(f"""
                INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (documento_id, numero, chunk, json.dumps(embedding)))
            # Pequena pausa para evitar estourar a cota da API do Gemini (429 Rate Limit)
            time.sleep(0.3)

        conexao.commit()

        return {
            "mensagem": "Documento enviado com sucesso!",
            "documento_id": documento_id,
            "empresa_id": empresa_id,
            "nome_arquivo": arquivo.filename,
            "nivel_acesso": nivel_formatado,
            "quantidade_caracteres": len(texto_documento),
            "total_chunks": len(chunks)
        }

    except Exception as e:
        conexao.rollback()
        raise e

    finally:
        conexao.close()


@router.get("/")
def listar_documentos(empresa_id: int | None = None):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        if empresa_id:
            cursor.execute(f"""
                SELECT id, empresa_id, nome_arquivo, tipo_arquivo, nivel_acesso, data_upload
                FROM documentos
                WHERE empresa_id = {ph}
                ORDER BY id DESC
            """, (empresa_id,))
        else:
            cursor.execute("""
                SELECT id, empresa_id, nome_arquivo, tipo_arquivo, nivel_acesso, data_upload
                FROM documentos
                ORDER BY id DESC
            """)
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
def obter_documentos(documento_id: int):
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


@router.delete("/{documento_id}", dependencies=[Depends(verificar_admin_api_key)])
def deletar_documento(documento_id: int):
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        # 1. Busca o documento
        cursor.execute(f"SELECT caminho_arquivo, nome_arquivo FROM documentos WHERE id = {ph}", (documento_id,))
        documento = cursor.fetchone()

        if documento is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento não encontrado."
            )

        # 2. Apaga o arquivo do disco
        caminho = Path(documento["caminho_arquivo"])
        if caminho.exists():
            caminho.unlink()

        # 3. Elimina os chunks e o documento
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