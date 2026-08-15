# Event Bus da I.S.A.B.E.L.L.A.

O Event Bus é o canal interno assíncrono entre Core, voz, inteligência, skills,
planner, TTS, memória, contexto, visão e HUD. Ele não substitui as APIs públicas
dos módulos; reduz o acoplamento para notificações de estado e resultados.

## Envelope

Cada `Event` contém `id`, `type`, `timestamp` UTC, `source`, `payload`,
`correlation_id` opcional e `priority`. O payload deve conter apenas metadados
necessários ao consumidor: nunca senhas, tokens, chaves, áudio bruto, imagens ou
tracebacks.

## Publicação e assinatura

```python
from Isabella.Events import EventType

bus.subscribe("skill.*", on_skill_event)       # categoria
bus.subscribe("system.error", on_error)       # evento específico
bus.emit(EventType.SKILL_STARTED, "skills", {"skill_id": "applications.open"})
bus.unsubscribe("skill.*", on_skill_event)
```

A publicação usa `put_nowait` e não executa assinantes na thread publicadora.
Uma fila limitada e um conjunto fixo de workers evitam crescimento indefinido.
Parte da fila fica reservada a eventos de alta prioridade, especialmente
`system.error`. Exceções de um assinante são registradas e não interrompem os
demais.

## Correlação

O `correlation_id` acompanha uma solicitação desde `voice.command` ou entrada da
UI, passando por `brain.*`, `planner.*`, `skill.*` e `tts.*`. Eventos criados por
um assinante herdam automaticamente a correlação que ele está processando.

## Tipos estáveis

Os nomes oficiais estão em `Isabella.Events.EventType`: `system.*`, `voice.*`,
`brain.*`, `skill.*`, `planner.*`, `tts.*`, `memory.*`, `context.updated`,
`vision.*` e `ui.message`. Novos nomes devem preservar o formato
`categoria.acontecimento` e ser adicionados ao enum antes do uso.

## Configuração e diagnóstico

`config/events.json` controla ativação, tamanho da fila, número de workers,
reserva de prioridade e tempo de encerramento. `bus.diagnostics()` informa total
de assinaturas, fila atual, eventos processados, falhas de assinantes, descartes
e latência média de publicação. `shutdown()` para novas publicações, drena a fila
até o limite configurado e encerra os workers.
