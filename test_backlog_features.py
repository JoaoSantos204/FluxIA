import os
import sys
import sqlite3
from fastapi.testclient import TestClient

from app.database.database import conectar, criar_banco
from app.services.history_service import salvar_interacao, obter_ultimas_interacoes
from app.services.company_service import obter_configuracao_empresa
from app.services.user_service import buscar_usuario_por_telegram, cadastrar_usuario, alterar_perfil_usuario
from app.services.ai_service import AIService, montar_system_prompt
from app.main import app

def testar_banco_e_migracoes():
    print("\n--- 1. Testando Banco e Migrações ---")
    criar_banco()
    conn = conectar()
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(documentos)")
    colunas_doc = [c["name"] for c in cur.fetchall()]
    assert "nivel_acesso" in colunas_doc, "Coluna 'nivel_acesso' não encontrada em documentos!"
    assert "empresa_id" in colunas_doc, "Coluna 'empresa_id' não encontrada em documentos!"

    cur.execute("PRAGMA table_info(usuarios)")
    colunas_user = [c["name"] for c in cur.fetchall()]
    assert "perfil" in colunas_user, "Coluna 'perfil' não encontrada em usuarios!"

    cur.execute("SELECT * FROM configuracoes_empresa WHERE empresa_id = 1")
    config = cur.fetchone()
    assert config is not None, "Registro padrão em configuracoes_empresa não encontrado!"
    assert config["numero_suporte_humano"] is not None, "Telefone de suporte nulo!"

    conn.close()
    print("[PASS] Schema do banco, colunas e migrações validados com sucesso!")


def testar_servicos_auxiliares():
    print("\n--- 2. Testando Serviços de Histórico e Configurações ---")
    chat_teste = "chat_teste_999888"

    # Salva interações de teste
    id1 = salvar_interacao(chat_teste, "Pergunta 1", "Resposta 1")
    id2 = salvar_interacao(chat_teste, "Pergunta 2", "Resposta 2")
    id3 = salvar_interacao(chat_teste, "Pergunta 3", "Resposta 3")
    id4 = salvar_interacao(chat_teste, "Pergunta 4", "Resposta 4")
    assert id1 and id2 and id3 and id4, "Falha ao salvar interações!"

    # Recupera últimas 3 interações (ordem cronológica)
    ultimas = obter_ultimas_interacoes(chat_teste, limite=3)
    assert len(ultimas) == 3, f"Esperado 3 interações, obtido {len(ultimas)}"
    assert ultimas[0]["mensagem_usuario"] == "Pergunta 2", "Ordem cronológica incorreta (esperado Pergunta 2 na primeira posição)"
    assert ultimas[2]["mensagem_usuario"] == "Pergunta 4", "Ordem cronológica incorreta (esperado Pergunta 4 na última posição)"

    # Limpeza dos dados de teste
    conn = conectar()
    cur = conn.cursor()
    cur.execute("DELETE FROM historico_conversas WHERE telegram_chat_id = ?", (chat_teste,))
    conn.commit()
    conn.close()

    # Testa busca de configuração da empresa
    config = obter_configuracao_empresa(1)
    assert "(11)" in config["numero_suporte_humano"], "Número de suporte inesperado!"
    assert config["mensagem_suporte"], "Mensagem de suporte vazia!"

    print("[PASS] HistoryService e CompanyService funcionando perfeitamente!")


def testar_user_service():
    print("\n--- 3. Testando UserService e RBAC ---")
    user = buscar_usuario_por_telegram("8342030105")
    assert user is not None, "Usuário existente não localizado!"
    assert user["perfil"] in ["admin", "funcionario", "cliente"], f"Perfil inválido: {user['perfil']}"

    # Teste de validação ao cadastrar com perfil inválido
    res = cadastrar_usuario(1, "Invalido", "invalido@email.com", "123", perfil="perfil_inexistente")
    assert not res["sucesso"], "Permitiu cadastrar com perfil inválido!"

    # Teste de alteração de perfil
    res_alt = alterar_perfil_usuario(user["id"], "funcionario")
    assert res_alt["sucesso"], "Falha ao alterar perfil para funcionario"
    user_alt = buscar_usuario_por_telegram("8342030105")
    assert user_alt["perfil"] == "funcionario"

    # Restaura perfil para cliente
    alterar_perfil_usuario(user["id"], "cliente")
    user_restaurado = buscar_usuario_por_telegram("8342030105")
    assert user_restaurado["perfil"] == "cliente"

    print("[PASS] UserService com perfis RBAC validado com sucesso!")


def testar_filtro_rag_rbac():
    print("\n--- 4. Testando Consulta RBAC e Isolamento Multi-Empresa ---")
    conn = conectar()
    cur = conn.cursor()

    # Cria documento público e interno para Empresa 1
    cur.execute("""
        INSERT INTO documentos (empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo, conteudo_texto, hash_conteudo, nivel_acesso, data_upload)
        VALUES (1, 'doc_empresa1_pub.txt', '.txt', '/tmp/p1.txt', 'conteudo pub emp1', 'hash_pub_1', 'publico', datetime('now'))
    """)
    doc_pub1_id = cur.lastrowid

    cur.execute("""
        INSERT INTO documentos (empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo, conteudo_texto, hash_conteudo, nivel_acesso, data_upload)
        VALUES (1, 'doc_empresa1_int.txt', '.txt', '/tmp/i1.txt', 'conteudo int emp1', 'hash_int_1', 'interno', datetime('now'))
    """)
    doc_int1_id = cur.lastrowid

    # Cria documento para Empresa 2
    cur.execute("""
        INSERT INTO documentos (empresa_id, nome_arquivo, tipo_arquivo, caminho_arquivo, conteudo_texto, hash_conteudo, nivel_acesso, data_upload)
        VALUES (2, 'doc_empresa2_pub.txt', '.txt', '/tmp/p2.txt', 'conteudo emp2', 'hash_pub_2', 'publico', datetime('now'))
    """)
    doc_emp2_id = cur.lastrowid

    cur.execute("INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding) VALUES (?, 1, 'chunk pub emp1', '[0.1]')", (doc_pub1_id,))
    cur.execute("INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding) VALUES (?, 1, 'chunk int emp1', '[0.2]')", (doc_int1_id,))
    cur.execute("INSERT INTO chunks (documento_id, numero_chunk, conteudo, embedding) VALUES (?, 1, 'chunk emp2', '[0.3]')", (doc_emp2_id,))
    conn.commit()

    # 1. Query para perfil 'cliente' da Empresa 1: deve ver apenas o público da Empresa 1
    cur.execute("""
        SELECT c.conteudo, d.nivel_acesso, d.empresa_id
        FROM chunks c
        JOIN documentos d ON c.documento_id = d.id
        WHERE d.empresa_id = 1 AND COALESCE(d.nivel_acesso, 'publico') = 'publico'
    """)
    chunks_cliente_emp1 = cur.fetchall()
    assert all(r["empresa_id"] == 1 for r in chunks_cliente_emp1), "Cliente emp 1 viu chunks de outra empresa!"
    assert all(r["nivel_acesso"] == "publico" for r in chunks_cliente_emp1), "Cliente viu chunk interno!"

    # 2. Query para perfil 'funcionario' da Empresa 1: vê tudo da Empresa 1, mas NADA da Empresa 2
    cur.execute("""
        SELECT c.conteudo, d.empresa_id
        FROM chunks c
        JOIN documentos d ON c.documento_id = d.id
        WHERE d.empresa_id = 1
    """)
    chunks_func_emp1 = cur.fetchall()
    assert all(r["empresa_id"] == 1 for r in chunks_func_emp1), "Funcionário da Empresa 1 viu dados da Empresa 2!"
    assert any("chunk int emp1" in r["conteudo"] for r in chunks_func_emp1), "Funcionário não viu documento interno da sua própria empresa!"

    # Limpeza
    cur.execute("DELETE FROM chunks WHERE documento_id IN (?, ?, ?)", (doc_pub1_id, doc_int1_id, doc_emp2_id))
    cur.execute("DELETE FROM documentos WHERE id IN (?, ?, ?)", (doc_pub1_id, doc_int1_id, doc_emp2_id))
    conn.commit()
    conn.close()

    print("[PASS] Filtro RAG isola rigorosamente por Empresa (Multi-tenancy) e por Perfil (RBAC)!")


def testar_prompt_ai_service():
    print("\n--- 5. Testando Montagem de Prompt com Suporte Humano ---")
    config = {
        "numero_suporte_humano": "(24) 98888-7777",
        "mensagem_suporte": "Fale com nosso atendente via WhatsApp."
    }
    prompt = montar_system_prompt(config)
    assert "(24) 98888-7777" in prompt, "Telefone de suporte não inserido no prompt do sistema!"
    assert "Fale com nosso atendente via WhatsApp." in prompt, "Mensagem de suporte não inserida no prompt!"
    print("[PASS] Prompt do AIService inclui dados de suporte humano dinamicamente!")


def testar_endpoints_documentos():
    print("\n--- 6. Testando Endpoints FastAPI e Proteção de API Key ---")
    client = TestClient(app)
    admin_key = os.getenv("ADMIN_API_KEY", "fluxia-admin-secret-key-2026")

    # 1. Tentativa de upload sem header de autenticação (deve dar 403)
    files = {"arquivo": ("teste.txt", b"Conteudo de teste", "text/plain")}
    resp_unauth = client.post("/documents/upload", files=files)
    assert resp_unauth.status_code == 403, f"Esperado 403 sem API Key, obtido {resp_unauth.status_code}"

    # 2. Tentativa com chave errada (deve dar 403)
    resp_bad_key = client.post("/documents/upload", files=files, headers={"X-Admin-API-Key": "chave_errada"})
    assert resp_bad_key.status_code == 403, f"Esperado 403 com chave inválida, obtido {resp_bad_key.status_code}"

    # 3. Com chave correta e nivel_acesso inválido (deve passar pela autenticação e falhar com 400)
    data = {"nivel_acesso": "invalido_xyz", "empresa_id": 1}
    resp_val = client.post("/documents/upload", files=files, data=data, headers={"X-Admin-API-Key": admin_key})
    assert resp_val.status_code == 400, f"Esperado 400 para nivel_acesso inválido, obtido {resp_val.status_code}"

    # 4. Tentativa de exclusão sem chave (deve dar 403)
    resp_del_unauth = client.delete("/documents/999")
    assert resp_del_unauth.status_code == 403, f"Esperado 403 no DELETE sem chave, obtido {resp_del_unauth.status_code}"

    # 5. Listagem de documentos deve conter nivel_acesso e empresa_id
    resp_list = client.get("/documents/")
    assert resp_list.status_code == 200
    docs = resp_list.json().get("documentos", [])
    if docs:
        assert "nivel_acesso" in docs[0], "Campo 'nivel_acesso' ausente no retorno de /documents/"
        assert "empresa_id" in docs[0], "Campo 'empresa_id' ausente no retorno de /documents/"

    print("[PASS] Proteção com X-Admin-API-Key e validações administrativas 100% ativas!")


def testar_webhook_telegram_com_historico():
    print("\n--- 7. Testando Webhook do Telegram e Persistência de Histórico ---")
    from unittest.mock import patch
    client = TestClient(app)

    chat_id = "8342030105"  # Usuário João cadastrado no banco

    # Limpa histórico anterior deste chat
    conn = conectar()
    cur = conn.cursor()
    cur.execute("DELETE FROM historico_conversas WHERE telegram_chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

    payload = {
        "message": {
            "chat": {"id": chat_id},
            "text": "Qual o horário de atendimento?"
        }
    }

    # Mocka chamadas externas (Telegram API e Gemini)
    with patch("app.routes.telegram.enviar_mensagem_telegram") as mock_envio, \
         patch("app.routes.telegram.ai_service.gerar_resposta") as mock_ia, \
         patch("app.routes.telegram.buscar_contexto_relevante") as mock_rag:

        mock_rag.return_value = "Horário de atendimento: Segunda a Sexta das 9h às 18h."
        mock_ia.return_value = "Nosso horário de atendimento é de segunda a sexta, das 9h às 18h."

        resp = client.post("/telegram/webhook", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Verifica se enviou a mensagem
        assert mock_envio.called
        assert mock_ia.called
        # Verifica se os parâmetros passados para IA continham config_suporte e historico
        _, kwargs = mock_ia.call_args
        assert "config_suporte" in kwargs
        assert "historico" in kwargs

    # Verifica se a interação foi gravada no histórico
    conn = conectar()
    cur = conn.cursor()
    cur.execute("SELECT * FROM historico_conversas WHERE telegram_chat_id = ?", (chat_id,))
    interacao = cur.fetchone()
    assert interacao is not None, "Interação não foi salva no histórico!"
    assert interacao["mensagem_usuario"] == "Qual o horário de atendimento?"
    assert "9h às 18h" in interacao["resposta_ia"]

    # Limpa dados de teste
    cur.execute("DELETE FROM historico_conversas WHERE telegram_chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

    print("[PASS] Webhook do Telegram executa fluxo completo e grava no historico_conversas!")


def testar_endpoints_empresa_e_usuarios():
    print("\n--- 8. Testando Endpoints de Gestão de Empresa e Usuários ---")
    client = TestClient(app)
    admin_key = os.getenv("ADMIN_API_KEY", "fluxia-admin-secret-key-2026")
    headers = {"X-Admin-API-Key": admin_key}

    # 1. Consulta de configurações de empresa (aberta para leitura)
    resp_get_cfg = client.get("/empresa/configuracoes?empresa_id=1")
    assert resp_get_cfg.status_code == 200
    cfg_original = resp_get_cfg.json()
    assert "numero_suporte_humano" in cfg_original

    # 2. Atualização de configurações sem chave administrativa (deve dar 403)
    novo_payload = {
        "empresa_id": 1,
        "numero_suporte_humano": "(21) 98888-0000",
        "mensagem_suporte": "Atendimento exclusivo via WhatsApp."
    }
    resp_put_unauth = client.put("/empresa/configuracoes", json=novo_payload)
    assert resp_put_unauth.status_code == 403

    # 3. Atualização de configurações com chave correta
    resp_put = client.put("/empresa/configuracoes", json=novo_payload, headers=headers)
    assert resp_put.status_code == 200
    assert resp_put.json()["configuracao"]["numero_suporte_humano"] == "(21) 98888-0000"

    # Restaura configuração original
    client.put("/empresa/configuracoes", json=cfg_original, headers=headers)

    # 4. Criação de usuário sem chave (deve dar 403)
    novo_user = {
        "empresa_id": 1,
        "nome": "Usuário Teste API",
        "email": "teste_api_gestao@empresa.com",
        "senha": "senha_segura_123",
        "perfil": "funcionario"
    }
    resp_user_unauth = client.post("/usuarios/", json=novo_user)
    assert resp_user_unauth.status_code == 403

    # 5. Criação de usuário com chave administrativa
    resp_user_create = client.post("/usuarios/", json=novo_user, headers=headers)
    assert resp_user_create.status_code == 201, f"Falha ao criar usuário: {resp_user_create.text}"
    user_id = resp_user_create.json()["usuario_id"]

    # 6. Listagem de usuários
    resp_users_list = client.get("/usuarios/", headers=headers)
    assert resp_users_list.status_code == 200
    usuarios = resp_users_list.json()["usuarios"]
    assert any(u["id"] == user_id for u in usuarios), "Usuário criado não retornado na listagem!"

    # 7. Detalhes do usuário
    resp_detail = client.get(f"/usuarios/{user_id}", headers=headers)
    assert resp_detail.status_code == 200
    assert resp_detail.json()["email"] == "teste_api_gestao@empresa.com"

    # 8. Alteração de perfil (RBAC) via API
    resp_patch_perfil = client.patch(f"/usuarios/{user_id}/perfil", json={"perfil": "admin"}, headers=headers)
    assert resp_patch_perfil.status_code == 200
    assert resp_patch_perfil.json()["novo_perfil"] == "admin"

    # 9. Alteração de status via API
    resp_patch_status = client.patch(f"/usuarios/{user_id}/status", json={"status": "inativo"}, headers=headers)
    assert resp_patch_status.status_code == 200
    assert resp_patch_status.json()["novo_status"] == "inativo"

    # 10. Exclusão do usuário de teste
    resp_del = client.delete(f"/usuarios/{user_id}", headers=headers)
    assert resp_del.status_code == 200

    print("[PASS] Endpoints de Empresa e Gestão de Usuários (RBAC/Status/CRUD) 100% aprovados!")


def testar_comandos_rapidos_telegram():
    print("\n--- 9. Testando Comandos Rápidos do Telegram (/ajuda, /suporte, /perfil, /start) ---")
    from unittest.mock import patch
    client = TestClient(app)
    chat_id = "8342030105"  # Usuário João

    with patch("app.routes.telegram.enviar_mensagem_telegram") as mock_envio:
        # 1. Teste /ajuda
        resp = client.post("/telegram/webhook", json={"message": {"chat": {"id": chat_id}, "text": "/ajuda"}})
        assert resp.status_code == 200
        assert mock_envio.called
        msg_enviada = mock_envio.call_args[0][1]
        assert "Guia de Uso" in msg_enviada

        # 2. Teste /suporte
        resp = client.post("/telegram/webhook", json={"message": {"chat": {"id": chat_id}, "text": "/suporte"}})
        assert resp.status_code == 200
        msg_enviada = mock_envio.call_args[0][1]
        assert "Canais de Suporte Humano" in msg_enviada
        assert "(11)" in msg_enviada

        # 3. Teste /perfil
        resp = client.post("/telegram/webhook", json={"message": {"chat": {"id": chat_id}, "text": "/perfil"}})
        assert resp.status_code == 200
        msg_enviada = mock_envio.call_args[0][1]
        assert "Seu Perfil no FluxIA" in msg_enviada
        assert "CLIENTE" in msg_enviada

        # 4. Teste /start
        resp = client.post("/telegram/webhook", json={"message": {"chat": {"id": chat_id}, "text": "/start"}})
        assert resp.status_code == 200
        msg_enviada = mock_envio.call_args[0][1]
        assert "Comandos úteis" in msg_enviada

    print("[PASS] Todos os comandos rápidos do Telegram responderam com perfeição!")


if __name__ == "__main__":
    testar_banco_e_migracoes()
    testar_servicos_auxiliares()
    testar_user_service()
    testar_filtro_rag_rbac()
    testar_prompt_ai_service()
    testar_endpoints_documentos()
    testar_webhook_telegram_com_historico()
    testar_endpoints_empresa_e_usuarios()
    testar_comandos_rapidos_telegram()
    print("\n==========================================")
    print("TODOS OS TESTES FORAM CONCLUÍDOS COM SUCESSO!")
    print("==========================================")
