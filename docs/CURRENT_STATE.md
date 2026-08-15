# Estado atual da I.S.A.B.E.L.L.A.

## O que funciona

- Core com configuração JSON, lifecycle e logs estruturados.
- HUD PySide6 responsiva e CLI opcional com `--cli`.
- Entrada por texto e voz sem bloquear a interface.
- Faster Whisper `base`, CPU/int8, carregado uma única vez sob demanda.
- Wake word `Isabella` e `Isabela`.
- TTS Francisca Neural com cache em memória, interrupção e fallback Maria/SAPI.
- Ollama local com `qwen3:1.7b`, conexão HTTP reutilizada e tolerância offline.
- Router determinístico para conversa, ação simples e comando composto.
- Planner determinístico somente para comandos compostos, com dependências.
- Registry allowlisted, validação de argumentos e confirmação de ações críticas.
- Abrir/fechar aplicativos conhecidos, abrir sites/URLs, screenshot, volume,
  suspensão, reinicialização e desligamento conforme limites e confirmações.
- Estados e saúde de Core, LLM, voz, TTS, Skills e Planner na HUD.
- Shutdown coordenado de workers, TTS, microfone e sessão HTTP.
- Diagnóstico técnico interno por `Core.diagnostics.technical_snapshot`.
- Filas, histórico, cache e séries de métricas com limites definidos.
- Memória persistente SQLite seletiva, memória de sessão em RAM e recuperação
  limitada por chave, tags e palavras-chave.
- Preferência persistente de navegador integrada à abertura de aplicativos.

## Limites conhecidos

- O reconhecimento de voz ainda pode confundir palavras em ambientes ruidosos.
- A primeira transcrição paga o custo de carga do modelo Whisper.
- Conversas dependem do Ollama local e sua latência; offline, a Isabella informa
  indisponibilidade sem fechar.
- Edge TTS depende de rede; Maria/SAPI é o fallback local carregado sob demanda.
- A aplicação é atualmente voltada ao Windows.
- Ações desconhecidas ou fora da allowlist são rejeitadas.

## Deliberadamente fora do escopo atual

Não existem Vision, reconhecimento facial, Event Bus, MCP, Nodes,
Android, Arduino, ESP32 ou integração Home Assistant. A Fase 7 não adiciona
essas funcionalidades.
