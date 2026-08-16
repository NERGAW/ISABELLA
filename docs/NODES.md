# ISABELLA Node Architecture

## Conceito

Node é um participante identificado do ecossistema ISABELLA. A arquitetura
separa identidade, estado, capabilities e trust do transporte. Nesta fase existe
um único Node físico real: o computador atual, registrado como `PRIMARY_PC`.

Tipos preparados: `PRIMARY_PC`, `SECONDARY_PC`, `MOBILE`, `HOME`, `HELMET`,
`EMBEDDED`, `DISPLAY` e `SENSOR`. Esses tipos não significam que Android, ESP32,
capacete ou hardware foram implementados.

## Modelo e estados

Cada Node registra:

- `node_id`, nome e tipo;
- status e versão do Protocol;
- capabilities verificadas/declaradas;
- `last_seen` e `connected_at` timezone-aware;
- metadata limitada;
- estado de trust.

Status: `CONNECTING`, `ONLINE`, `DEGRADED`, `OFFLINE`, `DISCONNECTED` e `ERROR`.

Trust: `UNTRUSTED`, `PENDING`, `TRUSTED` e `REVOKED`.

## Primary Node e identidade persistente

No primeiro startup, um UUID aleatório é combinado ao prefixo `primary.` e salvo
em `data/node_identity.json`. O arquivo é local, ignorado pelo Git e recebe
permissões restritas quando o sistema permite. Hostname não é usado como ID e o
valor não é regenerado em cada inicialização.

Nodes conhecidos ficam em `data/nodes_registry.json`, também fora do Git. O
Primary local é o único registrado automaticamente como `TRUSTED`; outro Node
não pode se apresentar como `PRIMARY_PC`.

## Capabilities

O Primary detecta somente recursos presentes no startup:

- `text_input`;
- `voice_input` e `voice_output` quando seus componentes existem;
- `screen_capture` quando Vision confirma captura de tela;
- `camera_capture` somente quando o health check real confirma câmera;
- `local_llm` quando Ollama responde;
- `skill_execution`, `hud`, `memory` e `research` conforme componentes ativos.

Uma câmera indisponível não é anunciada. Capabilities de um Node futuro precisam
pertencer à allowlist local e ainda são apenas alegações de um peer não pareado.

## Manager e Registry

`NodeManager` implementa registro, remoção, consulta, listagem, mudança de status,
capabilities, heartbeat, detecção explícita de offline e revogação. O Registry é
thread-safe, pequeno e persistente. IDs duplicados e versões incompatíveis são
rejeitados.

Não há polling interno. Um transporte futuro poderá chamar `heartbeat()` e
`mark_offline()` em intervalo moderado. O Primary não é marcado offline por essa
rotina remota e é persistido como `DISCONNECTED` no shutdown.

## Trust e Security

Um Node descoberto começa `UNTRUSTED`, mesmo que envie `TRUSTED` em seus dados.
Pareamento não foi implementado. Nodes revogados não podem voltar online por
heartbeat e geram `node.revoked`.

Trust nunca substitui autenticação nem o Security Policy Engine. Mesmo um Node
`TRUSTED` continuará enviando comandos pelo fluxo Protocol → Authentication →
Registry → Security. O Node Manager não oferece execução de Skills.

## Protocol v1

O Primary produz `HELLO` JSON compatível com ISABELLA Protocol `1.0`. O tipo
`PRIMARY_PC` é mapeado para a identidade Protocol `PRIMARY`; tipos futuros usam o
tipo protocolar mais próximo sem alterar o tipo interno.

O transporte WebSocket local pode conectar Nodes simulados em `127.0.0.1`.
Nenhuma rede externa, broadcast, MQTT ou internet é aberta.

## Event Bus, Context, HUD e Diagnostics

Eventos: `node.discovered`, `node.registered`, `node.online`, `node.offline`,
`node.capabilities_changed` e `node.revoked`.

O Context expõe apenas dados leves: `active_nodes`, `primary_node` e
`available_capabilities`. Diagnostics informa total, online, offline e conjunto
de capabilities. A HUD adiciona somente o indicador `NODES: X ONLINE`.

## Simulador

Uso local:

```powershell
python tools/simulate_node.py --type MOBILE
```

O simulador usa Registry temporário, produz um HELLO v1, registra heartbeat e
permanece `UNTRUSTED`. A saída inclui `ACCESS_GRANTED=false`. Ele não conecta à
API automaticamente nem acessa o computador. Com flags explícitas, pode conectar
ao WebSocket local e usar token fornecido pelo desenvolvedor para testar uma
Skill SAFE.

## Roadmap futuro

Pareamento, autenticação de dispositivo, transportes, Android e hardware exigem
fases próprias e revisão de segurança. Não há reconhecimento facial nesta
arquitetura.
