# Runtime e lifecycle da I.S.A.B.E.L.L.A.

O Runtime é o único coordenador de startup, dependências, modo degradado,
restart local e shutdown da aplicação. `main.py` apenas escolhe GUI ou CLI, cria
o Runtime, inicia, aguarda e encerra.

## Modelo de serviço

Cada `Service` possui nome, dependências, flag `required`, estado, hooks de
start/stop/health, métricas de tempo, erro recente e contador de restart. Os
estados são `STOPPED`, `STARTING`, `ONLINE`, `DEGRADED`, `ERROR` e `STOPPING`.

O `ServiceRegistry` valida dependências ausentes, detecta ciclos e produz uma
ordem topológica determinística. Falha de um serviço required aborta o startup e
faz rollback. Falha opcional deixa o Runtime `DEGRADED` e os demais continuam.

## Serviços concretos

- Core — required; configura logging e cria o Event Bus.
- Event Bus — required e sempre encerrado por último.
- Intelligence — Brain e LLM; Ollama offline resulta em `DEGRADED`, mantendo
  Skills locais disponíveis.
- Security, Memory, Context, Skills e Vision — serviços-proxy com health próprio;
  seus recursos físicos continuam pertencendo ao Brain para não haver shutdown
  duplicado.
- Diagnostics — consulta o Runtime como fonte de lifecycle; seu monitor
  periódico continua opt-in.
- HUD — somente no modo GUI e executado na thread principal do Qt.
- Voice Input e Voice Output — opcionais e reiniciáveis isoladamente.

## Startup e shutdown

O startup respeita dependências e mede o tempo de cada serviço e do Runtime. Ao
final, imprime `ISABELLA ONLINE` ou `ISABELLA DEGRADED` e a tabela de serviços.

No shutdown, novos comandos são recusados primeiro. A ordem reversa encerra TTS,
Voice, HUD/workers, Diagnostics, Vision e demais proxies, Intelligence, Core e,
por último, Event Bus. Hooks comuns têm timeout configurável; o HUD é a única
exceção porque o Qt exige execução no thread principal. Os próprios workers do
HUD, Voice e TTS já possuem encerramento limitado.

## Restart

`restart_service(nome)` para somente o serviço afetado, respeita dependências,
cooldown e `restart_attempts`. Um serviço recuperado volta à lista de shutdown.
Ao atingir o limite, não há novas tentativas nem loop infinito.

## Modos

`python main.py` e `python main.py --cli` usam `ApplicationRuntime`. O modo CLI
remove somente o serviço HUD; todos os demais contratos de lifecycle são os
mesmos.

## Eventos e configuração

São publicados `runtime.started`, `runtime.stopping`, `runtime.stopped`,
`service.starting`, `service.online`, `service.error`, `service.restarting` e
`service.stopped`. O evento de encerramento do Event Bus é enfileirado antes que
o próprio bus deixe de aceitar publicações.

`config/runtime.json` define timeouts, limite/cooldown de restart e serviços
habilitados. Não contém lógica de negócio.
