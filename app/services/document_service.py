from pathlib import Path

from pypdf import PdfReader
from docx import Document

def ler_documento(caminho_arquivo: str) -> str:

    caminho = Path(caminho_arquivo)

    extensao = caminho.suffix.lower()

    if extensao == ".txt":
        return ler_txt(caminho)

    elif extensao == ".pdf":
        return ler_pdf(caminho)

    elif extensao == ".docx":
        return ler_docx(caminho)

    else:
        raise ValueError(
            "Tipo de documento não suportado."
        )

def ler_txt(caminho: Path) -> str:

    with open(
        caminho,
        "r",
        encoding="utf-8"
    ) as arquivo:

        return arquivo.read()

def ler_pdf(caminho: Path) -> str:

    leitor = PdfReader(caminho)

    texto = ""

    for pagina in leitor.pages:

        texto_pagina = pagina.extract_text()

        if texto_pagina:
            texto += texto_pagina + "\n"

    return texto

def ler_docx(caminho: Path) -> str:

    documento = Document(caminho)

    texto = ""

    for paragrafo in documento.paragraphs:

        texto += paragrafo.text + "\n"

    return texto
