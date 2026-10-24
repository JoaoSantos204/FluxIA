# FluxIA — Documento de Arquitetura, Tecnologias e Estratégia de IA

> **Classificação:** Documento Estratégico & Técnico  
> **Público-alvo:** Sócios, Diretores, Desenvolvedores e Gestores de Produto  
> **Versão:** 1.0.0 — Pronto para Compartilhamento  

---

## Sumário Executivo

O **FluxIA** é uma plataforma SaaS B2B multitenant que une **Atendimento Omnichannel Automatizado via Telegram**, **CRM Comercial com Funil de Vendas** e um **Motor de Inteligência Artificial Especializado**. 

Diferente de soluções convencionais que apenas conectam um prompt genérico ao ChatGPT, o FluxIA foi desenhado com foco em:
1. **Segurança de dados e isolamento total entre empresas.**
2. **Zero alucinação em preços e produtos oficiais.**
3. **Controle financeiro em tempo real sobre os custos de IA.**
4. **Transbordo fluido entre bot e atendentes humanos.**

---

## 1. A Estratégia de Arquitetura: O "Condomínio Comercial Inteligente"

Para facilitar a compreensão técnica e de negócios, a arquitetura do FluxIA adota a analogia de um **Condomínio Comercial de Alto Padrão**:

* **A Infraestrutura Compartilhada (SaaS Multitenant):** Toda a estrutura do prédio (elevadores, segurança, fundação) é unificada para baratear os custos de operação. No entanto, **cada empresa cliente possui sua própria sala com chave blindada**. A Empresa "A" jamais consegue visualizar contatos, contratos, documentos ou conversas da Empresa "B".
* **A Fachada e as Salas de Reunião (Front-end):** A interface com a qual o atendente e o gestor interagem. Criada para ser extremamente leve, sem travar e de resposta instantânea.
* **A Administração Central (Back-end):** O motor de regras de negócio que valida permissões, calcula propostas, aciona o Telegram e orquestra as decisões.
* **O Arquivo Central Blindado (Banco de Dados):** Onde todas as informações ficam organizadas com etiquetas rígidas por empresa (`empresa_id`).
* **A Equipe de IA Especializada:** Uma divisão de tarefas onde cada módulo de inteligência artificial tem uma atribuição clara: recepção, consulta aos manuais, auditoria de qualidade e medição de custos.

```
                              [ INTERFACE WEB ]
                        (HTML5 + CSS3 + Vanilla JS)
                                     │
                                     ▼
                              [ SERVIDOR API ]
                        (FastAPI / Python 3.12)
                                     │
             ┌───────────────────────┼───────────────────────┐
             ▼                       ▼                       ▼
    [ BANCO POSTGRESQL ]    [ MOTOR DE IA LANGCHAIN ]  [ AGENDADOR JOBS ]
    (Isolamento por Tenant) (Gemini / OpenAI / RAG)    (APScheduler 1h/5min)
```

---

## 2. Decisões Tecnológicas: O que usamos, onde e por quê

A escolha das tecnologias priorizou **estabilidade, velocidade de carregamento, baixo consumo de memória e facilidade de manutenção**.

### 2.1. Front-end (Portal de Operação e CRM)
* **Tecnologias:** HTML5 Semântico, CSS3 Moderno (com Design Tokens em `:root`) e JavaScript puro (ES6+ Vanilla).
* **Decisão Estratégica:** **NÃO utilizamos frameworks pesados como React, Vue ou Angular.**
  * *Por que essa decisão foi tomada?*
    1. **Eliminação de Etapas de Compilação (Zero Build):** Não há dependência de Node.js, npm ou Webpack em produção. Qualquer alteração reflete imediatamente.
    2. **Carregamento Instantâneo:** As páginas abrem em frações de segundo, sem baixar megabytes de pacotes JavaScript de terceiros.
    3. **Ergonomia Operacional:** O módulo de chat adota o padrão consagrado do WhatsApp Web / Telegram Desktop em 3 colunas (Lista de conversas, Janela ativa e Ficha completa do Lead no CRM).
    4. **Design Flexível com Dark/Light Mode:** Implementado com variáveis CSS nativas que mudam o tema instantaneamente sem recarregar a tela.

### 2.2. Back-end (Núcleo de Regras e Conexões)
* **Tecnologias:** Python 3.12, FastAPI, Uvicorn, psycopg2-binary, APScheduler, ReportLab.
* **Decisão Estratégica:** **FastAPI como ponto central de micro-operações assíncronas.**
  * *Por que essa decisão foi tomada?*
    1. **Processamento Assíncrono Nativo:** Atende dezenas de mensagens simultâneas do Telegram sem bloquear o servidor.
    2. **Validação Rigorosa com Pydantic:** Todo dado que entra ou sai da API é tipado e verificado automaticamente contra falhas.
    3. **APScheduler Integrado ao Processo:** Tarefas como envio de mensagens de aquecimento de leads (follow-up de inatividade a cada 1 hora) e expiração de atendentes ociosos (timeout a cada 5 minutos) rodam em segundo plano sem exigir a contratação de servidores pesados de filas (como Redis/Celery) no estágio atual.
    4. **Emissão Dinâmica de Propostas em PDF:** Uso do ReportLab para transformar negócios ganhos em documentos comerciais prontos para assinatura.

### 2.3. Banco de Dados (Persistência e Isolamento)
* **Tecnologia:** PostgreSQL (Compatível com Neon, Render Postgres ou Cloud dedicada).
* **Decisão Estratégica:** **Multitenancy Lógico com Migrações Idempotentes no Startup.**
  * *Por que essa decisão foi tomada?*
    1. **Segurança Multitenant:** Cada tabela de negócio (`clientes`, `produtos`, `negocios`, `documentos`, `conversas_telegram`) possui a coluna `empresa_id`. Nenhuma consulta SQL é realizada sem filtrar esse identificador.
    2. **Autonomia de Deploy:** Ao ligar o sistema, a função `criar_banco()` cria e atualiza tabelas, colunas e índices de forma automática e idempotente (`IF NOT EXISTS`), permitindo atualizações contínuas de código sem necessidade de passos manuais no banco de dados.

---

## 3. Arquitetura da Inteligência Artificial em 4 Camadas

A IA do FluxIA não é uma "caixa preta". Ela opera através de **4 camadas especializadas e interdependentes**:

```
[Mensagem do Cliente no Telegram]
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 1. O GERENTE / ORQUESTRADOR (Classificador de Intenção) │
│    • Coleta dados obrigatórios (Nome real e WhatsApp)?  │
│    • Direciona: Saudação, Dúvida, Proposta ou Humano?   │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 2. O CONSULTOR DA BASE (Motor RAG e Busca Semântica)    │
│    • Consulta os arquivos oficiais DA EMPRESA           │
│    • Barreira de Segurança: Público (Bot) vs Interno    │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│ 3. O TAXÍMETRO (Observabilidade de Custos e Latência)   │
│    • Registra tokens, milissegundos e valor em USD      │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼ (Disparado em Segundo Plano)
┌─────────────────────────────────────────────────────────┐
│ 4. O AUDITOR DE QUALIDADE (Juiz Anti-Alucinação)        │
│    • Audita a fidelidade aos preços e catálogo oficial  │
└────────────────────────────┘
```

---

### Camada 1: O Agente Gerente / Orquestrador (`agent_orchestrator.py`)
* **Papel:** É a recepção qualificada do negócio.
* **Responsabilidades:**
  * **Qualificação Obrigatória de Contato:** Se a empresa configurar a coleta de dados como ativa, o bot identifica se o lead ainda possui nome genérico ou telefone incompleto. Em vez de abrir respostas amplas, ele conduz o cliente com cordialidade para fornecer seu nome completo e WhatsApp com DDD.
  * **Tratamento de Mensagens Mistas:** Se o cliente fornecer os dados e já fizer uma pergunta na mesma frase (*"Meu nome é Roberto, whats 11988887777, qual o preço do plano?"*), o orquestrador cadastra o contato no CRM e responde a dúvida comercial no mesmo turno.
  * **Máquina de Estados de Vendas (Funil Unidirecional):** Os estágios comerciais no CRM seguem a ordem: `novo` $\to$ `qualificado` $\to$ `proposta` $\to$ `negociacao` $\to$ `fechado`. A automação da IA só avança o funil, nunca regride automaticamente. Ao chegar em `fechado`, o contrato é criado de forma automática.

---

### Camada 2: O Motor RAG Semântico (`semantic_search_service.py`)
* **Papel:** O especialista que consulta os manuais da empresa para responder dúvidas com precisão cirúrgica.
* **O que é RAG?** *Retrieval-Augmented Generation* (Geração Aumentada por Recuperação). A IA não "tenta adivinhar": ela pesquisa nos documentos oficiais da empresa antes de formular a frase.
* **Barreira de Segurança (Público vs. Interno):**
  * **Documentos Públicos:** Manuais de produtos, tabelas de preço de venda e horários. São os únicos autorizados para o robô do Telegram utilizar com clientes externos.
  * **Documentos Internos:** Propostas confidenciais, minutas de contratos e estratégias internas. Ficam disponíveis **apenas** para o Copiloto do portal web usado pelos colaboradores da empresa.
* **Mecanismo de Busca:** Os documentos são divididos em pequenos blocos (chunks), convertidos em vetores matemáticos (Google `text-embedding-004`) e comparados por similaridade de cosseno com a pergunta do cliente. Chunks com relevância inferior a 70% são descartados para evitar ruído.

---

### Camada 3: Observabilidade e Gestão de Custos (`ai_observability_service.py`)
* **Papel:** O taxímetro financeiro e técnico da inteligência artificial.
* **Responsabilidades:**
  * Utiliza o `AIObservabilityCallbackHandler` do LangChain acoplado ao ciclo de vida da chamada.
  * Mede com precisão: tokens de envio, tokens de resposta, tempo de resposta em milissegundos e modelo acionado.
  * **Calcula o custo financeiro em dólares (USD)** com base nas tabelas oficiais dos provedores (Google Gemini e OpenAI). O gestor da empresa sabe exatamente quanto cada lead ou conversa custou para o negócio.

---

### Camada 4: Auditoria Contínua Anti-Alucinação (`ai_evaluation_service.py`)
* **Papel:** O auditor de qualidade executiva e jurídica (Padrão *LLM-as-a-Judge*).
* **Responsabilidades:**
  * Opera de forma assíncrona (`BackgroundTasks`) logo após o envio da mensagem, **sem atrasar nem um milissegundo o tempo de resposta percebido pelo cliente**.
  * Aplica a metodologia científica **RAG Triad**:
    1. **Fidelidade Comercial (0 a 100%):** A IA inventou algum plano, preço ou desconto não autorizado? Se inventou, gera um alerta de alucinação no painel do administrador.
    2. **Relevância da Resposta (0 a 100%):** A resposta foi clara e respondeu objetivamente ao que foi perguntado?
    3. **Relevância do Contexto (0 a 100%):** Os documentos recuperados da base eram pertinentes para a dúvida?

---

## 4. O Sistema de Transbordo: Bot vs. Atendimento Humano

Para que a automação seja viável em empresas reais, o controle humano é absoluto e imediato:

1. **Modo Bot:** O agente conversa, responde dúvidas públicas, recolhe contatos e avança negócios no CRM.
2. **Transferência para Humano:** Caso o cliente solicite um atendente (*"quero falar com uma pessoa"*), ou a negociação exija intervenção comercial, o sistema:
   * Altera a conversa para `status = 'humano'`.
   * Cria um **Ticket de Atendimento** na tela do portal.
   * **Silencia a IA imediatamente:** Nenhuma resposta automática é gerada enquanto a conversa estiver sob comando humano.
3. **Trava de Segurança de 30 Minutos (Timeout de Ociosidade):** Se um atendente assumir a conversa mas deixar o cliente sem retorno por mais de 30 minutos, um processo em segundo plano ([ticket_service.py](file:///c:/Users/paulo/Desktop/FluxIA/FluxIA/app/services/ticket_service.py)) devolve o atendimento para o bot automaticamente, impedindo que o lead esfrie.

---

## 5. Estrutura Central de Dados (Principais Tabelas)

| Tabela | Função Estratégica no FluxIA |
| :--- | :--- |
| `empresas` | Identificação das contas e tenants do SaaS. |
| `usuarios` | Colaboradores com níveis de acesso (Master, Admin, Colaborador). |
| `configuracoes_empresa` | Chaves BYOK (Gemini/OpenAI), token do bot do Telegram e regras de atendimento da empresa. |
| `produtos` & `pipelines` | Catálogo de soluções e etapas do funil de vendas. |
| `clientes` & `negocios` | Leads cadastrados com estágio no funil, valor estimado e histórico de interações. |
| `propostas` & `contratos` | Propostas emitidas (com PDF vinculado) e contratos formalizados. |
| `documentos` & `chunks` | Base de conhecimento da empresa, dividida entre público e interno com vetores de busca semântica. |
| `conversas_telegram` | Controle do status do chat (`bot` vs `humano`) e atendente responsável. |
| `tickets` & `ticket_mensagens` | Fila de atendimento humano sincronizada com o Telegram. |
| `ia_telemetria_execucao` | Histórico minucioso de tokens, latência e custo em USD de cada interação. |
| `ia_evaluations` | Notas de auditoria (RAG Triad), fidelidade de catálogo e alertas de alucinação. |

---

## 6. Roteiro Executivo de Apresentação (2 Minutos)

Para reuniões com clientes, parceiros ou investidores, utilize este roteiro:

> *"O **FluxIA** é uma solução completa que transforma o canal de mensagens do Telegram em uma máquina de vendas com CRM integrado e Inteligência Artificial.*
>
> *Diferente de robôs comuns que apenas dão respostas soltas e frequentemente inventam informações, o FluxIA foi desenhado com três garantias de nível empresarial:*
> 1. * **Velocidade sem excessos técnicos:** Desenvolvido em Python FastAPI com front-end puro e sem frameworks pesados, garantindo que o sistema seja rápido, estável e não dependa de servidores caros para rodar.*
> 2. * **Inteligência Segura e Baseada em Fatos:** A IA só responde sobre produtos e regras consultando os arquivos oficiais da empresa. Os documentos confidenciais ficam blindados e nunca são exibidos para clientes externos.*
> 3. * **Governança e Transparência Absoluta:** O gestor acompanha quanto cada mensagem custou em centavos de dólar e conta com um auditor automático que avisa se a IA manteve fidelidade total ao catálogo, com transbordo fácil para operadores humanos a qualquer momento."*
