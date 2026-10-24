# FluxIA — Especificação Completa de Arquitetura de Software e Engenharia de IA

> **Classificação:** Documento Técnico e Arquitetural Definitivo  
> **Público-alvo:** Sócios, Diretores, Engenheiros de Software, Arquitetos de Solução e Gestores de Produto  
> **Versão:** 2.0.0 — Documento de Engenharia e Especificação para Diagramação  
> **Data de Atualização:** Outubro de 2026  

---

## Sumário Executivo

O **FluxIA** é uma plataforma SaaS B2B *multitenant* desenvolvida para unificar **Atendimento Omnichannel Automatizado via Telegram**, **CRM Comercial Completo com Funil de Vendas** e um **Motor de Inteligência Artificial Especializado com RAG**.

Diferente de soluções convencionais do mercado que se limitam a conectar uma interface web a um prompt genérico do ChatGPT, o FluxIA foi concebido para resolver os gargalos reais de operações corporativas:
1. **Segurança de dados e isolamento total entre empresas (*Zero Data Leakage*)**: Garantia de que nenhum cliente ou colaborador veja informações de outro *tenant*.
2. **Zero alucinação em preços, catálogos e regras oficiais**: A IA consulta a base documental oficial antes de emitir qualquer resposta ao cliente.
3. **Controle financeiro em tempo real sobre os custos de IA (*FinOps*)**: Medição por chamada de tokens, latência e custo em dólares (USD) com até 7 casas decimais.
4. **Governança com Auditoria Contínua (*LLM-as-a-Judge*)**: Um segundo modelo de IA audita em segundo plano a fidelidade de cada resposta gerada contra o catálogo oficial da empresa.
5. **Transbordo fluido com salvaguardas de ociosidade**: Passagem transparente entre atendimento automatizado (bot) e operadores humanos, com timeout automático de 30 minutos em caso de inatividade.

---

## 1. Topologia Geral e Decisões de Engenharia

```
                               ┌──────────────────────────────────────────────────────────┐
                               │                    CANAL DE ENTRADA                      │
                               │  • Telegram Webhook (/telegram/webhook/{empresa_id})     │
                               │  • Portal Web Interno (/chat/ e Dashboard CRM)           │
                               └────────────────────────────┬─────────────────────────────┘
                                                            │
                                                            ▼
                               ┌──────────────────────────────────────────────────────────┐
                               │                 GATEWAY FASTAPI & RBAC                   │
                               │  • Validação Multitenant estrita (empresa_id)            │
                               │  • Checagem de Perfis: Master, Admin, Colaborador        │
                               │  • Trava de Adição de Conhecimento (Admin/Master)        │
                               └────────────────────────────┬─────────────────────────────┘
                                                            │
                                                            ▼
                               ┌──────────────────────────────────────────────────────────┐
                               │             ORQUESTRADOR CENTRAL (Agente Gerente)        │
                               │  • Extrator Regex/LLM de Nome, Telefone e Email          │
                               │  • Classificador Semântico em 10 Intenções (Gemini)      │
                               │  • Máquina de Estados: Modo Bot vs Modo Humano           │
                               │  • Dossiê Comercial Sigiloso (sem expor IDs de CRM)      │
                               └─────────────┬──────────────────────────────┬─────────────┘
                                             │                              │
                                             ▼                              ▼
               ┌───────────────────────────────────────────┐  ┌───────────────────────────┐
               │         MOTOR RAG E BUSCA SEMÂNTICA       │  │    SERVIÇOS DE NEGÓCIO    │
               │  • Deduplicação SHA-256 prévia            │  │  • ReportLab (PDF Proposta│
               │  • Chunking Deslizante (1000c / 200c)     │  │  • TicketService Priorit. │
               │  • Embeddings gemini-embedding-001 (768d) │  │  • Síntese IA para Equipe│
               │  • Cosseno com Expansão Vizinhança (N±1)  │  │  • Funil Kanban e Contratos│
               └─────────────────────┬─────────────────────┘  └─────────────┬─────────────┘
                                     │                                      │
                                     └──────────────────────┬───────────────┘
                                                            │
                                                            ▼
                               ┌──────────────────────────────────────────────────────────┐
                               │         MOTOR MULTI-VENDOR COM FAILOVER (LangChain)      │
                               │  • BYOK (Bring Your Own Key) por Empresa                 │
                               │  • Primário: Gemini 3.5 Flash Lite / Secundário: GPT-4o-mini│
                               │  • Telemetria Integrada (AIObservabilityCallbackHandler) │
                               └────────────────────────────┬─────────────────────────────┘
                                                            │
                                                            ▼
                               ┌──────────────────────────────────────────────────────────┐
                               │           PERSISTÊNCIA RELACIONAL (PostgreSQL)           │
                               │  • Tabelas de Negócio: clientes, negocios, propostas     │
                               │  • Tabelas de IA: documentos, chunks, ia_telemetria      │
                               │  • Auditoria Desacoplada: ia_evaluations (RAG Triad)     │
                               └──────────────────────────────────────────────────────────┘
```

### 1.1. Front-end: Zero-Build com JavaScript Vanilla
- **Tecnologias:** HTML5 Semântico, CSS3 com variáveis nativas (*Design Tokens*) e JavaScript puro (ES6+ Vanilla).
- **Justificativa Estratégica:** A escolha de **não utilizar frameworks pesados (React, Vue, Angular)** elimina custos de build (Node.js/Webpack em produção), torna o portal imune a quebras de dependências do ecossistema npm e garante renderização instantânea (carregamento em menos de 100ms).
- **Ergonomia Operacional:** Layout em 3 colunas para atendimento no padrão WhatsApp Web / Telegram Desktop (Lista de conversas, Janela ativa com histórico e Ficha completa do Lead no CRM).

### 1.2. Back-end: FastAPI Assíncrono com APScheduler
- **Tecnologias:** Python 3.12, FastAPI, Uvicorn, psycopg2-binary, Pydantic v2, APScheduler, ReportLab.
- **Justificativa Estratégica:** O FastAPI processa requisições de forma não bloqueante (assíncrona), suportando dezenas de mensagens simultâneas do Telegram sem gargalos. O agendador **APScheduler** roda em segundo plano na mesma instância, executando:
  - *Follow-up automático de leads inativos* a cada 1 hora.
  - *Timeout de ociosidade de atendentes humanos* a cada 5 minutos.
  - Isso dispensa a sobrecarga de gerenciar serviços de fila externos (como Redis ou Celery) na fase atual.

### 1.3. Banco de Dados: PostgreSQL Multitenant
- **Tecnologia:** PostgreSQL (Neon Serverless / Render Managed Postgres).
- **Justificativa Estratégica:** Todas as tabelas de negócio possuem a coluna `empresa_id`. Nenhuma query é executada sem o filtro de tenant. A inicialização da aplicação executa `criar_banco()`, com migrações idempotentes (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), garantindo que novas colunas e tabelas sejam criadas automaticamente no deploy sem passos manuais.

---

## 2. Pipeline de Ingestão e Processamento Documental (ETL de Conhecimento)

Arquivo de referência: [`document_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/document_service.py), [`chunk_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/chunk_service.py), [`documents.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/routes/documents.py).

O processo de ingestão transforma arquivos não estruturados em vetores semânticos com rigoroso isolamento e proteção contra duplicações.

```
[Upload de Arquivo / Documento Sintético]
                    │
                    ▼
       1. Leitura dos Bytes Brutos
                    │
                    ▼
       2. CÁLCULO DO HASH SHA-256
          hash_conteudo = hashlib.sha256(bytes).hexdigest()
                    │
                    ▼
       3. FILTRO PRÉVIO DE DUPLICADOS (SQL)
          WHERE (nome_arquivo = %s OR hash_conteudo = %s) AND empresa_id = %s
          ├── Se já existe com mesmo nome  ──> Erro 409: Arquivo já enviado anteriormente
          └── Se já existe com outro nome  ──> Erro 409: Conteúdo idêntico já cadastrado
                    │ (Passou no filtro)
                    ▼
       4. Extração Textual por Tipo de Arquivo
          ├── .txt, .pdf (pypdf), .docx (python-docx), .pptx (python-pptx)
          └── .png, .jpg, .jpeg ──> Gemini Multimodal Vision (OCR + Descrição Visual)
                    │
                    ▼
       5. Chunking com Janela Deslizante
          ├── Tamanho: ~1000 caracteres
          ├── Sobreposição (Overlap): 200 caracteres
          └── Preservação de limites de palavras (busca pelo último espaço)
                    │
                    ▼
       6. Vetorização (Embeddings)
          └── Google models/gemini-embedding-001 (768 dimensões)
          └── Retries com backoff progressivo em caso de cota 429 (15s, 30s, 45s...)
                    │
                    ▼
       7. Gravação Relacional e Vetorial
          ├── Tabela 'documentos': metadados, hash_conteudo, nivel_acesso ('publico' | 'interno')
          └── Tabela 'chunks': numero_chunk, conteudo (TEXT), embedding (JSON com 768 floats)
```

### 2.1. O Algoritmo de Hash SHA-256 em Detalhes
O cálculo do hash SHA-256 no momento do upload cumpre quatro funções vitais de arquitetura:
1. **Prevenção de Custos de API (*FinOps*)**: O modelo de embeddings é pago e possui limites de requisições por minuto (RPM). Se o usuário fizer upload do mesmo arquivo três vezes, o hash bloqueia a operação antes de enviar qualquer requisição para a Google.
2. **Detecção de Renomeação**: Mesmo que o arquivo seja renomeado (ex: de `catalogo.pdf` para `catalogo_final_v2.pdf`), o SHA-256 detecta que os bytes internos são 100% idênticos e impede a duplicata.
3. **Anti-Diluição do Contexto RAG**: Se o mesmo arquivo for indexado múltiplas vezes, a busca vetorial retornará três cópias do mesmo chunk no Top-3. Isso consome a janela de contexto da IA e impede que ela leia outras informações relevantes.
4. **Isolamento Multitenant no Hash**: A checagem do hash inclui `AND empresa_id = %s`. Duas empresas distintas podem subir o mesmo arquivo público sem conflito.

### 2.2. Leitura Multimodal de Imagens com OCR via Gemini Vision
Quando uma imagem (`.png`, `.jpg`, `.jpeg`) é submetida, o FluxIA envia os bytes para o modelo multimodal `gemini-3.5-flash-lite` com uma instrução especializada:
- Transcreve todo o texto visível com precisão (tabelas, cartazes, infográficos, notas fiscais).
- Produz uma descrição detalhada em Português do Brasil dos elementos visuais.
- O texto resultante é então fatiado em chunks e vetorizado, permitindo que a IA encontre informações contidas em fotos de catálogos ou recibos.

### 2.3. Controle de Acesso (RBAC) na Ingestão
- **Upload Formal (`POST /documents/upload`)**: Exige `usuario_id` e invoca `validar_perfil_admin_ou_master(usuario_id)`. Colaboradores e clientes recebem HTTP 403 Forbidden.
- **Aprendizado Dinâmico via Chat (`POST /chat/`)**: Quando uma mensagem contém a intenção imperativa *"Cadastre na base que..."*, o sistema consulta o perfil do usuário no banco. Se não for `admin` ou `master`, a IA emite uma recusa educada informando que apenas administradores possuem privilégio para alterar o conhecimento corporativo.

---

## 3. Mecanismo de Busca Semântica e Contextualização (RAG Retrieval)

Arquivo de referência: [`semantic_search_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/semantic_search_service.py), [`telegram.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/routes/telegram.py).

O FluxIA utiliza uma arquitetura híbrida relacional-vetorial executada no PostgreSQL:

### 3.1. Pré-Filtragem SQL por Tenant e Nível de Acesso
A query SQL nunca busca vetores globalmente. Ela aplica filtros rígidos antes de qualquer cálculo matemático:
- **No Bot do Telegram (Canal Externo)**:
  ```sql
  WHERE d.empresa_id = %s AND COALESCE(d.nivel_acesso, 'publico') = 'publico'
  ```
  *Garantia absoluta de que propostas internas, salários ou contratos confidenciais jamais vazem para leads externos.*
- **No Portal Web (Canal Interno)**:
  ```sql
  WHERE d.empresa_id = %s
  ```
  *Permite ao colaborador consultar documentos públicos e internos da respectiva empresa.*

### 3.2. Similaridade de Cosseno em Memória
A similaridade entre o vetor da dúvida do cliente ($\vec{u}$) e o vetor do chunk armazenado ($\vec{v}$) é calculada por:
$$\text{Similaridade}(\vec{u}, \vec{v}) = \frac{\sum_{i=1}^{n} u_i \cdot v_i}{\sqrt{\sum_{i=1}^{n} u_i^2} \cdot \sqrt{\sum_{i=1}^{n} v_i^2}}$$

- **Limiar no Portal**: `LIMIAR_SIMILARIDADE = 0.70` (elimina ruídos e trechos pouco relacionados).
- **Limiar no Telegram**: `LIMIAR_SIMILARIDADE = 0.25` (maior tolerância para linguagem informal e gírias).
- **Top-K**: Seleção padrão dos 3 chunks de maior pontuação.

### 3.3. Técnica de Expansão de Contexto por Vizinhança (*Context Window Expansion*)
Um dos problemas clássicos do RAG é quando uma informação importante fica dividida entre a fronteira de dois chunks. Para resolver isso:
1. O sistema localiza o chunk com a maior similaridade semântica (`melhor_resultado`, ex: `numero_chunk = 5`).
2. Realiza uma consulta no banco buscando os vizinhos imediatos do mesmo documento: `numero_chunk IN (4, 6)`.
3. Reordena os chunks na ordem original de leitura (`numero_chunk ASC` $\to 4, 5, 6$).
4. Isso reconstitui o parágrafo inteiro ao redor da resposta, entregando para o LLM um contexto fluido e coeso.

---

## 4. Arquitetura do Agente Gerente Central (`AgentOrchestrator`)

Arquivo de referência: [`agent_orchestrator.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/agent_orchestrator.py).

O Agente Gerente opera como uma máquina de estados finitos que intercepta e governa cada mensagem recebida pelo webhook do Telegram.

### 4.1. Máquina de Estados: Modo Bot vs. Modo Humano
- **Estado `humano`**: Se um operador humano assumiu a conversa no CRM (`conversas_telegram.status == 'humano'`), a IA entra em modo silencioso. A mensagem do cliente é registrada diretamente no histórico e vinculada ao Ticket ativo, sem disparar geração de resposta por IA.
- **Timeout de Ociosidade (30 Minutos)**: Se a conversa estiver em modo `humano`, mas nem o atendente nem o cliente interagirem por 30 minutos, o orquestrador reverte automaticamente o status para `bot`. Isso impede que leads esfriem caso um atendente esqueça de devolver a conversa.

### 4.2. Extração Estruturada de Contato e Entidades
Antes de classificar a intenção, o orquestrador executa a rotina `extrair_dados_contato()`:
- **Telefone**: Regex otimizada para o padrão brasileiro celular e fixo (`(XX) XXXXX-XXXX` ou `XX9XXXXXXXX`), validando se possui no mínimo 8 dígitos úteis.
- **Nome**: Validação via `is_nome_generico()`. Nomes como *"Usuário #1234"*, *"Lead"*, *"Oi"*, *"Bom dia"* são descartados. Se o cliente enviar *"Sou o Gabriel e meu whats é 11988887777"*, o nome "Gabriel" é isolado e formatado.
- **Fallback com LLM**: Se houver menção de contato mas a regex não capturar ambos com clareza, uma chamada leve em JSON extrai `{"nome": ..., "telefone": ..., "email": ...}`.
- **Tratamento de Perguntas com Dados**: Se o cliente disser *"Meu nome é Lucas, whats 11977776666, quanto custa o serviço?"*, o sistema grava o contato no banco imediatamente e responde à dúvida de preço no mesmo turno.

### 4.3. Classificador Semântico em Dois Níveis
1. **Nível 1 (Principal — LLM Gemini 3.5 Flash Lite)**: A mensagem é avaliada em JSON estruturado com base nas seguintes categorias:
   - `BLOQUEIO_PRIVACIDADE`: Tentativas de obter dados de terceiros ou de outros clientes (LGPD).
   - `COLETA_CONTATO`: Demanda comercial ou atendimento onde falta nome ou WhatsApp válido e a coleta é obrigatória.
   - `CADASTRO_CONCLUIDO`: O cliente forneceu os dados de contato faltantes nesta mensagem.
   - `SAUDACAO`: Cumprimento cordial sem dúvida substantiva.
   - `DESPEDIDA`: Encerramento do diálogo.
   - `ENVIO_PDF_PROPOSTA`: Solicitação expressa de download do documento em PDF da proposta.
   - `CONSULTA_STATUS_TICKET`: Cobrança ou pergunta sobre chamado já aberto.
   - `TRANSBORDO_HUMANO`: Pedido explícito para falar com uma pessoa/suporte.
   - `PROPOSTA_COMERCIAL`: Pedido de novo orçamento formal ou negociação de desconto/contrato.
   - `CONSULTA_COMERCIAL`: Dúvidas em primeira pessoa sobre propostas ou contratos que o cliente já possui.
   - `PRODUTOS_CATALOGO` / `PERGUNTA_CONHECIMENTO`: Perguntas sobre catálogo, produtos, regras ou funcionamento.
   - `FORA_DO_ESCOPO`: Assuntos desconexos da empresa.
2. **Nível 2 (Fallback Heurístico por Regras/Regex)**: Caso a API do LLM sofra oscilação de rede ou timeout, o orquestrador possui um motor heurístico com mais de 40 expressões regulares pré-compiladas que garantem que o cliente nunca fique sem resposta.

### 4.4. Ações e Subagentes Especializados
- **Subagente de Envio de PDF (`ENVIO_PDF_PROPOSTA`)**:
  - Identifica a proposta do cliente (pelo produto citado ou a mais recente).
  - Invoca [`proposal_pdf_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/proposal_pdf_service.py), gerando o PDF em memória via ReportLab.
  - Envia o arquivo diretamente no Telegram do cliente através do método `sendDocument` da API oficial.
- **Subagente de Dossiê Comercial Sigiloso (`CONSULTA_COMERCIAL`)**:
  - Compila as propostas, contratos e itens em aberto do cliente em um dossiê textual estruturado.
  - **Diretriz de Sigilo no Prompt**: O modelo é instruído a responder em 1ª pessoa (*"Você possui conosco a proposta do Produto X no valor de R$ Y..."*), sendo **estritamente proibido** de citar IDs de banco de dados (`#18`), estágios internos do funil (*"Pipeline"*, *"Lead Qualificado"* ou *"Valor Estimado"*).
- **Subagente de Chamados e Síntese Executiva (`ticket_service.py`)**:
  - Ao transferir para transbordo humano ou solicitação de proposta, cria um chamado prioritário na tabela `tickets`.
  - Recupera até as últimas 8 mensagens da conversa e gera um resumo executivo com marcadores:
    - *Cliente (Nome e WhatsApp)*
    - *Interesse Comercial*
    - *Demanda Específica do Ticket*
    - *Histórico Resumido em 2 frases*
    - *Ação Recomendada para o Vendedor*
  - O operador humano lê o chamado em menos de 10 segundos e já responde diretamente pelo Telegram através do portal.

---

## 5. Motor Multi-Vendor e Roteamento de LLM (`MultiVendorAIEngine`)

Arquivo de referência: [`ai_engine_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/ai_engine_service.py).

O motor de inteligência artificial é construído sobre o **LangChain**, implementando redundância automática e compatibilidade com o modelo BYOK (*Bring Your Own Key*):

### 5.1. Matriz de Provedores e Fallback Automático
- **Provedor Padrão (Google)**: Primário em `gemini-3.5-flash-lite`. Se houver indisponibilidade ou falha de cota, o método `with_fallbacks()` comuta transparentemente a execução para o `gpt-4o-mini` (OpenAI).
- **Provedor Alternativo (OpenAI)**: Se a empresa definir a OpenAI como padrão, o `gpt-4o-mini` opera como primário, com failover automático para o `gemini-3.5-flash-lite`.
- **Failover Crítico de Emergência**: Se ambos os provedores apresentarem instabilidade simultânea, o sistema captura a exceção e devolve uma mensagem calorosa avisando que a equipe humana responderá pelo chat em instantes, garantindo que o cliente nunca veja telas de erro técnicas.

### 5.2. Ferramentas Integradas do CRM (LangChain Tools)
O motor disponibiliza ferramentas formais com tipagem estrita via `@tool`:
1. `buscar_base_conhecimento(pergunta, empresa_id)`: Executa RAG semântico com isolamento de tenant.
2. `consultar_status_cliente_crm(telegram_chat_id)`: Consulta o estágio atual do cliente, produtos e valores no funil.
3. `transferir_atendimento_humano(telegram_chat_id, motivo)`: Atualiza a conversa para modo `humano` via banco.

---

## 6. Observabilidade, FinOps e Métricas de Execução

Arquivo de referência: [`ai_observability_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/ai_observability_service.py).

O `AIObservabilityCallbackHandler` intercepta cada etapa da invocação do modelo e persiste na tabela `ia_telemetria_execucao`:

### 6.1. Atributos Rastreados por Requisição
- `empresa_id`, `canal` (`telegram` ou `portal`), `session_id`.
- `vendor` (`google` ou `openai`) e `modelo` utilizado.
- `tokens_prompt`, `tokens_completion`, `tokens_total` (capturados do `usage_metadata` do LangChain com fallback por contagem de caracteres $\approx \text{chars} / 4$).
- `latencia_ms`: tempo total de ida e volta da requisição em milissegundos.
- `status_execucao` (`sucesso` ou `erro`).
- `custo_estimado_usd`: custo financeiro exato calculado em tempo real.

### 6.2. Motor de Precificação Dinâmica (FinOps)
$$\text{Custo USD} = \left(\frac{\text{Tokens Prompt}}{1.000.000} \times \text{Preço Prompt}\right) + \left(\frac{\text{Tokens Completion}}{1.000.000} \times \text{Preço Completion}\right)$$

| Provedor | Modelo | Custo / 1M Prompt (USD) | Custo / 1M Completion (USD) |
|---|---|---|---|
| Google | `gemini-3.5-flash-lite` | \$0.075 | \$0.30 |
| Google | `gemini-3.6-flash` | \$0.15 | \$0.60 |
| OpenAI | `gpt-4o-mini` | \$0.15 | \$0.60 |
| OpenAI | `gpt-4o` | \$2.50 | \$10.00 |

---

## 7. Auditoria Contínua Anti-Alucinação (`LLM-as-a-Judge`)

Arquivo de referência: [`ai_evaluation_service.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/ai_evaluation_service.py).

Para assegurar governança corporativa sobre o atendimento automatizado, o FluxIA implementa um auditor baseado no framework **RAG Triad**, disparado de forma assíncrona via `FastAPI.BackgroundTasks` (zero impacto na latência percebida pelo cliente final):

### 7.1. Critérios de Avaliação (RAG Triad)
1. **Fidelidade Comercial e Anti-Alucinação (`score_fidelidade`: 0 a 100%)**:
   - A IA respeitou os produtos, serviços, preços cadastrados e regras oficiais da empresa?
   - **Alucinação Crítica** (`possivel_alucinacao = True`, `score_fidelidade < 50%`):
     - A IA inventou serviços ou planos que a empresa não comercializa.
     - A IA inventou ou alterou preços, descontos ou políticas não autorizadas.
     - A IA contradisse informações explícitas da base de conhecimento da empresa.
2. **Relevância da Resposta (`score_relevancia_resposta`: 0 a 100%)**:
   - A resposta foi clara, profissional, concisa e respondeu diretamente à dúvida do cliente?
3. **Relevância do Contexto (`score_relevancia_contexto`: 0 a 100%)**:
   - Os chunks recuperados da base de conhecimento eram pertinentes para a dúvida?

Os dados são armazenados na tabela `ia_evaluations`, permitindo aos diretores auditar o índice de confiabilidade e os alertas de alucinação no painel de Analytics.

---

## 8. Dashboard Comercial e Inteligência por Produto

Arquivo de referência: [`crm.py`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/routes/crm.py#L2608), [`crm_portal.html`](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/static/crm_portal.html).

O endpoint `GET /dashboard/metricas` consolida em tempo real o desempenho comercial e de produtos da empresa:

### 8.1. Métricas Consolidadas do CRM
- `total_clientes`, `total_leads_telegram`, `conversas_ativas`.
- `total_negocios`, `negocios_fechados`, `negocios_perdidos`.
- `receita_pipeline` (negócios abertos no funil) e `receita_fechada` (vendas concluídas).
- `taxa_conversao` (% de negócios fechados sobre o total de negócios válidos).

### 8.2. Módulo de Inteligência Comercial por Produto
Agrega por produto ativo no catálogo:
- **Vendas Concluídas**: Contagem de negócios fechados vinculados ao produto.
- **Faturamento Fechado**: Soma da receita realizada com o produto.
- **Oportunidades em Aberto**: Contagem e valor total de negócios em negociação no pipeline.
- **Participação no Faturamento**: Percentual de contribuição do produto na receita global da empresa.
- **Destaques Automáticos**: Identificação do *Produto Mais Vendido*, *Produto de Maior Faturamento* e *Produto com Maior Pipeline Ativo*.

---

## 9. Dicionário de Dados do Banco PostgreSQL

| Tabela | Colunas Chave | Papel no Subsistema de IA e Negócio |
| :--- | :--- | :--- |
| `empresas` | `id`, `nome`, `cnpj`, `status`, `criado_em` | Cadastro de inquilinos (*tenants*) do SaaS. |
| `usuarios` | `id`, `empresa_id`, `nome`, `email`, `senha_hash`, `perfil`, `status` | Usuários do sistema (Master, Admin, Colaborador). |
| `configuracoes_empresa` | `empresa_id`, `gemini_api_key`, `openai_api_key`, `provedor_ia_padrao`, `ia_prompt_sistema`, `ia_coletar_dados_obrigatorio`, `telegram_bot_token` | Configurações BYOK, prompts e regras de atendimento. |
| `produtos` | `id`, `empresa_id`, `nome`, `preco`, `descricao`, `ativo`, `pipeline_id` | Catálogo de produtos e serviços para venda e RAG. |
| `clientes` | `id`, `empresa_id`, `nome`, `telefone`, `email`, `telegram_chat_id`, `origem` | Base de leads e clientes qualificados no CRM. |
| `pipelines` & `etapas_pipeline` | `id`, `empresa_id`, `nome`, `ordem`, `cor` | Estrutura de estágios do funil de vendas (Kanban). |
| `negocios` | `id`, `empresa_id`, `cliente_id`, `produto_id`, `etapa_id`, `valor_estimado`, `estagio` | Oportunidades comerciais em andamento ou fechadas. |
| `propostas` | `id`, `empresa_id`, `negocio_id`, `cliente_id`, `produto_id`, `valor`, `condicoes_pagamento`, `documento_id` | Propostas comerciais emitidas com PDF gerado. |
| `contratos` | `id`, `negocio_id`, `status`, `valor_contrato`, `data_assinatura` | Contratos vigentes formalizados após fechamento. |
| `documentos` | `id`, `empresa_id`, `nome_arquivo`, `hash_conteudo`, `nivel_acesso`, `origem`, `ref_tipo`, `ref_id` | Documentos indexados para RAG (público vs interno). |
| `chunks` | `id`, `documento_id`, `numero_chunk`, `conteudo`, `embedding` | Fatias textuais com vetores JSON de 768 dimensões. |
| `conversas_telegram` | `chat_id`, `empresa_id`, `status`, `atendente_id`, `ticket_id_ativo`, `ultimo_humano_em` | Estado operacional do chat (`bot` vs `humano`). |
| `historico_conversas` | `id`, `empresa_id`, `chat_id`, `mensagem_usuario`, `resposta_ia`, `documentos_utilizados` | Memória de conversas e logs do bot no Telegram. |
| `conversas_chat_portal` | `id`, `empresa_id`, `usuario_id`, `titulo`, `atualizado_em` | Threads de conversação do Copiloto interno no portal. |
| `mensagens_chat_portal` | `id`, `conversa_id`, `remetente`, `texto`, `fonte`, `criado_em` | Mensagens individuais da thread do portal web. |
| `tickets` | `id`, `empresa_id`, `cliente_id`, `telegram_chat_id`, `tipo`, `status`, `prioridade`, `titulo` | Fila de atendimento humano prioritário sincronizada com Telegram. |
| `ia_telemetria_execucao` | `id`, `empresa_id`, `canal`, `session_id`, `vendor`, `modelo`, `tokens_prompt`, `tokens_completion`, `custo_estimado_usd`, `latencia_ms` | Telemetria completa de FinOps e consumo de LLMs. |
| `ia_evaluations` | `id`, `telemetria_id`, `score_fidelidade`, `score_relevancia_resposta`, `score_relevancia_contexto`, `possivel_alucinacao`, `justificativa_avaliacao` | Governança e auditoria científica anti-alucinação. |

---

## 10. Elementos de Referência para Modelagem do Diagrama

Para desenhar o diagrama técnico oficial (Mermaid, PlantUML, Draw.io ou C4 Model), estruture os componentes conforme os seguintes limites de fronteira (*boundaries*):

1. **Camada de Atores**:
   - `Lead Externo` (Telegram Messenger App).
   - `Operador Comercial / Atendente` (Navegador Desktop / Painel de Atendimento).
   - `Administrador / Gestor` (Navegador Desktop / Painel CRM, Analytics e FinOps).
2. **Camada de Borda (Edge / Gateway)**:
   - `FastAPI Webhook Router` (`POST /telegram/webhook/{empresa_id}`).
   - `FastAPI Portal Router` (`POST /chat/`, `GET /dashboard/metricas`).
3. **Camada de Orquestração e Agentes**:
   - `AgentOrchestrator` (Decisor de estados `bot` vs `humano`, extrator de entidades e despachante).
   - `SemanticIntentClassifier` (Classificador semântico em 10 categorias).
   - `DossieComercialBuilder` (Compilador sigiloso de propostas e contratos).
4. **Camada de RAG e Processamento Documental**:
   - `DeduplicationFilter` (Cálculo e verificação SHA-256).
   - `MultimodalExtractor` (Extratores de texto e OCR Vision Gemini).
   - `ChunkingEngine` (Janela deslizante de 1000 caracteres com 200 de sobreposição).
   - `EmbeddingEngine` (`gemini-embedding-001`).
   - `SemanticSearchService` (Cálculo de cosseno com expansão de vizinhança $N-1, N, N+1$).
5. **Camada de Roteamento de IA (Multi-Vendor Engine)**:
   - `MultiVendorAIEngine` (LangChain Core com BYOK).
   - `PrimaryLLM` (`gemini-3.5-flash-lite`).
   - `FallbackLLM` (`gpt-4o-mini`).
6. **Camada de Serviços de Negócio**:
   - `ProposalPdfService` (Renderizador de PDF ReportLab).
   - `TicketService` (Criador de tickets com síntese executiva por IA).
   - `FollowupScheduler` (Reengajamento automatizado por APScheduler).
7. **Camada de Persistência (PostgreSQL Database)**:
   - Partição lógica em todas as tabelas por `empresa_id`.
   - Tabelas de CRM, Conhecimento, Telemetria e Auditoria.
8. **Camada de Observabilidade e Governança**:
   - `AIObservabilityCallbackHandler` (Rastreamento síncrono de tokens e custo USD).
   - `LLM-as-a-Judge Worker` (Auditoria assíncrona em segundo plano com RAG Triad).
