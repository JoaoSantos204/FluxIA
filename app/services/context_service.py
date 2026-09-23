import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError(
        "A variável GEMINI_API_KEY não foi encontrada."
    )

cliente = genai.Client(api_key=API_KEY)

def verificar_contexto(pergunta: str, contexto: str) -> bool:
    instrucoes = (
        "Você é um verificador de contexto de uma aplicação chamada FluxIA.\n\n"

        "Sua função é verificar se o CONTEXTO fornecido possui "
        "informações suficientes para responder à PERGUNTA.\n\n"

        "Responda SOMENTE com uma destas duas opções:\n"
        "SIM\n"
        "NAO\n\n"

        "Responde SIM somente quando o contexto possuir "
        "informações suficientes para responder à pergunta.\n\n"

        "Responda NAO quando o contexto não possuir a informação "
        "necessária para responder à pergunta.\n\n"

        "CONTEXTO:\n"
        f"{contexto}\n\n"

        "PERGUNTA:\n"
        f"{pergunta}"
    )

    tentativas = 3

    for tentativa in range(tentativas):
        try:
            resposta = cliente.models.generate_content(
                model="gemini-3.6-flash",
                contents=instrucoes
            )

            resultado = resposta.text.strip().upper()

            return resultado == "SIM"
        except errors.ServerError:
            if tentativa == tentativas - 1:
                return False

            tempo_espera = 2 ** tentativa
            time.sleep(tempo_espera)
        except Exception:
            return False