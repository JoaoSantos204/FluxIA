import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import errors

load_dotenv()

chave_api = os.getenv("GEMINI_API_KEY")

if not chave_api:
    raise ValueError("A variável GEMINI_API_KEY não foi encontrada.")

cliente = genai.Client(api_key=chave_api)

def gerar_embedding(texto: str):
    modelo_oficial = "models/gemini-embedding-001"
    
    # Aumentamos para até 5 tentativas com esperas mais longas
    for tentativa in range(1, 6):
        try:
            resultado = cliente.models.embed_content(
                model=modelo_oficial,
                contents=texto
            )
            
            # Pausa fixa de 2 segundos entre cada chunk para respeitar a cota gratuita (~15 a 30 RPM)
            time.sleep(2.0)
            
            if hasattr(resultado, "embedding") and resultado.embedding:
                return resultado.embedding.values
            elif hasattr(resultado, "embeddings") and resultado.embeddings:
                return resultado.embeddings[0].values
                
            raise ValueError("Formato de resposta inesperado do modelo de embedding.")

        except errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                # Espera progressiva: 15s na 1ª vez, 30s na 2ª, 45s na 3ª...
                tempo_espera = tentativa * 15
                print(f"⚠️ Cota do Gemini excedida. Aguardando {tempo_espera}s para resetar a janela (Tentativa {tentativa}/5)...")
                time.sleep(tempo_espera)
            else:
                raise e

    raise Exception("Não foi possível gerar o embedding após 5 tentativas por restrição de cota da API.")