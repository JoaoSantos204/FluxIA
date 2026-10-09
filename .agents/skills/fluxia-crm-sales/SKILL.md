---
name: fluxia-crm-sales
description: Módulo comercial/CRM do FluxIA: produtos, clientes, pipelines e etapas por produto, negócios (funil/kanban), propostas, contratos e follow-up automático de leads. Use SEMPRE que a tarefa tocar crm.py, followup_service, funil, estágio, proposta, contrato, aquecimento de leads, tags {nome}/{produto}/{estagio} ou dashboard de vendas, mesmo que o usuário só diga "vendas" ou "leads".
---

# FluxIA CRM & Vendas

## Arquivos
- `app/routes/crm.py` (CRUD completo do módulo)
- `app/services/followup_service.py` (agendador de follow-up)
- Tabelas em `database.py`: produtos, pipelines, etapas_pipeline, clientes, negocios, propostas, contratos.

## Modelo de dados (resumo)
- `produtos`: nome, descricao, preco, ativo, pipeline_id.
- `pipelines`: nome, produto_id, padrao. Um pipeline pode ser específico de um produto; existe um pipeline `padrao`.
- `etapas_pipeline`: pipeline_id, nome, ordem, cor.
- `clientes`: nome, telefone, email, telegram_chat_id, origem, aguardando_contato. Podem ser criados manualmente OU pela IA.
- `negocios`: cliente_id, produto_id, etapa_id, estagio, valor_estimado, proposta_enviada, ultima_interacao_em, ultimo_followup_em.
- `propostas`: negocio_id, cliente_id, produto_id, valor, condicoes_pagamento, validade_dias, status_envio, documento_id.
- `contratos`: negocio_id, status (pendente|assinado|cancelado), valor_contrato, data_assinatura.
- Tudo filtrado por `empresa_id` (ver fluxia-core).

## Regras de negócio
- Estágios: novo(1) -> qualificado(2) -> proposta(3) -> negociacao(4) -> fechado(5). Automação da IA só avança. Mudança manual pelo usuário (kanban) é permitida.
- Ao chegar em `fechado`, criar contrato automaticamente (status pendente).
- Um cliente pode ter vários negócios, cada um ligado a um produto distinto.
- Propostas e contratos viram documentos INTERNOS automáticos na base de conhecimento (`documentos.origem`, `ref_tipo`, `ref_id`), para o chat interno responder sobre eles. Nunca públicos.
- Proposta: template padrão gera documento para envio por e-mail.

## Endpoints
GET/POST /crm/produtos · /crm/clientes · /crm/pipelines · /crm/negocios · POST /crm/propostas/direta · POST /crm/executar-followup.

## Follow-up automático ("aquecimento")
- Job APScheduler `job_followup_automatico` a cada 1 h; também manual via POST /crm/executar-followup.
- Elegíveis: negócio fora de `fechado`/`perdido`, com `telegram_chat_id` no cliente, inatividade (`ultima_interacao_em` ou `ultimo_followup_em`) maior que `followup_horas_inatividade` (padrão 24 h) e `followup_ativo=True`.
- Mensagem: usa `followup_mensagem_personalizada` com tags `{nome}`, `{produto}`, `{estagio}`; se vazia, a IA (Gemini Flash Lite) gera 2 a 3 frases humanizadas e contextualizadas.
- Após enviar, atualizar `ultimo_followup_em` para não repetir no próximo ciclo.
- Envio usa o bot Telegram DA empresa.

## Dashboard
- Métricas e gráficos vêm de dados reais por `empresa_id`; séries temporais (clientes e vendas por mês). Sem valores fixos/mockados.

## Cuidados
- Ao criar negócio/proposta, valide que cliente, produto e pipeline pertencem à mesma `empresa_id`.
- Ao excluir produto/pipeline, trate negócios dependentes (não deixar órfãos).
