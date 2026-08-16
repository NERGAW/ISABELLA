# ISABELLA Protocol v1

## Overview

ISABELLA Protocol é um protocolo de aplicação transport-agnostic para futuras
instâncias e equipamentos. Ele define identidade, envelopes, capabilities,
commands, eventos, telemetria e versionamento sobre transportes existentes.

Ele não substitui TCP/IP, HTTP, WebSocket ou MQTT. Nesta fase não existe Node,
socket, broker, Android ou hardware real. A API REST continua funcionando como
antes e apenas anuncia suporte ao modelo `1.0` com `transport_enabled=false`.

## Versão

- Nome: `ISABELLA Protocol`
- `protocol_version`: `1.0`
- Limite padrão: 64 KiB por mensagem JSON UTF-8
- Profundidade máxima: 12 níveis

Versões são negociadas explicitamente. Uma versão incompatível recebe `ERROR`
com código `INCOMPATIBLE_VERSION` e a sessão não deve continuar silenciosamente.

## Message envelope

Toda mensagem contém exatamente:

```json
{
  "id": "msg-a1",
  "protocol_version": "1.0",
  "type": "HEARTBEAT",
  "source": "phone.1",
  "destination": "primary.1",
  "timestamp": "2026-08-15T20:00:00-03:00",
  "correlation_id": "flow-a1",
  "payload": {"status": "ONLINE", "sequence": 4}
}
```

Timestamps devem conter timezone. IDs possuem formato e tamanho limitados.
Campos ausentes ou desconhecidos no envelope são rejeitados.

## Tipos

- `HELLO`: identidade apresentada no início;
- `WELCOME`: identidade primária, versão aceita e heartbeat negociado;
- `HEARTBEAT`: status e sequência opcionais;
- `STATUS`: estado estruturado do Node;
- `CAPABILITIES`: identidade/capabilities atualizadas;
- `COMMAND_REQUEST`: Skill allowlisted e argumentos;
- `COMMAND_RESULT`: resultado estruturado;
- `EVENT`: evento explicitamente autorizado;
- `TELEMETRY`: capability e métricas específicas;
- `ERROR`: erro limitado e correlacionado;
- `GOODBYE`: encerramento da sessão futura.

## Node identity e capabilities

Identidade:

```json
{
  "node_id": "helmet.1",
  "node_type": "WEARABLE",
  "name": "Capacete principal",
  "protocol_version": "1.0",
  "capabilities": ["notifications", "imu"]
}
```

Tipos iniciais: `PRIMARY`, `COMPUTER`, `SMARTPHONE`, `HOME`, `EMBEDDED`,
`WEARABLE` e `PANEL`.

Capabilities são strings pequenas como `voice_input`, `voice_output`, `screen`,
`camera`, `gps`, `imu`, `sensors`, `display`, `notifications` e
`skill_execution`. Cada transporte futuro deve fornecer ao validador o conjunto
realmente disponível para aquele Node. Anunciar capability inexistente é erro;
nenhuma lista é confiada apenas porque veio do peer.

## HELLO e WELCOME

```json
{
  "type": "HELLO",
  "payload": {
    "identity": {
      "node_id": "phone.1",
      "node_type": "SMARTPHONE",
      "name": "Telefone",
      "protocol_version": "1.0",
      "capabilities": ["notifications"]
    }
  }
}
```

O `source` do envelope deve ser igual ao `node_id`. Após validar identidade,
versão e capabilities reais, o Primary responde `WELCOME` com sua identidade,
`accepted_version` e `heartbeat_seconds`. O intervalo permitido é de 5 a 30
segundos; nenhum heartbeat agressivo foi implementado.

O primeiro transporte concreto é WebSocket local, documentado em
`docs/TRANSPORT.md`. Ele não altera o envelope nem a negociação.

## Commands e Security

Um command transporta somente:

```json
{
  "skill_id": "applications.open",
  "arguments": {"name": "chrome"}
}
```

O gateway exige que o transporte já tenha autenticado o peer, valida o envelope,
reutiliza o schema do Skill Registry e chama `registry.execute()`. O fluxo é:

```text
Transport authentication → Protocol validation
→ Skill Registry → Security Policy → executor allowlisted
```

Campos `python`, `code`, `shell`, `command`, `executable`, `permissions`,
`confirmed`, `confirmation_id` e `risk_level` são proibidos. O protocolo não
possui confirmação embutida e nunca chama o sistema operacional diretamente.
Uma Skill crítica continua retornando `confirmation_required`; uma Skill
inexistente é rejeitada pelo Registry.

No WebSocket, `COMMAND_RESULT` inclui também `request_id`, ligando o resultado ao
ID do `COMMAND_REQUEST` além do `correlation_id` do envelope.

## Telemetria

Telemetria não usa um schema universal gigante:

```json
{
  "capability": "sensors",
  "metrics": {"temperature_c": 24.5, "battery_percent": 78}
}
```

O transporte fornece a allowlist de capabilities autorizadas. Métricas devem ser
JSON estruturado dentro dos limites globais. Imagens, blobs ou base64 não podem
ser embutidos; exigirão estratégia externa explícita em versão futura.

## Events

Eventos internos não são exportados automaticamente. Um `EVENT` só é válido se
seu nome estiver na allowlist explícita do transporte/sessão:

```json
{
  "event": "diagnostics.status_changed",
  "data": {"subsystem": "LLM", "status": "OFFLINE"}
}
```

Assinar `*` internamente não concede direito de exportação pelo protocolo.

## Error model

```json
{
  "code": "INCOMPATIBLE_VERSION",
  "message": "Protocol version is not supported",
  "request_id": "msg-a1",
  "details": {"supported": ["1.0"]}
}
```

Código, mensagem e quantidade de detalhes são limitados. Errors preservam o
`correlation_id` da requisição.

## Codec e versioning

`encode()` valida antes de serializar JSON compacto. `decode()` limita bytes
antes de parsear, exige UTF-8, rejeita NaN, valores não JSON, envelopes extras,
profundidade excessiva e tipos desconhecidos. `validate()` pode ser usado quando
o transporte já possui um modelo.

Mudanças incompatíveis exigirão nova versão negociada. Implementações v1 não
devem presumir suporte futuro nem continuar após erro de versão.
