---
name: fluxia-frontend-portal
description: Frontend do FluxIA: SPA em HTML/CSS/JS puro (crm_portal.html, admin_portal.html, login.html, cadastro.html) com tokens CSS e tema dark/light. Use SEMPRE que a tarefa envolver telas (#screen-...), modais (#modal-...), menu lateral, layout, estilo, dark mode, formulários, fetch para a API, kanban, dashboard visual ou textos de interface, mesmo que o usuário diga apenas "tela", "botão" ou "página".
---

# FluxIA Frontend Portal

## Arquivos
- `app/static/crm_portal.html` (portal unificado; arquivo único, grande)
- `app/static/admin_portal.html` (painel master multiempresa)
- `app/static/login.html`, `cadastro.html`
- `app/static/css/crm_whatsapp_leads_system.css` (tokens e estilos)

## Economia de tokens (arquivo grande!)
- NUNCA abra `crm_portal.html` inteiro. Localize pelo id: busque `id="screen-<nome>"`, `id="modal-<nome>"` ou o nome da função JS e leia só aquele trecho.
- Edite com patches localizados; não reescreva a tela inteira.

## Regras de stack
- Sem React/Vue/npm/webpack. HTML5 + CSS3 + JS vanilla, sem etapa de build.
- Navegação por abas: elementos `.screen` com `id="screen-<nome>"`; modais `id="modal-<nome>"`.
- CSS: use variáveis em `:root` (tokens). Tema Dark/Light com toggle e persistência em `localStorage`. Nunca use cor hardcoded: crie/reuse um token.
- Todo dado vem da API via `fetch`; sem dados mockados/fixos.

## Telas (`#screen-...`)
dashboard (métricas reais, séries temporais) · chat (Telegram em 3 colunas: conversas, janela, painel do lead; botões Assumir e Devolver ao Bot) · chatrag (copiloto interno) · clientes · produtos · fluxos (kanban arrastável) · propostas / criar-proposta (passo a passo) · contratos · pipelines · documentos (upload drag-and-drop, alternar público/interno) · analytics · observabilidade (custo USD, latência, notas RAG Triad) · config · equipe.
- `#screen-config`, em cards: Multi-vendor e BYOK · fuso horário · bot Telegram e webhook · regras do orquestrador (coleta obrigatória e prompt) · follow-up (ativação, horas, mensagem com tags).

## Decisões de UX já tomadas
- Analytics e Configurações visíveis só para admin; upload de documentos só admin.
- Menu lateral: sem contagem de documentos/equipe; só badge de conversas não lidas do Telegram.
- Rótulos sem "RAG" no menu (ex.: "Chat IA", "Analytics").
- Login sem quadro explicativo de RBAC; tem tela de cadastro de empresa.
- Conversas do Telegram mostram nome do cliente ou "Lead".
- Respostas do chat IA renderizam markdown (sem asteriscos crus) e exibem o modelo usado.
- Sem bot configurado: aviso claro na tela de configuração (cada empresa precisa do seu bot).

## Padrões de implementação
- Ao criar tela: nova `<section class="screen" id="screen-x">`, item no menu, função de carregamento chamada ao abrir a aba.
- Esconda/mostre itens por perfil lendo o perfil da sessão; a checagem real continua no backend.
- Escape de HTML ao injetar texto vindo da API (evitar XSS), principalmente mensagens de clientes.
- Teste mentalmente os dois temas (dark/light) ao mudar CSS.
