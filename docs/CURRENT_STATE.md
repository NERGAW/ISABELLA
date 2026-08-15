# Estado atual da I.S.A.B.E.L.L.A.

Checkpoint validado em 15/08/2026 após a Fase 19.

## Arquitetura operacional

O `ApplicationRuntime` é o único coordenador usado por `main.py`, tanto em GUI
quanto em CLI. Ele ordena dependências, permite modo degradado, limita restart e
encerra Event Bus por último. O antigo `Interface.hud.run_gui()` agora é apenas
um wrapper do mesmo Runtime e não cria mais um lifecycle paralelo.

Fluxo atual:

```text
User
  ↓
Voice Input / HUD / CLI
  ↓
Router → Brain → Planner (quando composto) / Skill Registry
  ↓
Security Policy Engine (inclusive Skills compostas da Skill Forge)
  ↓
Executor allowlisted local / MCP Tool Registry / Research Manager
  ↓
Context + Memory + Event Bus
  ↓
HUD + TTS
```

Diagnostics consulta os componentes e o estado do Runtime. Voice, TTS, Vision e
LLM podem falhar isoladamente; Core, Skills locais, HUD/CLI e shutdown continuam.

## Componentes validados

- Core, Runtime e Event Bus interno thread-safe.
- Ollama `qwen3:1.7b`, Router e Planner determinístico.
- Skills allowlisted e Security central com confirmação única/expirante.
- Memory SQLite seletiva e Context volátil conservador.
- Faster Whisper `base`, CPU/int8 e wake word Isabella/Isabela.
- TTS Francisca Neural com fallback SAPI, cache e fila limitada.
- HUD PySide6 e CLI compartilhando o mesmo Runtime.
- Vision sob demanda para tela, janela e câmera sem captura contínua.
- Compreensão multimodal local sob demanda com `qwen3-vl:2b`, imagem reduzida e
  contexto apenas textual/estruturado.
- Diagnostics para 16 subsistemas, métricas e histórico limitado.
- MCP modular com `stdio` e Streamable HTTP oficiais, desligado de conexões por
  padrão e sempre subordinado ao Security Policy Engine local.
- Research sob demanda com fontes consultadas, citações, fetch público protegido
  e cache curto limitado.
- Skill Forge declarativa para compor Skills existentes, com validação estática,
  sandbox sem efeitos, testes obrigatórios, aprovação e habilitação separadas.
- Logs com rotação de 5 MB e três backups.

## Auditoria da Fase 15

Itens removidos por evidência:

- imports não usados `re` em Context e `RiskLevel` no Registry;
- `config/personality.json`, sem consumidor em código, testes ou documentação;
- campos duplicados de idioma/wake word em `system.json`; `voice.json` é a fonte
  única do pipeline de voz;
- coordenação GUI duplicada em `hud.run_gui()`.

Itens deliberadamente mantidos:

- `Core.diagnostics.technical_snapshot`, ainda coberto como API de compatibilidade;
- screenshots e relatórios em `data/`, pois são artefatos explícitos do usuário,
  não temporários comprovadamente descartáveis;
- `pycaw`, usado por import lazy em `system.set_volume`.

Todas as filas possuem limite. Deques de métricas/histórico possuem `maxlen`.
Vision remove temporários no cleanup/shutdown, câmera fecha após health check,
TTS remove WAV temporário e SQLite/HTTP/Event Bus têm fechamento explícito.

## Limites conhecidos

- Conversa depende do Ollama local e continua sendo o maior custo de latência.
- A primeira transcrição inclui carga do Whisper e eleva RAM temporariamente.
- Edge TTS depende de rede; SAPI permanece como fallback local.
- Reconhecimento de voz pode perder precisão em ambiente ruidoso.
- A aplicação é voltada ao Windows.

## Fora do escopo

Não existem servidores MCP configurados, reconhecimento facial, biometria, Nodes
ou contas externas conectadas neste checkpoint. Research não realiza login nem
navegação autônoma complexa. A Skill Forge não gera nem executa código arbitrário,
não instala dependências e não cria Skills automaticamente durante conversas.
