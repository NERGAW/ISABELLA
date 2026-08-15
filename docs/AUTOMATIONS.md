# Automations Engine

O Automations Engine executa regras declarativas quando recebe eventos internos.
Ele não concede autonomia irrestrita: toda ação referencia uma Skill registrada e
passa novamente pelo `SkillRegistry` e pelo `Security Policy Engine`.

## Arquitetura

```text
Event Bus → trigger → condições simples → cooldown/loop guard
          → Skill Registry → Security Policy → executor autorizado
          → resultado + eventos + SQLite próprio
```

O engine assina o Event Bus; não existe polling nem scheduler nesta fase. Regras
ficam em `data/automations.db`, separado da Memory semântica.

## Modelo e triggers

Cada automação possui ID, nome, estado, trigger, condições, ações, timestamps,
última execução, contador, `max_runs` opcional, cooldown, owner e source.

Triggers suportados:

- `EVENT`: corresponde ao nome exato de um evento;
- `STATE_CHANGE`: usa um evento de mudança de estado e condições sobre o payload;
- `MANUAL`: executado somente por `run_manual()`.

Não há trigger temporal. Agendamento permanece fora desta fase.

As condições permitidas são `equals`, `not_equals`, `greater_than`,
`less_than`, `contains` e `exists`. Campos aninhados usam caminho pontuado, sem
expressões, scripts ou linguagem programável.

## Exemplo estruturado

```json
{
  "id": "workspace.open_github",
  "name": "Abrir GitHub no modo Engenharia",
  "enabled": false,
  "trigger": {
    "type": "STATE_CHANGE",
    "event": "context.active_application_changed"
  },
  "conditions": [
    {"field": "active_application", "operator": "equals", "value": "vscode"}
  ],
  "actions": [
    {"skill": "browser.open_url", "arguments": {"url": "https://github.com"}}
  ],
  "cooldown_seconds": 30,
  "owner": "user",
  "source": "manual_structured"
}
```

Especificações produzidas futuramente pelo Brain devem passar por
`create_automation()` antes de persistência. A criação conversacional não foi
implementada automaticamente.

## Segurança

- Skills e argumentos são validados antes de salvar.
- O engine nunca aceita Python, shell ou executáveis arbitrários.
- A Skill `automations.create` é `CRITICAL`, exigindo confirmação explícita antes
  de persistir qualquer conjunto dinâmico de ações.
- `automations.enable` é `CAUTION`; listagem e desativação são `SAFE`.
- Cada ação é reavaliada pela política na hora da execução.
- O engine nunca envia `confirmed`, confirmation ID ou outra pré-autorização.
- Uma ação `CRITICAL` retorna `confirmation_required` e a automação falha sem
  executá-la. Automações não respondem à própria confirmação.

## Proteção contra loops e falhas

Cooldown é aplicado antes de executar. O par automação/correlação bloqueia
reentrada, e a cadeia compartilhada é interrompida ao exceder
`max_chain_depth` (padrão 5). O cache de correlações é limitado.

Falhas são registradas e publicadas, sem repetição infinita. Retry automático
está desabilitado nesta fase. `max_runs` encerra regras de uso limitado e regras
desabilitadas nunca executam, nem manualmente.

## CRUD, eventos e diagnóstico

O manager oferece `create_automation`, `update_automation`, `enable`, `disable`,
`delete`, `list`, `get` e `run_manual`. As Skills públicas oferecem criação,
listagem, habilitação, desabilitação e exclusão.

Eventos: `automation.created`, `triggered`, `started`, `completed`, `failed`,
`disabled` e `loop_blocked`. Diagnostics expõe total, habilitadas, execuções,
falhas, última execução e acessibilidade do storage. A HUD pode consumir o total
ativo futuramente; nenhum dashboard novo foi adicionado.

## Limites

Não há scheduler, Nodes, reinício automático de serviço crítico, polling,
navegação autônoma ou pré-confirmação de ações críticas.

