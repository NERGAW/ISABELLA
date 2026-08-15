# Benchmark e estabilidade — Fase 15

Medições em 15/08/2026, Windows, CPU, Faster Whisper `base/int8`, Ollama
`qwen3:1.7b` e TTS `pt-BR-FranciscaNeural`. Resultados variam com hardware,
cache, rede e carga do sistema.

## Resumo antes/depois

| Métrica | Referência anterior | Checkpoint atual |
|---|---:|---:|
| Startup serviço aquecido | 2.313–2.416 ms | 2.279,10 ms (média de 10 ciclos reais) |
| Startup GUI frio completo | 4.693,33 ms | preservado; Runtime isolado adiciona 1,324 ms |
| LLM curto | 16.325,80 ms | 14.223,74 ms |
| STT falado aquecido | 1.641,03 ms | 201,17 ms de inferência silenciosa; 2.663,50 ms com carga |
| TTS primeiro áudio | 956,83 ms | 886,87 ms |
| Router controlado | 0,019 ms | 0,0396 ms |
| Planner controlado | 0,049 ms | 0,1356 ms |
| Stress | 50 interações | 100 interações |

Os cenários de STT anterior/atual não são diretamente equivalentes: o anterior
era fala real aquecida; o atual separa inferência silenciosa e carga inicial.

## Latências atuais

| Operação | Resultado |
|---|---:|
| Startup real médio, 10 ciclos | 2.279,10 ms |
| Shutdown real médio, 10 ciclos | 414,67 ms |
| Router | 0,0396 ms |
| Planner | 0,1356 ms |
| Validação de Skill | 0,0106 ms |
| Execução de Skill controlada | 0,0572 ms |
| Ollama, resposta “OK” | 14.223,74 ms |
| STT, inferência de 1 s silencioso | 201,17 ms |
| STT, carga + inferência | 2.663,50 ms |
| TTS, síntese | 1.301,05 ms |
| TTS, time-to-first-audio | 886,87 ms |
| TTS, inicialização + síntese | 2.535,51 ms |
| Vision, tela 1920×1080 | 150,36 ms |
| Memory retrieval | 0,7559 ms |
| Context resolution | 0,0703 ms |
| Event Bus publish | 0,01065 ms |

## RAM, CPU e threads

Stress controlado misturando texto, voz simulada, conversa, Skills, Planner,
Memory, Context, Vision controlada, confirmação crítica e falhas simuladas:

| Interações | RAM | Threads |
|---:|---:|---:|
| 0 | 35,230 MB | 3 |
| 25 | 35,469 MB | 3 |
| 50 | 35,480 MB | 3 |
| 100 | 35,488 MB | 3 |
| Após shutdown | — | 1 |

Crescimento total: 0,258 MB, sem tendência contínua relevante e com working
memory estabilizada no limite de 30 mensagens. O Event Bus processou 1.073
eventos, fila final 0, falhas 0 e descartes 0.

Runtime real ocioso: 73,69 MB, 6 threads e 0,8% de CPU; após shutdown, 1 thread.

| Operação real | CPU do processo |
|---|---:|
| Idle com Voice/TTS ativos | 0,8% |
| STT | 96,8% |
| Cliente LLM | 0,11% |
| TTS síntese | 51,15% |
| Vision captura | 83,1% |

A CPU do Ollama servidor é externa ao processo Python; 0,11% mede apenas o
cliente Isabella durante a requisição.

## Stress e falhas

As 100 interações concluíram em 254,25 ms no ambiente controlado, média de 2,543
ms. Foram exercitados texto, fonte de voz simulada, conversa, Skills, Planner,
Memory, Context, Vision controlada, ação inválida e confirmação crítica.

Matriz isolada aprovada: Ollama offline, microfone em erro, TTS offline, Vision
offline/câmera degradada, SQLite indisponível, Event Bus sobrecarregado, Skill
inválida, Planner acima do limite e serviço required do Runtime em falha. Nove
testes passaram em 0,50 s sem crash global.

## Shutdown e recursos

Dez ciclos reais completos com Ollama, microfone e TTS:

- 10/10 iniciaram `ONLINE` e encerraram com sucesso;
- threads: 1 antes e 1 depois;
- temporários `isabella_*` novos: 0;
- nenhum `python.exe`/`pythonw.exe` órfão;
- RAM final do processo de teste: 74,27 MB;
- microfone, TTS, HTTP, SQLite e Event Bus encerrados explicitamente.

## Requirements auditados

| Dependência | Uso comprovado |
|---|---|
| requests | cliente HTTP do Ollama |
| Pillow | screenshot e captura de tela |
| psutil | processos, contexto e Diagnostics |
| pycaw | ajuste de volume, import lazy |
| faster-whisper | STT local |
| numpy | áudio, STT e playback |
| sounddevice | captura e reprodução de áudio |
| edge-tts | voz neural principal |
| pyttsx3 | fallback SAPI |
| PySide6 | HUD e workers |
| av | decodificação de áudio em memória |
| opencv-python | câmera sob demanda |
| pytest (dev) | suíte automatizada |

Nenhuma dependência foi removida porque todas possuem uso direto comprovado.

## Reprodução

O stress controlado pode ser repetido com:

```powershell
.\.venv\Scripts\python.exe tools\checkpoint_benchmark.py
```

