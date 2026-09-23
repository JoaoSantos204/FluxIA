import re

from app.database.database import conectar

PALAVRAS_IRRELEVANTES = {
    "a",
    "o",
    "as",
    "os",
    "um",
    "uma",
    "uns",
    "umas",
    "de",
    "do",
    "da",
    "dos",
    "das",
    "e",
    "é",
    "em",
    "no",
    "na",
    "nos",
    "nas",
    "por",
    "para",
    "com",
    "que",
    "qual",
    "quais",
    "como",
    "onde",
    "quando",
    "quem",
    "se",
}

def buscar_contexto(pergunta: str):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT
            c.id,
            c.documento_id,
            c.numero_chunk,
            c.conteudo,
            d.nome_arquivo
        FROM chunks c
        INNER JOIN documentos d
            ON c.documento_id = d.id
        ORDER BY c.documento_id, c.numero_chunk
    """)

    chunks = cursor.fetchall()

    conexao.close()

    # Remove pontuação e separa as palavras da pergunta
    palavras_pergunta = re.findall(
        r"\b[\wÀ-ÿ]+\b",
        pergunta.lower()
    )

    # Remove palavras muito comuns
    palavras_relevantes = [
        palavra
        for palavra in palavras_pergunta
        if palavra not in PALAVRAS_IRRELEVANTES
    ]

    resultados = []

    for chunk in chunks:

        conteudo = chunk["conteudo"].lower()

        encontrou_palavra = False

        for palavra in palavras_relevantes:

            if palavra in conteudo:
                encontrou_palavra = True
                break

        if encontrou_palavra:

            resultados.append(
                {
                    "chunk_id": chunk["id"],
                    "documento_id": chunk["documento_id"],
                    "numero_chunk": chunk["numero_chunk"],
                    "nome_arquivo": chunk["nome_arquivo"],
                    "conteudo": chunk["conteudo"]
                }
            )

    return resultados

