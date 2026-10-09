---
name: fluxia-rag-engine
description: Motor RAG e IA do FluxIA: upload e indexação de documentos (PDF/DOCX/TXT), chunking, embeddings, busca semântica por cosseno, acesso público vs interno, chat interno (copiloto), multi-vendor Gemini/OpenAI com BYOK, telemetria de LLM (tokens, latência, custo) e avaliação contínua (RAG Triad, LLM-as-a-Judge). Use SEMPRE que a tarefa envolver documentos, base de conhecimento, embeddings, chat IA, chaves de API, observabilidade, analytics de perguntas ou alucinação.
---

# FluxIA RAG Engine

## Arquivos
- `services/document_service.py` (extrai texto de PDF/DOCX/TXT)
- `services/chunk_service.py` (chunks ~500-800 caracteres com sobreposição)
- `services/embedding_service.py` (Google GenAI `text-embedding-004`)
- `services/semantic_search_service.py` (cosseno em memória, limiar > 0.25)
- `services/ai_service.py` (cliente Gemini, system prompts, fallback)
- `services/ai_engine_service.py` (LangChain multi-vendor + callbacks)
- `services/ai_observability_service.py`, `services/ai_evaluation_service.py`
- `routes/documents.py`, `routes/chat.py`, `routes/analytics.py`

## Indexação
1. Upload (somente admin) -> extrai texto -> calcula `hash_conteudo` (evita duplicata) -> grava em `documentos` com `nivel_acesso` (`publico`|`interno`).
2. Divide em chunks -> embedding por chunk -> grava JSON em `chunks.embedding` (TEXT).
3. DELETE /documents/{id} remove documento e chunks.
- Nível de acesso pode ser alterado depois do upload.
- Documentos automáticos (propostas/contratos) usam `origem`/`ref_tipo`/`ref_id` e são `interno`.

## Busca e RBAC de documentos (regra de segurança)
- RAG PÚBLICO (bot do Telegram): SQL sempre com
  `WHERE d.empresa_id = %s AND COALESCE(d.nivel_acesso,'publico') = 'publico'`.
  NUNCA expor documento interno a cliente externo.
- Chat interno (portal): usa públicos + internos da empresa. Sem resposta na base, responde com conhecimento geral e adiciona nota de transparência (não rejeita a pergunta).
- Consulta: embedding da pergunta -> cosseno com chunks do tenant -> top-k acima do limiar.

## Multi-vendor e BYOK
- Provedores: Gemini (3.5 Flash Lite, 3.6 Flash) e OpenAI (GPT-4o-mini).
- Empresa pode cadastrar chave própria (`gemini_api_key`, `openai_api_key`, `provedor_ia_padrao`); sem chave, usa o provedor padrão da plataforma com failover automático.
- Chaves sempre mascaradas ao retornar para o frontend.
- A UI do chat mostra o modelo de IA usado na resposta.

## Chat interno (`POST /chat/`)
- RAG híbrido. Resposta em markdown (frontend deve renderizar, sem asteriscos crus).
- Últimas 3 conversas por usuário aparecem na lateral.
- Registra em `perguntas_historico` (pergunta, resposta, documentos_utilizados, teve_contexto, fonte_resposta).

## Observabilidade e avaliação
- `AIObservabilityCallbackHandler` grava em `ia_telemetria_execucao`: vendor, modelo, tokens (prompt/completion/total), custo_estimado_usd, latencia_ms, status_execucao.
- `avaliar_interacao_ia` roda em `BackgroundTasks` (não bloqueia a resposta) e grava em `ia_evaluations`: score_fidelidade, score_relevancia_resposta, score_relevancia_contexto, possivel_alucinacao.
- Toda nova chamada de LLM deve passar pelo engine para herdar telemetria; não chame o SDK direto.

## Dicas de custo/performance
- Cosseno é em memória: filtre por `empresa_id` (e nível de acesso) NO SQL antes de carregar embeddings.
- Reaproveite embeddings pelo `hash_conteudo`; não reindexe documento idêntico.
