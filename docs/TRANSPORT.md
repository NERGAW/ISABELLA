# ISABELLA real-time Node Transport

## Arquitetura

O primeiro transporte em tempo real usa WebSocket, mas todas as mensagens são
envelopes ISABELLA Protocol v1:

```text
WebSocket → Protocol decode/validation → Node Manager
          → authentication → Skill Registry → Security Policy
```

WebSocket fornece framing e conexão bidirecional; não substitui identidade,
tipos, versionamento ou validação do Protocol.

## Rede local segura

`config/transport.json` usa `127.0.0.1:8766` e `allow_remote=false`. Um endereço
não-loopback é rejeitado nessa configuração. A implementação não altera firewall,
não descobre dispositivos e não publica porta na internet.

A dependência `websockets>=15,<17` fornece framing e lifecycle maduros. Ping
automático da biblioteca fica desligado porque o heartbeat é uma mensagem
Protocol explícita.

## Handshake e conexão

O cliente conecta, envia `HELLO`, o servidor valida versão, identidade e
capabilities, registra/reconecta o Node como `UNTRUSTED` e responde `WELCOME`.
Uma conexão mantém ID próprio, Node ID, timestamps, endereço remoto, versão,
status, autenticação e contadores.

`node_id` não autentica. Um Node desconhecido pode participar do teste local como
`UNTRUSTED`, mas isso não concede commands. Node revogado, tipo conflitante ou
segunda conexão simultânea com o mesmo ID são rejeitados.

## Autenticação e commands

O transporte reutiliza o Bearer token local em `data/api_token.txt`. O cliente
envia `Authorization` no upgrade WebSocket. Sem token válido, handshake,
heartbeat, STATUS e TELEMETRY locais continuam possíveis, mas
`COMMAND_REQUEST` retorna `AUTHENTICATION_REQUIRED`.

Com autenticação, o gateway chama exclusivamente o Skill Registry. Toda ação
passa pelo Security Policy Engine; ação crítica retorna `confirmation_required`,
sem confirmação remota silenciosa. `COMMAND_RESULT` inclui `request_id`, sucesso,
status, mensagem, dados permitidos e erro.

## Heartbeat e reconnect

O intervalo padrão é 10 segundos. Uma janela perdida marca conexão e Node como
`DEGRADED`; a segunda marca `OFFLINE` e fecha. O cliente oferece `heartbeat()` e
`reconnect()` com backoff exponencial, quantidade e espera máxima limitadas.

## Events e telemetria

Somente eventos em `event_allowlist` são convertidos para Protocol `EVENT`. O
padrão contém `diagnostics.status_changed`; wildcard é proibido. TELEMETRY usa
schema capability-specific e não é salva como Memory.

## Limites e rate limit

Frames acima do limite configurado são bloqueados. O Protocol preserva seu limite
mais conservador de 64 KiB mesmo se WebSocket permitir 1 MiB. JSON malformado,
versão inválida, mensagem proibida e excesso de taxa causam erro e fechamento
controlado. O rate limit é por connection ID e possui histórico limitado.

## Runtime, eventos e diagnóstico

Transport é serviço opcional, dependente de Nodes, Skills, Security e Event Bus.
Falha de bind não derruba o Core.

Eventos: `transport.started`, `connection_opened`, `connection_closed`,
`message_received`, `message_sent` e `protocol_error`. Diagnostics informa
conexões, mensagens, erros, reconnects, host, porta e allowlist.

## Cliente e simulador

`WebSocketNodeClient` implementa `connect`, `disconnect`, `send`, `receive`,
`heartbeat` e `reconnect`.

```powershell
python tools/simulate_node.py --type MOBILE `
  --connect ws://127.0.0.1:8766 `
  --token-file data/api_token.txt
```

O simulador testa HELLO, WELCOME, HEARTBEAT, STATUS, `scheduler.list` e GOODBYE.
Sem token, não recebe autorização de command. Ele não é Android e não ganha
acesso automático ao computador.

## Fora do escopo

Não há pareamento seguro, certificados de dispositivo, Android, hardware,
internet, abertura de firewall ou reconhecimento facial.

