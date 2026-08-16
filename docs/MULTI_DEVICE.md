# Continuidade multi-device

O `PRIMARY_PC` continua sendo o único Brain, dono de Context, working memory, Skills e Security. Nodes móveis são interfaces autenticadas. Nenhum banco Memory é copiado ao telefone.

## Sessões

Uma `IsabellaSession` contém `session_id`, `user_context`, `active_node`, `started_at`, `last_activity` e uma referência opaca à working memory. `CHAT_REQUEST` inclui texto, origem `text`/`voice` e o `session_id` conhecido; `CHAT_RESULT` devolve a mesma sessão. `handoff_session(target_node)` muda o destino preferencial e atualiza o Context Engine. Respostas vão somente ao Node que iniciou a interação.

## Notificações

O modelo suporta `INFO`, `SUCCESS`, `WARNING`, `ERROR`, `ACTION_REQUIRED` e `REMINDER`, com ID, título, mensagem, fonte, timestamp, prioridade, ações e expiração. Scheduler, Automations e Diagnostics podem criar notificações pelo Event Bus. O roteador envia somente ao Node alvo; avisos importantes ficam em uma fila por Node limitada a 50 itens enquanto ele estiver offline. IDs conhecidos são deduplicados e o histórico volátil é limitado a 100 itens, sem virar Memory permanente.

Preferências por Node controlam informativas, avisos, erros, lembretes e segurança. Quiet mode omite `INFO`/`SUCCESS`. Não existe push cloud nesta versão.

## Ações e segurança

O Mobile reconhece o recebimento com `NOTIFICATION_ACK`. `NOTIFICATION_ACTION` aceita apenas Confirmar, Cancelar, Abrir e Dispensar. O Core verifica Node alvo, ação declarada e expiração. Confirmação crítica exige simultaneamente:

- credencial criptográfica válida e estado `TRUSTED`;
- permissão local `confirm_critical`;
- `ConfirmationRequest` existente, ainda válido e ligado à notificação;
- decisão final do Security Policy Engine.

Node errado, request expirado ou ação não declarada é negado. O PC não fala simultaneamente quando a origem é voz móvel; o telefone usa seu TTS local conforme preferência.

## Eventos

São publicados `session.created`, `session.handoff`, `notification.created`, `notification.sent`, `notification.acknowledged` e `notification.action`.
