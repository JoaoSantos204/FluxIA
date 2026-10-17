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

def buscar_contexto(pergunta: str, empresa_id: int | None = None):
    if not empresa_id:
        return []

    from app.database.database import _cursor, _placeholder
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    cursor.execute(f"""
        SELECT
            c.id,
            c.documento_id,
            c.numero_chunk,
            c.conteudo,
            d.nome_arquivo
        FROM chunks c
        INNER JOIN documentos d
            ON c.documento_id = d.id
        WHERE d.empresa_id = {ph}
        ORDER BY c.documento_id, c.numero_chunk
    """, (empresa_id,))

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


def extrair_intencao_adicionar_conhecimento(mensagem: str) -> dict | None:
    """
    Detecta se o usuário no portal solicitou a adição de um documento ou nota à base de conhecimento.
    Exemplos de entrada:
    - 'Adicione à base de conhecimento que o horário aos sábados agora é das 9h às 13h.'
    - 'Cadastre na base de conhecimento este procedimento: Para reembolso de despesas...'
    - 'Acrescente à base de conhecimento o documento sobre Política de Home Office: [conteúdo]'
    """
    if not mensagem:
        return None

    msg = mensagem.strip()
    padrao = re.search(
        r"(?i)^(?:por favor\s*,?\s*)?(?:adicione|adicionar|acrescente|acrescentar|cadastre|cadastrar|inclua|incluir|salve|salvar|registre|registrar)\s+(?:um\s+(?:novo\s+)?documento\s+)?(?:[aà]|na|à)\s+base\s+de\s+conhecimento(?:\s+que|\s+o\s+seguinte|\s*:|\s+com\s+o\s+t[íi]tulo|\s+sobre|\s+documento)?\s*(.*)",
        msg,
        re.DOTALL
    )

    if not padrao:
        # Padrão alternativo com "base de conhecimento" no início ou menção direta
        padrao_alt = re.search(
            r"(?i)^(?:base de conhecimento|nova nota de conhecimento)\s*:\s*(.*)",
            msg,
            re.DOTALL
        )
        if not padrao_alt:
            return None
        conteudo_bruto = padrao_alt.group(1).strip()
    else:
        conteudo_bruto = padrao.group(1).strip()

    if not conteudo_bruto or len(conteudo_bruto) < 5:
        return None

    # Tenta identificar se o usuário passou um título explícito (ex: "Título: X ... Conteúdo: Y" ou "sobre 'X': Y")
    titulo = "Nota de Conhecimento"
    match_titulo = re.search(r"(?i)^(?:t[íi]tulo\s*:\s*|sobre\s+[\"']?([^\"':\n]+)[\"']?\s*:\s*)(.*)", conteudo_bruto, re.DOTALL)
    if match_titulo:
        cand_titulo = match_titulo.group(1).strip() if match_titulo.group(1) else ""
        if cand_titulo and len(cand_titulo) <= 60:
            titulo = cand_titulo
            conteudo = match_titulo.group(2).strip()
        else:
            conteudo = conteudo_bruto
    else:
        # Usa a primeira frase ou até 40 caracteres como título resumido
        primeira_linha = conteudo_bruto.split("\n")[0].strip()
        palavras = primeira_linha.split()[:7]
        titulo = " ".join(palavras) if palavras else "Nota de Conhecimento"
        if len(titulo) > 50:
            titulo = titulo[:50] + "..."
        conteudo = conteudo_bruto

    # Nível de acesso (detecta se pediu "público" expressamente)
    nivel = "publico" if re.search(r"(?i)\b(p[uú]blico|para os clientes|aberto ao p[uú]blico)\b", msg) else "interno"

    return {
        "titulo": titulo,
        "conteudo": conteudo,
        "nivel_acesso": nivel
    }


def adicionar_conhecimento_empresa(
    titulo: str,
    conteudo: str,
    empresa_id: int = 1,
    nivel_acesso: str = "interno"
) -> dict:
    """
    Adiciona diretamente um texto ou documento à base de conhecimento da respectiva empresa,
    dividindo em chunks, gerando embeddings e indexando no banco de dados para busca semântica imediata.
    """
    import hashlib
    import json
    from datetime import datetime
    from app.services.chunk_service import dividir_texto
    from app.services.embedding_service import gerar_embedding
    from app.database.database import _cursor, _placeholder

    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    try:
        nome_arquivo = f"{titulo.strip()[:60]}.txt"
        hash_conteudo = hashlib.sha256(conteudo.encode("utf-8")).hexdigest()
        data_upload = datetime.now().isoformat()
        nivel = (nivel_acesso or "interno").strip().lower()
        if nivel not in {"publico", "interno"}:
            nivel = "interno"

        # 1. Insere o documento na tabela documentos
        cursor.execute(f"""
            INSERT INTO documentos (
                empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo,
                conteudo_texto, hash_conteudo, nivel_acesso, data_upload, origem
            ) VALUES ({ph}, {ph}, '.txt', {ph}, {ph}, {ph}, {ph}, {ph}, 'chat_portal')
            RETURNING id
        """, (empresa_id, nome_arquivo, f"portal_nota_{hash_conteudo[:8]}", conteudo, hash_conteudo, nivel, data_upload))

        doc_row = cursor.fetchone()
        documento_id = doc_row["id"] if doc_row else None

        # 2. Divide em chunks e gera embeddings
        chunks = dividir_texto(conteudo)
        total_chunks = 0
        for numero, chunk in enumerate(chunks, start=1):
            emb = gerar_embedding(chunk)
            cursor.execute(f"""
                INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding)
                VALUES ({ph}, {ph}, {ph}, {ph})
            """, (documento_id, numero, chunk, json.dumps(emb)))
            total_chunks += 1

        conexao.commit()

        return {
            "documento_id": documento_id,
            "titulo": nome_arquivo,
            "total_chunks": total_chunks,
            "nivel_acesso": nivel,
            "sucesso": True
        }
    except Exception as e:
        conexao.rollback()
        raise e
    finally:
        conexao.close()


def obter_resumo_corporativo_empresa(empresa_id: int, pergunta: str = "") -> str:
    """
    Consulta e estrutura a visão holística dos dados da empresa autenticada:
    - Clientes cadastrados
    - Produtos e serviços
    - Propostas comerciais
    - Contratos vigentes
    - Oportunidades do funil de vendas (CRM)
    Permite que o assistente interno do portal responda perguntas gerenciais e operacionais completas.
    """
    from app.database.database import _cursor, _placeholder
    conexao = conectar()
    cursor = _cursor(conexao)
    ph = _placeholder()

    linhas_resumo = []
    linhas_resumo.append(f"=== INFORMAÇÕES OPERACIONAIS E COMERCIAIS DA EMPRESA (ID: {empresa_id}) ===")

    try:
        # 1. Clientes
        cursor.execute(f"""
            SELECT id, nome, email, telefone, origem, criado_em
            FROM clientes
            WHERE empresa_id = {ph}
            ORDER BY id DESC
            LIMIT 15
        """, (empresa_id,))
        clientes = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) AS total FROM clientes WHERE empresa_id = {ph}", (empresa_id,))
        total_clientes = cursor.fetchone()["total"]

        linhas_resumo.append(f"\n👥 CARTEIRA DE CLIENTES (Total: {total_clientes}):")
        if clientes:
            for cl in clientes:
                tel = cl.get("telefone") or "Não informado"
                email = cl.get("email") or "Não informado"
                dt = str(cl.get("criado_em") or "")[:10]
                linhas_resumo.append(f"- Cliente #{cl['id']}: {cl['nome']} | Tel/Whats: {tel} | Email: {email} | Origem: {cl.get('origem')} | Cadastro: {dt}")
        else:
            linhas_resumo.append("- Nenhum cliente cadastrado no momento.")

        # 2. Produtos e Serviços
        cursor.execute(f"""
            SELECT id, nome, preco, descricao, ativo
            FROM produtos
            WHERE empresa_id = {ph}
            ORDER BY id ASC
        """, (empresa_id,))
        produtos = cursor.fetchall()

        linhas_resumo.append(f"\n📦 CATÁLOGO DE PRODUTOS E SERVIÇOS ({len(produtos)} cadastrados):")
        if produtos:
            for pr in produtos:
                status_prod = "Ativo" if pr.get("ativo") else "Inativo"
                val = f"R$ {float(pr.get('preco') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                desc = f" ({pr.get('descricao')})" if pr.get("descricao") else ""
                linhas_resumo.append(f"- Produto #{pr['id']}: {pr['nome']} | Preço: {val} | Status: {status_prod}{desc}")
        else:
            linhas_resumo.append("- Nenhum produto cadastrado no momento.")

        # 3. Propostas Comerciais
        cursor.execute(f"""
            SELECT p.id, p.valor, p.status_envio, p.validade_dias, p.criado_em,
                   cl.nome AS cliente_nome, prod.nome AS produto_nome
            FROM propostas p
            LEFT JOIN clientes cl ON p.cliente_id = cl.id
            LEFT JOIN produtos prod ON p.produto_id = prod.id
            WHERE p.empresa_id = {ph}
            ORDER BY p.id DESC
            LIMIT 15
        """, (empresa_id,))
        propostas = cursor.fetchall()

        linhas_resumo.append(f"\n📋 PROPOSTAS COMERCIAIS DA EMPRESA ({len(propostas)} recentes):")
        if propostas:
            for prop in propostas:
                val = f"R$ {float(prop.get('valor') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                cl_nome = prop.get("cliente_nome") or "Cliente não especificado"
                prod_nome = prop.get("produto_nome") or "Geral"
                dt = str(prop.get("criado_em") or "")[:10]
                status_env = prop.get("status_envio") or "Registrada"
                linhas_resumo.append(f"- Proposta #{prop['id']}: Cliente: {cl_nome} | Produto: {prod_nome} | Valor: {val} | Status: {status_env} | Data: {dt}")
        else:
            linhas_resumo.append("- Nenhuma proposta registrada no momento.")

        # 4. Contratos
        cursor.execute(f"""
            SELECT c.id, c.valor_contrato, c.status, c.data_assinatura,
                   cl.nome AS cliente_nome, prod.nome AS produto_nome
            FROM contratos c
            JOIN negocios n ON c.negocio_id = n.id
            LEFT JOIN clientes cl ON n.cliente_id = cl.id
            LEFT JOIN produtos prod ON n.produto_id = prod.id
            WHERE n.empresa_id = {ph}
            ORDER BY c.id DESC
            LIMIT 15
        """, (empresa_id,))
        contratos = cursor.fetchall()

        linhas_resumo.append(f"\n📄 CONTRATOS DA EMPRESA ({len(contratos)} recentes/ativos):")
        if contratos:
            for ct in contratos:
                val = f"R$ {float(ct.get('valor_contrato') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                cl_nome = ct.get("cliente_nome") or "Cliente não informado"
                prod_nome = ct.get("produto_nome") or "Serviço"
                dt_ass = str(ct.get("data_assinatura") or "")[:10]
                status_ct = ct.get("status") or "Ativo"
                linhas_resumo.append(f"- Contrato #{ct['id']}: Cliente: {cl_nome} | Serviço: {prod_nome} | Valor: {val} | Status: {status_ct} | Assinatura: {dt_ass or 'Pendente'}")
        else:
            linhas_resumo.append("- Nenhum contrato registrado para esta empresa no momento.")

        # 5. Pipeline / CRM
        cursor.execute(f"""
            SELECT estagio, COUNT(*) AS qtd, SUM(COALESCE(valor_estimado, 0)) AS total_valor
            FROM negocios
            WHERE empresa_id = {ph}
            GROUP BY estagio
            ORDER BY estagio
        """, (empresa_id,))
        crm_estagios = cursor.fetchall()

        linhas_resumo.append("\n📊 FUNIL DE VENDAS / CRM DA EMPRESA:")
        if crm_estagios:
            for crm in crm_estagios:
                val = f"R$ {float(crm.get('total_valor') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                linhas_resumo.append(f"- Estágio '{crm['estagio']}': {crm['qtd']} negociação(ões) (Total: {val})")
        else:
            linhas_resumo.append("- Nenhuma oportunidade registrada no CRM da empresa.")

        return "\n".join(linhas_resumo)
    except Exception as e:
        linhas_resumo.append(f"\n[Aviso: Não foi possível carregar os registros operacionais completos: {e}]")
        return "\n".join(linhas_resumo)
    finally:
        conexao.close()

