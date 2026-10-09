---
name: fluxia-agent-orchestrator
description: Agente Gerente / orquestrador de IA e bot do Telegram do FluxIA. Use SEMPRE que a tarefa envolver agent_orchestrator, webhook do Telegram, classificação de intenção, coleta de contato (nome e WhatsApp), saudação, despedida, transbordo humano, modo bot vs humano, assumir/devolver conversa, prompt do agente ou regras de atendimento, mesmo que o usuário diga só "o bot" ou "a IA do atendimento".
---

# FluxIA Agent Orchestrator

## Arquivos (leia só o necessário)
- `app/services/agent_orchestrator.py` (núcleo: classificador + despacho)
- `app/routes/telegram.py` (webhook, conversas, envio manual)
- `app/services/company_service.py` (regras e config da empresa)
- Complementares: `history_service.py`, `ai_service.py`.

## Fluxo do webhook `POST /telegram/webhook/{empresa_id}`
1. Recebe mensagem; carrega cliente + histórico.
2. Se `conversas_telegram.status == 'humano'`: só salva no histórico para o operador; a IA NÃO responde.
3. Senão: extrai contato (nome, telefone, e-mail) => Agente Gerente classifica => executa ação => envia resposta pelo bot DA EMPRESA => salva em `historico_conversas`.

## Ações do classificador
| Ação | Quando |
|---|---|
| COLETA_CONTATO | cliente sem nome real ou telefone válido E `ia_coletar_dados_obrigatorio=True` |
| CADASTRO_CONCLUIDO | contato fornecido na mensagem; salva cliente |
| SAUDACAO | saudação inicial; usa nome e negócios ativos |
| DESPEDIDA | encerramento; agradece pelo nome, sem perguntar "como posso ajudar" |
| CONSULTA_COMERCIAL | dúvida sobre proposta/negócio; usa dados reais do CRM |
| TRANSBORDO_HUMANO | pedido de suporte/humano; muda status para `humano` |
| PERGUNTA_CONHECIMENTO | dúvida sobre produtos/regras; RAG PÚBLICO apenas |

## Regras críticas (não quebrar)
- Coleta obrigatória: sem nome real (não genérico) ou telefone com mínimo 8 dígitos, não responder genericamente; pedir nome completo e WhatsApp com DDD, de forma cordial.
- Dados + pergunta na mesma mensagem ("Sou o Lucas, whats 11988887777, qual o valor?"): salvar o contato E responder a pergunta no mesmo turno.
- Cliente cadastrado é chamado pelo nome; injetar produto e estágio comercial no prompt.
- Despedida não gera loop de reinício.
- Funil só AVANÇA, nunca regride automaticamente: novo(1) -> qualificado(2) -> proposta(3) -> negociacao(4) -> fechado(5). Ao chegar em `fechado`, inserir registro em `contratos`.
- Cada empresa tem bot próprio (token em `configuracoes_empresa`). NÃO existe bot global de fallback; sem token configurado, o bot não opera e a UI avisa.
- Bot do Telegram é público (sem vínculo por e-mail/autenticação).
- Quando a base pública não tem resposta, o bot pede nome/e-mail/telefone para encaminhar.
- Atribuição automática a atendente específico foi descartada: assumir/devolver é manual.

## Endpoints de atendimento
GET /telegram/conversas (status, atendente, não lidas) · GET /telegram/conversas/{chat_id}/mensagens · POST .../assumir · POST .../devolver · POST .../enviar · POST /telegram/configurar-webhook · GET /telegram/status-webhook.
- Lista de conversas mostra nome do cliente cadastrado ou "Lead", nunca o chat_id cru.

## Ao alterar o classificador
- Mantenha as ações como constantes/enum já existentes; não invente nomes novos sem necessidade.
- Prompt do agente é editável por empresa (`ia_prompt_sistema`); preserve o prompt padrão como fallback.
- Chamadas de LLM passam por `ai_engine_service` para manter telemetria (ver skill fluxia-rag-engine).
