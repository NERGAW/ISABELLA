# Modos operacionais da I.S.A.B.E.L.L.A.

Os modos são perfis reversíveis de comportamento. Eles alteram prioridades, disponibilidade de integrações, perfil de interface/voz e nível de diagnóstico, mas nunca alteram `RiskLevel`, credenciais ou as decisões do Security Policy Engine.

## Modos

- **NORMAL**: perfil padrão.
- **ENGINEERING**: respostas técnicas, Diagnostics detalhado, logs, VS Code, Git e Control Center em destaque.
- **PRIVACY**: processamento local, sem Research nem MCP externo e sem envio de Vision para cloud.
- **OFFLINE**: Ollama, STT, TTS, Memory e Skills locais; pesquisa, MCP remoto e provedores cloud são bloqueados.
- **HOME**: prioriza Home Gateway, Automations, Scheduler e telemetria local.
- **MOBILE**: perfil compacto e conciso, aplicado também a requisições cuja origem seja `MOBILE_NODE`.

`HELMET` e `EMERGENCY` estão apenas reservados para evolução futura e não podem ser ativados nesta versão.

## Uso

Use “Isabella, modo Engenharia” ou a Skill segura `system.set_mode`. O HUD mostra o modo atual discretamente e o Control Center oferece um seletor. O estado inicial vem de `config/modes.json`; trocas durante a sessão não são persistidas automaticamente.

Toda transição publica `mode.changing`, `mode.changed` ou `mode.failed`. A sugestão de ENGINEERING ao abrir VS Code permanece desabilitada por padrão e nenhuma automação muda o modo sem configuração explícita.

## Segurança e rede

A política de modo é avaliada antes das integrações, e a política de segurança continua sendo avaliada normalmente na execução da Skill. Modos não podem desabilitar Security nem confirmação crítica. PRIVACY/OFFLINE selecionam TTS local e impedem Research, MCP remoto e Vision cloud; nenhum segredo é modificado.
