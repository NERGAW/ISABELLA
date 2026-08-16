# Agentes especializados da I.S.A.B.E.L.L.A.

A ISABELLA continua sendo uma única assistente. O `Brain` permanece como entrada principal e usa um Orchestrator leve apenas para encaminhar tarefas específicas aos subsistemas que já existem. Não há agentes autônomos, threads próprias, runtimes duplicados ou execução em background.

## Especializações

- `SYSTEM_AGENT`: Windows, aplicativos, estado do sistema e Diagnostics; sem shell arbitrário.
- `RESEARCH_AGENT`: pesquisa, avaliação de fontes e citações.
- `VISION_AGENT`: tela, câmera e contexto visual; sem reconhecimento facial.
- `MEMORY_AGENT`: recall, preferências e projeto; nunca salva segredos automaticamente.
- `ENGINEERING_AGENT`: projeto, logs, Git, código e diagnóstico técnico; não autoriza alterações destrutivas.

Cada contrato declara capacidades, Skills permitidas e apenas os campos de contexto necessários. A execução real continua nas APIs de Skills, Research, Vision, Memory e Security já existentes.

## Seleção e delegação

Regras determinísticas resolvem pedidos evidentes sem chamada LLM adicional. Conversa comum não é delegada. Uma tarefa como “veja o erro na tela e pesquise uma solução” passa sequencialmente por Vision e Research, com no máximo três saltos. O resultado volta ao Brain para uma única resposta ao usuário.

O Orchestrator publica `agent.started`, `agent.completed`, `agent.failed` e `agent.delegated`. Uso, falhas, latência média e atividade recente ficam em buffers limitados e aparecem em Diagnostics e no painel Agents do Control Center.

## Performance

O caminho anterior permanece intacto para conversa comum: a seleção determinística retorna sem delegação e não chama o LLM. O benchmark automatizado executa 1.000 seleções simples em menos de 100 ms no ambiente de teste (menos de 0,1 ms por seleção); tarefas delegadas registram sua latência real em Diagnostics para comparação contínua.

## Segurança

Agentes não alteram Risk Levels, não confirmam ações críticas, não recebem credenciais e não contornam o Skill Registry ou o Security Policy Engine. Falha de uma especialização é isolada e não derruba o Core.
