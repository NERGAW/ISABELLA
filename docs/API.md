# API local segura

A API REST fornece uma fronteira controlada para integrações futuras sem expor o
Core, filesystem, Python ou comandos arbitrários. O fluxo é sempre:

```text
HTTP → Bearer token → validação/rate limit → Brain
     → Skill Registry → Security Policy → resultado estruturado
```

Foi usado `ThreadingHTTPServer` da biblioteca padrão. Cinco rotas simples não
justificam FastAPI, Uvicorn e seus lifecycles/dependências adicionais. O servidor
é um serviço opcional e degradável do Runtime; uma falha de bind não derruba o
Core.

## Configuração segura

`config/api.json` usa por padrão:

```json
{
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8765,
  "allow_remote": false,
  "authentication_required": true,
  "cors_allowed_origins": []
}
```

Com `allow_remote=false`, qualquer endereço que não seja loopback é rejeitado.
Nenhuma regra de firewall é criada. `0.0.0.0` nunca é escolhido automaticamente.
Se `allow_remote` for habilitado manualmente no futuro, autenticação não pode ser
desativada.

CORS começa vazio e wildcard é proibido. O corpo possui limite configurável e a
API não oferece WebSocket nesta fase.

## Token local

No primeiro startup habilitado, um token aleatório é salvo em
`data/api_token.txt`. `data/` é ignorado pelo Git e o token nunca aparece em
`config/api.json`, logs, respostas ou documentação. Para configurar um cliente
local, copie o valor diretamente desse arquivo e envie:

```text
Authorization: Bearer SEU_TOKEN_LOCAL
```

Não use o texto de exemplo como token real. `/health` é público para probes
locais; todas as outras rotas exigem token.

## Endpoints

- `GET /health`: saúde mínima da API local;
- `GET /status`: estado do Runtime;
- `POST /command`: texto encaminhado ao Brain;
- `GET /skills`: metadados públicos das Skills autorizadas, sem executores;
- `GET /diagnostics`: snapshot técnico autenticado.

Exemplo local:

```http
POST /command HTTP/1.1
Host: 127.0.0.1:8765
Authorization: Bearer SEU_TOKEN_LOCAL
Content-Type: application/json
X-Request-ID: cliente-123

{"text":"abra o Chrome"}
```

Resposta:

```json
{
  "success": true,
  "request_id": "cliente-123",
  "message": "Aplicativo aberto.",
  "status": "completed",
  "data": {"response_type": "single_skill", "skill_results": []},
  "error": null
}
```

O servidor gera um request ID quando o cliente não fornece um. O mesmo valor é
usado como correlation ID no Event Bus e no processamento do Brain.

## Security e rate limit

`POST /command` aceita somente um objeto com o campo string `text`; campos extras,
texto vazio e corpos grandes são rejeitados. O rate limit por IP é especialmente
aplicado a essa rota, com histórico e quantidade de clientes limitados em memória.

A API não aceita confirmation ID nem endpoint de confirmação. Se um comando
crítico chegar, o Registry retorna `confirmation_required`; a chamada remota não
executa nem confirma silenciosamente a ação.

Eventos publicados: `api.request_received`, `api.request_completed`,
`api.auth_failed` e `api.rate_limited`. Diagnostics informa status, requests,
erros e falhas de autenticação sem revelar credenciais.

## Fora do escopo

Não há exposição à internet, abertura de firewall, arquivos locais, execução de
código, aplicativo Android, WebSocket ou API externa nesta fase.

