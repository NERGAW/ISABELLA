# Desempenho e estabilidade

Medições realizadas em 14/08/2026 no Windows, CPU, Whisper `base/int8`, Ollama
`qwen3:1.7b` e voz `pt-BR-FranciscaNeural`. Valores variam conforme cache,
hardware, rede e duração da frase.

## Startup

| Componente | Medição |
|---|---:|
| Configuração | 0,43–0,60 ms |
| Core | 2,15–3,39 ms |
| Construção da GUI | 7,95–26,01 ms |
| Inicialização do objeto LLM | 0,89–1,31 ms |
| Voice input | 2,51–4,16 ms aquecido; 381,82 ms a frio |
| TTS | 1,82–3,08 ms aquecido; 1.453,94 ms a frio |
| Ollama health check | 2.045,30 ms, assíncrono |
| Serviço pronto, incluindo health check | 2.313–2.416 ms aquecido |

### Runtime (Fase 14)

Teste real GUI offscreen com Core, Ollama, TTS, Voice, HUD e todos os serviços:
startup de 4.693,33 ms e shutdown de 291,12 ms, retornando ao mesmo total de
threads Python. A linha de base anterior de serviço pronto era 2.313–2.416 ms
aquecida e mantinha o health check do Ollama assíncrono; a medição nova inclui o
health check síncrono usado para classificar o startup e a inicialização fria do
Edge TTS (1.380,24 ms nesta execução), portanto não é uma comparação direta de
overhead.

Em 50 ciclos sintéticos com seis serviços sem I/O, o coordenador isolado mediu
1,324 ms para startup e 1,198 ms para shutdown em média. Este é o overhead
atribuível à ordenação, estados, threads limitadas e eventos do Runtime.

Antes da otimização, Edge e SAPI eram carregados sempre: 1.429,84 + 416,43 ms.
Agora somente o Edge principal inicia; o SAPI é carregado sob demanda. A carga
fria observada do TTS caiu de 1.846,27 para 1.245,31 ms no log comparável
(aproximadamente 32,5%). O health check não bloqueia a exibição da HUD.

Whisper permanece lazy: não participa do startup e é carregado na primeira
transcrição. A mesma instância é reutilizada por todos os comandos do listener.

## Modelos e latências reais

| Operação | Resultado |
|---|---:|
| Whisper: carga + primeira transcrição silenciosa | 2.005,23 ms |
| Whisper: somente transcrição silenciosa | 103,22 ms |
| Whisper: RAM antes/depois da carga | 59,07 / 226,32 MB |
| Whisper: CPU do processo durante a operação | 119,4% (multicore) |
| STT falado já medido (`base`) | 1.641,03 ms |
| Ollama: resposta curta | 16.325,80 ms |
| Edge: síntese | 1.189,87 ms |
| Edge: primeiro áudio | 956,83 ms |
| Edge: reprodução da frase de teste | 2.125,41 ms |

O maior gargalo atual é a geração conversacional no Ollama. Comandos simples
não passam pelo LLM: usam Router, Skill e resposta determinística. O Planner só
é chamado quando há duas ou mais cláusulas de ação.

## Teste de 50 interações

O teste misturou fontes texto/voz simulada, conversa, aplicativos, navegador,
screenshot, volume, falha de aplicativo e Planner, com executores controlados
para impedir efeitos reais no Windows.

| Marco | RAM | Threads |
|---|---:|---:|
| Inicial | 31,625 MB | 1 |
| 10 | 31,633 MB | 1 |
| 25 | 31,637 MB | 1 |
| 50 | 31,645 MB | 1 |

Variação total: aproximadamente 0,020 MB. Latência média controlada: 0,063 ms;
Router 0,019 ms; Planner 0,049 ms. Não houve crash, congelamento ou crescimento
de threads.

## Threads e filas

| Nome/origem | Função | Lifecycle |
|---|---|---|
| `MainThread` / Python + Qt | UI e sinais | processo inteiro |
| `IsabellaVoiceCapture` | captura do microfone | `start_voice` → `stop` |
| `IsabellaVoiceProcessing` | STT e wake word | `start_voice` → `stop` |
| `IsabellaTTS` | fila, síntese e reprodução | `start_tts` → `shutdown` |
| `QThreadPool` (máximo 1) | cérebro e health check | controller → fechamento |

Cinco ciclos reais iniciaram com 4 threads Python e voltaram sempre a 1 depois
do fechamento. Não restaram processos Python nem streams presos.

- Voice: fila máxima 2; overflow descarta a nova fala com warning.
- TTS: fila máxima 10; overflow descarta a resposta mais antiga com warning.
- HUD: apenas um worker por vez; mensagens limitadas a 100.
- Caches e métricas: TTS 32 entradas; séries de latência/métricas 200 entradas.

## Dependências auditadas

| Dependência | Uso direto |
|---|---|
| `requests` | cliente Ollama (`Intelligence/llm.py`) |
| `Pillow` | screenshot (`Skills/system.py`) |
| `psutil` | aplicações e diagnóstico técnico |
| `faster-whisper` | STT lazy (`Voice/stt.py`) |
| `numpy` | buffers de áudio e STT |
| `sounddevice` | microfone e reprodução |
| `edge-tts` | voz neural principal |
| `pyttsx3` | fallback SAPI offline |
| `PySide6` | HUD e workers |
| `av` | decodificação do áudio em memória |
| `pytest` (dev) | testes automatizados |

`pycaw` é carregado sob demanda pela Skill `system.set_volume`. `av` foi
declarado diretamente, pois o código o importa e não deve depender de instalação transitiva.

## Observabilidade

Com debug ativo, `PERFORMANCE` registra `request_id`, fonte, Router, LLM,
Planner, Skill e total. `tts_ms=queued` indica corretamente que a reprodução é
assíncrona; as métricas completas de TTS são registradas pelo serviço ao terminar.
Logs ruidosos de `asyncio`, `comtypes`, `httpcore`, `httpx`, `urllib3` e
`faster_whisper` ficam em WARNING.
