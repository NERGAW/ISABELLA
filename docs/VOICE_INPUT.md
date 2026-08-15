# Voice Input

```text
Microfone → segmentação de fala → Faster Whisper → normalização → wake word
          → comando sem wake word → Brain → Registry/Planner
```

Esta fase implementa somente entrada. Não há TTS.

## Configuração

`config/voice.json` controla ativação, idioma, aliases, modelo, CPU/device, compute type, microfone, sample rate, VAD, timeout e logs de transcrição. O modelo escolhido é Faster Whisper `base`, CPU, `int8`, português e 16 kHz. O modelo é carregado de forma lazy uma única vez e reutilizado.

Na comparação real com a mesma frase, `small` reconheceu “Isabela, abra o Chrome” em 5.783 ms e `base` reconheceu “Isabela Abra o Chrome” em 1.641 ms. Como a precisão percebida foi equivalente e o `base` foi aproximadamente 3,5 vezes mais rápido, a configuração foi alterada explicitamente para `base`.

`input_device` pode ser `null` para o padrão, um ID ou parte do nome do microfone. Liste entradas válidas com:

```powershell
python -m Isabella.Voice.audio --list-devices
```

## Wake word

São aceitos somente `isabella` e `isabela`, opcionalmente precedidos por `ei`, no início da transcrição. Frases que apenas mencionam Isabella no meio não são encaminhadas ao Brain.

## Teste isolado

Grave e transcreva uma frase sem executar Skills:

```powershell
python -m Isabella.Voice.stt
```

Inicie voz e texto simultaneamente:

```powershell
python main.py
```

## Diagnóstico

- Microfone ausente: execute a listagem e configure ID/nome válido.
- Silêncio não reconhecido: confira o microfone padrão e ajuste `speech_threshold` com cuidado.
- CPU lenta: repita `python -m Isabella.Voice.stt --compare-base`; não reduza o modelo sem medir precisão em pt-BR.
- Falha no modelo: confirme acesso para o primeiro download e espaço no cache local.
- A fila possui limite de duas utterances; excedentes são descartados para evitar atraso crescente.

Falhas de microfone ou STT colocam Voice em `ERROR`, mas mantêm Core e CLI disponíveis.

## Limitações observadas

No teste final com `base`, nove segmentos foram transcritos com média de 1.678 ms (mínimo 1.650 ms, máximo 1.790 ms). Cinco continham wake word reconhecida, dois comandos completos foram encaminhados e uma Skill foi concluída. A principal limitação atual é a segmentação de uma pausa entre “Isabela” e o restante da frase; em alguns casos eles chegam como utterances separadas. Isso deve ser refinado em uma fase posterior com calibração de energia e janela de continuação, sem flexibilizar excessivamente a wake word.
