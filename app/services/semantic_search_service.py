import json

from app.database.database import conectar
from app.services.embedding_service import gerar_embedding
from app.services.similarity_service import calcular_similaridade

LIMIAR_SIMILARIDADE = 0.70

def buscar_chunks_semanticamente(pergunta: str, limite: int = 3):

    # Gera o embedding da pergunta
    embedding_pergunta = gerar_embedding(pergunta)

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT
            c.id,
            c.documento_id,
            c.numero_chunk,
            c.conteudo,
            c.embedding,
            d.nome_arquivo
        FROM chunks c
        INNER JOIN documentos d
            ON c.documento_id = d.id
        WHERE c.embedding IS NOT NULL
    """)

    chunks = cursor.fetchall()
    conexao.close()

    resultados = []

    # Calcula a similaridade de todos os chunks
    for chunk in chunks:
        embedding_chunk = json.loads(
            chunk["embedding"]
        )

        similaridade = calcular_similaridade(
            embedding_pergunta,
            embedding_chunk
        )

        resultados.append({
            "chunk_id": chunk["id"],
            "documento_id": chunk["documento_id"],
            "numero_chunk": chunk["numero_chunk"],
            "nome_arquivo": chunk["nome_arquivo"],
            "conteudo": chunk["conteudo"],
            "similaridade": similaridade
        })
            
    # Ordena os chunks pela maior similaridade
    resultados.sort(
        key=lambda resultado: resultado["similaridade"],
        reverse=True
    )

    # Mantém somente os chunks que atingiram o limite
    resultados_relevantes = [
        resultado
        for resultado in resultados
        if resultado["similaridade"] >= LIMIAR_SIMILARIDADE
    ]

    # Mantém somente os melhores resultados semânticos
    resultados_relevantes = resultados_relevantes[:limite]

    if not resultados_relevantes:
        return []

    # Guarda os resultados já calculados para preservar
    # a simularidade real dos chunks
    resultados_por_id = {
        resultado["chunk_id"]: resultado
        for resultado in resultados
    }

    # Começamos com os melhores resultados semânticos
    resultados_finais = list(resultados_relevantes)

    # Expande o contexto somente ao retor do melhor resultado
    melhor_resultado = resultados_relevantes[0]

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT
            id,
            documento_id,
            numero_chunk
        FROM chunks
        WHERE documento_id = ?
            AND numero_chunk in (?, ?)
        """,(
            melhor_resultado["documento_id"],
            melhor_resultado["numero_chunk"] - 1,
            melhor_resultado["numero_chunk"] + 1
        ))

    vizinhos = cursor.fetchall()
    conexao.close()

    # Identifica os chunks que já estão nos resultados
    ids_existentes = {
        resultado["chunk_id"]
        for resultado in resultados_finais
    }

    # Adiciona os chunks vizinhos
    for vizinho in vizinhos:

        # Não adiciona novamente um chunk que já está
        # entre os resultados semânticos
        if vizinho["id"] in ids_existentes:
            continue

        resultado_vizinho = resultados_por_id.get(vizinho["id"])

        if resultado_vizinho:
            resultado_complementar = dict(resultado_vizinho)

            # Indica que esse chunk foi adicionado apenas
            # para complementar o contexto
            resultado_complementar["similaridade"] = None

            resultados_finais.append(resultado_complementar)

    # Organiza os chunks novamente na ordem original
    # do documento
    resultados_finais.sort(
        key=lambda resultado: resultado["numero_chunk"]
    )

    return resultados_finais