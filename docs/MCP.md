# Integração MCP

## Arquitetura

MCP é uma camada externa opcional. Ele não substitui Core, Runtime, Skills locais,
Security ou Event Bus.

```text
Servidor MCP
    -> MCPClient (SDK oficial)
    -> MCPManager
    -> MCPToolRegistry
    -> Skill mcp.<server>.<tool>
    -> SkillRegistry
    -> SecurityPolicyEngine
    -> chamada externa
```

O `ApplicationRuntime` controla o serviço `MCP`, depois de `Skills` e `Security`.
No desligamento, conexões e subprocessos MCP são encerrados antes de Intelligence e
Core. Com `auto_connect: false` e nenhum servidor configurado, nenhuma thread,
conexão ou subprocesso MCP é criado.

## Transportes

São suportados somente transportes do SDK oficial:

- `stdio`: subprocesso local iniciado e encerrado pelo SDK;
- `streamable_http`: transporte HTTP atual do protocolo.

SSE legado e protocolos proprietários não são implementados.

## Configuração de servidores

O arquivo `config/mcp.json` nasce sem servidores e não conecta automaticamente.

Exemplo local sem segredo:

```json
{
  "id": "local_tools",
  "name": "Local Tools",
  "transport": "stdio",
  "command": "python",
  "enabled": true,
  "trusted": false,
  "timeout": 30,
  "metadata": {
    "args": ["server.py"],
    "cwd": "C:/caminho/seguro"
  }
}
```

Exemplo Streamable HTTP:

```json
{
  "id": "internal_api",
  "name": "Internal API",
  "transport": "streamable_http",
  "url": "https://example.invalid/mcp",
  "enabled": true,
  "trusted": false,
  "timeout": 30,
  "metadata": {}
}
```

`trusted` é informação operacional e nunca concede autorização.

## Segredos

Tokens, senhas e chaves não podem ser armazenados em `mcp.json`. A carga da
configuração rejeita campos de segredo.

Para `stdio`, passe somente nomes de variáveis existentes no ambiente:

```json
"environment_variables": {
  "SERVICE_TOKEN": "ISABELLA_SERVICE_TOKEN"
}
```

Para Streamable HTTP, cabeçalhos podem ser derivados do ambiente:

```json
"headers_from_environment": {
  "Authorization": "ISABELLA_MCP_AUTHORIZATION"
}
```

Os valores são resolvidos apenas no momento da conexão e não são publicados no
Event Bus nem inseridos em resultados.

## Ferramentas e segurança

Uma ferramenta `echo` descoberta no servidor `local_tools` vira
`mcp.local_tools.echo`, na categoria separada `mcp`.

- risco ausente ou inválido: `CAUTION`;
- hint destrutivo do protocolo: `CRITICAL`;
- metadata de risco válida pode declarar `SAFE`, `CAUTION` ou `CRITICAL`;
- a decisão final sempre pertence ao `SecurityPolicyEngine` local;
- confirmações booleanas externas não são aceitas;
- chamadas diretas ao `MCPManager.call_tool()` também passam pelo `SkillRegistry`.

O servidor fornece capacidades, não autorização.

## Eventos e diagnóstico

Eventos publicados:

- `mcp.server_connected`;
- `mcp.server_disconnected`;
- `mcp.tool_started`;
- `mcp.tool_completed`;
- `mcp.tool_failed`.

Diagnostics informa se MCP está habilitado, servidores registrados/conectados,
ferramentas disponíveis e falhas recentes. O histórico é limitado a 50 entradas.

## Testes

`tests/test_mcp.py` cobre registro, conexão, desconexão, descoberta, timeout,
servidor indisponível, ferramenta inválida, negação de segurança e shutdown. Há
também um round-trip real descartável por `stdio`, usando o SDK oficial e
`tests/fixtures/mcp_stdio_server.py`. Nenhuma conta externa é acessada.

Validação manual da Fase 16: conexão, descoberta, chamada e shutdown aprovados;
nenhum `python.exe` permaneceu órfão. A construção e `start()` do manager
desabilitado mediram aproximadamente 7,1 microssegundos por ciclo em 1.000 ciclos,
sem criação de thread ou cliente MCP.
