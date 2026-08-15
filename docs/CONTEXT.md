# Context Engine da I.S.A.B.E.L.L.A.

O Context Engine representa o que está acontecendo agora. Ele é volátil e
começa uma nova sessão a cada inicialização.

Memory responde “o que ficou guardado entre sessões?”. Context responde “qual é
o estado operacional neste momento?”. O Context Engine não grava snapshots no
SQLite e não duplica a Working Memory.

## Snapshot

`ContextSnapshot` contém:

- timestamp e identificador aleatório da sessão;
- aplicativo e título da janela ativa;
- projeto atual;
- último comando e última resposta;
- última Skill, ação tipada e argumentos;
- último resultado, sucesso, mensagem e dados seguros;
- estado de voz;
- estados básicos de Core, LLM, voz, HUD e Memory;
- dispositivos de áudio realmente detectados;
- metadata limitada para estado controlado futuro.

Os tipos iniciais são `SESSION`, `APPLICATION`, `WINDOW`, `PROJECT`, `DEVICE`,
`VOICE`, `LAST_ACTION`, `LAST_RESULT` e `SYSTEM`.

## Provider Windows

O provider consulta `GetForegroundWindow`, título e processo da janela por meio
das APIs nativas do Windows e `psutil`. Não existe thread de polling. A consulta
acontece sob demanda, com intervalo mínimo de 1 segundo entre atualizações.

Se a API falhar, aplicativo e título ficam como `unavailable`, Context assume
`DEGRADED` e o restante da Isabella continua funcionando. Microfone e saída de
áudio são consultados uma vez no startup da HUD.

## Resolução de referências

A camada determinística reconhece referências simples como:

- “ele”, “ela”, “isso” e “esse programa”;
- “o aplicativo” e “o navegador”;
- “o programa que está aberto”.

Fontes possíveis são a última ação, janela ativa e preferência persistente de
navegador. Cada resolução produz entidade, origem e confidence. O limiar atual é
0,8. Se a última ação e a janela real indicarem programas diferentes, a
confidence cai para 0,45 e nenhuma Skill é executada sem clarificação.

Não há substituição textual cega e somente campos relevantes são fornecidos ao
Brain ou ao LLM.

## Ações, resultados e segurança

Toda Skill executada registra identificador, argumentos relevantes e risk level.
O resultado registra sucesso, status, mensagem e dados, removendo campos como
traceback e exception.

“Faça de novo” recria um `SkillRequest`, mas sempre o envia novamente ao Skill
Registry. Assim:

- `SAFE` e `CAUTION` seguem as regras normais;
- `CRITICAL` exige uma confirmação nova, mesmo que a ação anterior já tenha sido
  confirmada ou cancelada.

“Continue” não retoma tarefas arbitrárias. Sem plano controlado pendente, a
Isabella informa que não há tarefa para continuar. Uma confirmação pendente deve
ser tratada pela caixa de confirmação existente.

## Memory e projeto

`preferred_browser` pode resolver “Abra o navegador”. `current_project_name`
inicializa `current_project`, mas o restante do snapshot permanece volátil. Um
reset de sessão reaplica somente esse contexto persistente explicitamente útil.

## HUD

O painel lateral mostra apenas `App ativo` e `Projeto`, além do status `CONTEXT`.
Não foi criado painel complexo nem timer de repaint.

## Desempenho

Medições locais em 1.000 operações:

| Operação | Média | Máximo |
|---|---:|---:|
| Snapshot | 0,015 ms | 0,115 ms |
| Resolução de referência | 0,028 ms | 0,133 ms |
| Consulta real de janela ativa | 0,642 ms | 0,642 ms |

## Limitações atuais

- Não há Vision nem análise visual da janela.
- Não há retomada genérica de planos complexos.
- Não há Event Bus completo ou contexto distribuído.
- Apenas dispositivos de áudio locais realmente detectados são registrados.
- Contexto é uma indicação conservadora; ambiguidades não são resolvidas por
  adivinhação.

