# Diagnostics e telemetria da I.S.A.B.E.L.L.A.

O `DiagnosticsManager` agrega health checks existentes sem assumir o ciclo de
vida dos subsistemas. Ele monitora Core, LLM, Router, Planner, Skills, Voice
Input, Voice Output, HUD, Memory, Context, Vision, Event Bus e Security.

## Estados

Cada subsistema retorna `ONLINE`, `DEGRADED`, `OFFLINE`, `ERROR` ou `UNKNOWN`,
acompanhado por detalhes pequenos, horário e duração do check. Exceções de um
probe são isoladas e transformadas em `ERROR`; não derrubam a aplicação.

## Consultas

Os comandos “Isabella, diagnóstico” e “Isabella, diagnóstico detalhado” usam a
Skill `system.diagnostics`, classificada como `SAFE`. O primeiro responde com um
resumo. O segundo também abre um painel técnico leve no HUD com estados, CPU,
memória do processo e threads.

O diagnóstico explícito verifica se o Ollama responde, se o modelo configurado
está listado e consulta a câmera apenas por um health check que sempre a fecha.
Nenhum prompt é enviado ao modelo. O STT não é carregado à força e nenhum áudio
ou imagem é capturado.

## Métricas

O relatório inclui CPU, uso de RAM do sistema, memória do processo, quantidade
de threads, uptime e tamanhos das filas de Voice, TTS, HUD e Event Bus. Também
inclui latências disponíveis, erros recentes do LLM, acessibilidade e tamanho do
banco de Memory, métricas do Event Bus e confirmações pendentes/expiradas da
Security.

O histórico de falhas e as latências do próprio Diagnostics usam buffers
limitados. `diagnostics.status_changed` é publicado somente quando um status já
conhecido muda; checks repetidos no mesmo estado não geram eventos.

## Monitoramento opcional

`check()` é sob demanda. `start()` oferece um loop periódico opt-in com intervalo
normal e intervalo separado para checks caros. A aplicação não chama `start()`
automaticamente nesta fase, portanto nenhum Runtime ou alerta falado fica ativo
em segundo plano. `shutdown()` encerra esse loop caso um integrador o tenha
iniciado explicitamente.

Configuração: `config/diagnostics.json`.
