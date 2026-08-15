# Segurança da I.S.A.B.E.L.L.A.

O Security Policy Engine é a autoridade central para execução de Skills. O LLM,
Router, Planner, Context, HUD e Voice podem solicitar ações, mas nenhum deles
autoriza a execução.

## Fronteiras de confiança

- Texto do LLM e conteúdo de prompts são sempre não confiáveis.
- Cada etapa do Planner é avaliada separadamente pelo Policy Engine.
- `confirmed=True`, a palavra “confirmado” e instruções no prompt não têm
  autoridade.
- Confirmações aceitas vêm apenas de entrada real do usuário pelo HUD, CLI ou
  pipeline de voz e devem apontar para um `ConfirmationRequest` pendente.
- `config/security.json` é configuração local carregada pelo Core; nenhuma Skill
  oferece escrita nesse arquivo.

## Risco e decisões

Os níveis existentes são `SAFE`, `CAUTION` e `CRITICAL`. A configuração mapeia
cada nível para `ALLOW`, `CONFIRM` ou `DENY`. Ações `CRITICAL` nunca podem ser
configuradas como `ALLOW`; desligar, reiniciar, suspender e agendar desligamento
exigem confirmação explícita ou podem ser totalmente negadas.

Permissões `READ`, `WRITE` e `DELETE` estão reservadas para futuras operações de
filesystem. `WRITE` e `DELETE` são classificadas como sensíveis para que uma
integração futura não ignore a política.

## Confirmações

Um pedido contém ID aleatório, Skill, cópia dos argumentos, risco, criação,
expiração e ID da solicitação original. Ele:

- expira após o tempo definido, 30 segundos por padrão;
- só autoriza a mesma Skill com os mesmos argumentos;
- é removido ao confirmar ou cancelar;
- só pode ser consumido uma vez;
- não é herdado por “faça novamente” nem por outra etapa de um plano.

Na voz, “sim” só confirma quando existe um pedido pendente no controlador. Fora
desse contexto, não executa uma ação crítica.

## Restrições permanentes atuais

Não existem Skills genéricas `execute_shell`, `execute_python`,
`execute_powershell`, `eval` ou `exec`. Os poucos subprocessos internos usam
comandos fixos e argumentos validados. `browser.open_url` aceita somente HTTP e
HTTPS; `file:`, `javascript:`, `data:` e outros esquemas são recusados. A memória
rejeita automaticamente senhas, tokens, API keys, chaves privadas e credenciais.

## Eventos e logs

O Engine publica `security.allowed`, `security.confirmation_required`,
`security.denied`, `security.confirmed` e `security.expired`. Payloads contêm
somente identificadores e motivos técnicos controlados, nunca segredos. O nível
de log é configurável, mas argumentos e conteúdo do usuário não são registrados
pela política.
