def dividir_texto(
    texto: str,
    tamanho_chunk: int = 1000,
    sobreposicao: int = 200
):
    """
    Divide um texto em partes menores (chunks),
    evitando cortar palavras ao meio.


    tamanho_chunk:
        Quantidade aproximada de caracteres por chunk.

    sobreposicao:
        Quantidade aproximada de caracteres repetidos
        entre um chunk e o próximo.
    """

    if not texto.strip():
        return []

    if tamanho_chunk <= 0:
        raise ValueError(
            "O tamanho do chunk deve ser maior que zero."
        )

    if sobreposicao < 0:
        raise ValueError(
            "A sobreposição não pode ser negativa."
        )

    if sobreposicao >= tamanho_chunk:
        raise ValueError(
            "A sobreposição deve ser menor que o tamanho do chunk."
        )

    chunks = []

    inicio = 0
    tamanho_texto = len(texto)

    while inicio < tamanho_texto:

        limite = min(
            inicio + tamanho_chunk,
            tamanho_texto
        )

        # Procura o último espaço antes do limite
        # para evitar cortar uma palavra.
        if limite < tamanho_texto:

            fim = texto.rfind(" ", inicio, limite)

            if fim <= inicio:
                fim = limite

        else:
            fim = limite

        chunk = texto[inicio:fim].strip()

        if chunk:
            chunks.append(chunk)

        # Chegamos ao final do documento.
        if fim >= tamanho_texto:
            break

        # Define aproximadamente onde a sobreposição deve começar.
        novo_inicio = fim - sobreposicao

        # Procura o próximo espaço para garantir
        # que o próximo chunk comece no início de uma palavra.
        inicio = texto.find(" ", novo_inicio, fim)

        if inicio == -1:
            inicio = novo_inicio
        else:
            inicio += 1

    return chunks

