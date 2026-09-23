import json
import numpy as np

def calcular_similaridade(vetor_a, vetor_b):
    """
    Calcula a similaridade de cosseno entre dois vetores.
    """

    vetor_a = np.array(vetor_a)
    vetor_b = np.array(vetor_b)

    norma_a = np.linalg.norm(vetor_a)
    norma_b = np.linalg.norm(vetor_b)

    if norma_a == 0 or norma_b == 0:
        return 0.0

    similaridade = np.dot(vetor_a, vetor_b) / (
        norma_a * norma_b
    )

    return float(similaridade)


def converter_embedding(embedding_json):
    """
    Converte o embedding armazenado como JSON
    novamente para uma lista de números.
    """

    return json.loads(embedding_json)

