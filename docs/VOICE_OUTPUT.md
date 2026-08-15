# Voice Output

```text
BrainResponse / SkillResult.message → TTSManager → provider → áudio
                                      ├── Edge Neural (principal)
                                      └── SAPI5 local (fallback)
```

## Providers e voz

- Principal: `edge-tts`, online, voz feminina `pt-BR-FranciscaNeural`.
- Fallback: SAPI5/`pyttsx3`, offline, voz `Microsoft Maria Desktop - Portuguese(Brazil)`.
- Idioma: `pt-BR`.

Francisca foi escolhida pelo usuário após comparação auditiva com Thalita Multilingual Neural e Maria Desktop. A lista oficial da Microsoft identifica Francisca como voz feminina pt-BR e oferece o estilo `calm`. Maria é confiável e offline, mas possui naturalidade inferior à voz neural.

## Configuração

A seção `tts` de `config/voice.json` controla providers, voz, rate, pitch, volume, streaming, cache e limites de fila. Chaves e tokens não são usados nem armazenados.

O Edge fornece chunks de áudio e permite medir time-to-first-audio, mas esta implementação acumula o MP3 em memória antes da decodificação para garantir reprodução estável com PyAV/PortAudio. Por isso `streaming` permanece desativado na configuração desta fase. Nenhum WAV neural temporário é criado.

## Fila, cache e threading

O `TTSManager` possui uma única thread de reprodução e uma fila limitada a 10 respostas. Quando cheia, a resposta mais antiga é descartada. Frases de até 100 caracteres podem entrar em um cache LRU limitado a 32 itens. Providers são inicializados uma vez e encerrados no shutdown.

`stop()` interrompe o player atual e limpa falas pendentes. Enquanto o estado é `SPEAKING`, o listener entra em proteção e descarta captura; ao final ele volta a `LISTENING`. O teste real com uma frase contendo “Isabella”, “Chrome” e “Discord” produziu zero comandos falsos.

## Pronúncia

O texto visual não é alterado. Uma cópia destinada à fala aplica um dicionário pequeno para ISABELLA, WhatsApp, GitHub, Ollama, Faster Whisper e as siglas CPU, GPU, RAM, USB, GPS, HUD e IA. Chrome, Discord, Steam, Visual Studio Code, Windows e Python usam a pronúncia natural do provider.

## Métricas observadas

Com Francisca Neural no teste de autoeco:

- time-to-first-audio: aproximadamente 1.052 ms;
- síntese: aproximadamente 1.419 ms;
- reprodução total da frase: aproximadamente 4.759 ms.

Na simulação de indisponibilidade do provider principal, Maria assumiu automaticamente e o manager permaneceu em `READY`.

## Teste de vozes

```powershell
python tools/test_isabella_voices.py
```

O utilitário compara Francisca, Thalita e Maria com as mesmas frases e grava métricas em `data/voice_test_results.json`, ignorado pelo Git.

## Troubleshooting

- Sem internet: o Edge falha de forma controlada e Maria assume.
- Sem voz local Maria: instale o pacote de fala pt-BR do Windows ou configure outra voz SAPI.
- Sem áudio: confirme o dispositivo de saída padrão e teste `sounddevice`.
- Fila atrasada: use `tts.stop()`; respostas antigas não são acumuladas indefinidamente.
- Falha total: o estado TTS passa a `ERROR`, mas respostas continuam aparecendo em texto.
