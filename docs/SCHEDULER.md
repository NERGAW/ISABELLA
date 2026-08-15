# Scheduler

O Scheduler gerencia tarefas temporais persistentes e executa somente Skills
registradas. Ele reutiliza o Runtime, Event Bus, Automations e Security; não cria
um executor paralelo nem aceita código, shell, cron arbitrário ou APIs externas.

## Tipos e timezone

Tipos suportados:

- `ONE_TIME`: timestamp único em `run_at`;
- `INTERVAL`: `recurrence.interval_seconds`;
- `DAILY`: `recurrence.hour` e `minute`;
- `WEEKLY`: `recurrence.weekdays` (segunda=0) mais `hour` e `minute`.

Timestamps precisam ser timezone-aware. A configuração padrão usa
`America/Sao_Paulo`, com a base IANA fornecida pelo pacote leve `tzdata` no
Windows. Valores ingênuos são rejeitados em vez de interpretados silenciosamente
como UTC ou horário local.

## Persistência e lifecycle

As tarefas ficam em `data/scheduler.db`, separadas da Memory e das Automations.
No startup, tarefas pendentes são carregadas e a próxima execução é recalculada.
Um único worker dorme até a próxima deadline, com despertar quando uma tarefa é
criada, pausada ou cancelada. Não há busy loop e a precisão pretendida é de
segundos, não real-time.

O modelo registra ID, nome, enabled, tipo, `run_at`, recorrência, Skill,
argumentos, risco obtido do Registry, timestamps, próxima/última execução,
contador e status.

## Tarefas perdidas

`missed_task_policy` aceita:

- `SKIP` (padrão): tarefas únicas viram `MISSED`; recorrentes avançam para a
  próxima ocorrência;
- `RUN_ON_STARTUP`: a tarefa vencida entra no fluxo normal assim que o worker
  inicia;
- `ASK`: a tarefa é pausada e um evento informa que confirmação é necessária.

Nenhuma política ignora o Security Policy Engine.

## Linguagem natural

O parser determinístico entende formas limitadas:

- `daqui a 10 minutos` ou `daqui a 2 horas`;
- `amanhã às 8`;
- `todo dia às 19`;
- horário exato como `às 18 horas`.

O Brain intercepta apenas pedidos com marcadores temporais inequívocos, cria uma
especificação de `scheduler.create` e usa o fluxo normal de confirmação. Frases
como `amanhã de manhã` retornam uma solicitação de horário exato. Não se inventa
um horário e não há parser cron.

## Segurança e ações críticas

`scheduler.create` é `CRITICAL`, pois encapsula uma ação futura dinâmica. Assim,
a tarefa só é persistida após confirmação explícita da criação. Quando vence, a
Skill de destino passa novamente pelo mesmo Registry e Security.

O Scheduler nunca fornece `confirmed` ou confirmation ID. Se a ação de destino
for crítica, ela não é executada automaticamente: fica registrada como falha com
`CONFIRMATION_REQUIRED`. Essa decisão conservadora impede que uma confirmação
antiga seja reutilizada como autorização irrestrita. Cancelamento e pausa são
`SAFE`; retomada é `CAUTION`.

## Reminders e eventos

Lembretes usam a Skill interna `scheduler.reminder`. Ao disparar, publicam
`scheduler.reminder` e `ui.message`; o Runtime também liga o callback de TTS do
Core para falar o texto quando Voice Output estiver disponível.

Eventos publicados: `scheduler.task_created`, `task_due`, `task_started`,
`task_completed`, `task_failed`, `task_cancelled` e `scheduler.reminder`.
`scheduler.task_due` pode acionar regras do Automations Engine pela assinatura já
existente do Event Bus.

## Skills e diagnóstico

Skills: `scheduler.create`, `scheduler.list`, `scheduler.cancel`,
`scheduler.pause` e `scheduler.resume`. Diagnostics informa total de tarefas,
próxima tarefa, falhas e acesso ao SQLite.

Não foram implementados API externa, Nodes, cron complexo ou avanço automático.

