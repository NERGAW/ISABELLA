# Vision leve da I.S.A.B.E.L.L.A.

Vision é acionada somente mediante pedido explícito. Não existe captura contínua,
loop de vídeo, vigilância ou tracking em segundo plano. `continuous_capture` é
obrigatoriamente `false` na configuração.

## Captura de tela

`ScreenCapturer` usa Pillow `ImageGrab` e é a implementação comum de:

- tela principal ou monitor específico disponível;
- janela ativa, usando os limites obtidos pelas APIs Win32;
- Skill legada `system.screenshot`;
- Skills `vision.capture_screen` e `vision.capture_active_window`.

Uma captura retorna origem, timestamp, largura, altura, path, janela ativa e
metadata leve. Imagens maiores que 1920×1080 são reduzidas mantendo a proporção.
A lógica de screenshot não foi duplicada e nenhuma segunda biblioteca é usada
para tela.

## Câmera

`CameraCapture` usa `opencv-python`. A câmera:

- pode listar índices disponíveis;
- abre somente sob demanda;
- captura um único frame;
- fecha imediatamente após sucesso ou falha;
- não permanece em background.

No Windows, DirectShow é tentado primeiro e o backend padrão fica como fallback.
Isso reduziu a abertura real observada de 11.506,73 ms para 1.167,08 ms.

## VisionManager e Skills

O manager oferece `capture_screen`, `capture_active_window`, `capture_camera`,
`health_check` e `shutdown`. As Skills autorizadas são:

- `vision.capture_screen` — SAFE;
- `vision.capture_active_window` — SAFE;
- `vision.capture_camera` — SAFE e somente por solicitação explícita.

Falhas de tela ou câmera retornam um resultado controlado e não fecham a
Isabella. A HUD mostra apenas `VISION: ONLINE/OFFLINE`.

## Context Engine

Context recebe somente:

- `last_vision_source`;
- `last_capture_timestamp`;
- `last_capture_window`.

Pixels, buffers e caminhos completos não são armazenados no snapshot contextual.

## Temporários e memória

Com `temporary_images=true`, o manager mantém no máximo cinco capturas. A mais
antiga é removida ao exceder o limite; `cleanup()` remove uma captura após uso e
`shutdown()` remove todas as restantes. O teste real confirmou que os três
arquivos de tela, janela e câmera foram apagados.

Medições reais:

| Operação | Latência | Dimensão |
|---|---:|---:|
| Tela principal | 137,97 ms | 1920×1080 |
| Janela ativa | 181,35 ms | 1908×1080 |
| Câmera com DirectShow | 1.167,08 ms | 640×480 |

RAM do processo no teste combinado: 23,99 MB antes e 78,70 MB após carregar
Pillow/OpenCV e realizar as três capturas. A carga do módulo permanece no
processo, mas frames e arquivos temporários são liberados; não há fila infinita
nem captura recorrente.

## Pipeline multimodal

O modelo textual principal continua sendo `qwen3:1.7b`. Vision usa separadamente
o modelo local `qwen3-vl:2b`, que o catálogo oficial do Ollama declara como
Text+Image. O modelo visual não substitui o LLM de conversa.

```text
pedido explícito
  → Intent.VISION
  → captura de tela ou janela ativa
  → resize + JPEG em memória
  → qwen3-vl:2b no Ollama local
  → ScreenAnalysis validada
  → BrainResponse
  → HUD/TTS
```

Não existe loop, observador de tela ou inferência periódica. Cada análise nasce de
um pedido do usuário e a captura temporária é removida logo após a inferência.

## Configuração multimodal

`config/vision.json` define:

- `multimodal_enabled=true`;
- `vision_provider=ollama`;
- `vision_model=qwen3-vl:2b`;
- `max_image_size=1280`;
- `compression_quality=85`;
- `provider_local=true`;
- `allow_cloud_upload=false`;
- contexto de análise válido por 120 segundos.

O carregador rejeita captura contínua, limites inválidos, modelo ausente e provider
cloud sem permissão explícita. Nesta fase somente Ollama local é implementado.

## Resultado estruturado

`ScreenAnalysis` contém apenas campos sustentados pelo modelo:

- `summary` obrigatório;
- `visible_text`;
- `applications`;
- `errors`;
- `ui_elements`;
- `confidence` entre 0 e 1.

Os campos opcionais podem ser omitidos. Listas são limitadas e valores inválidos
são descartados. Confiança abaixo de 0,6 acrescenta um aviso de incerteza. O prompt
proíbe deduzir texto ilegível, objetos ocultos e detalhes ausentes; conteúdo visível
na tela é tratado como dado, nunca como instrução ou autorização.

## Contexto recente e “isso”

Após uma análise, Context recebe somente:

- `last_screen_summary`;
- `last_detected_error`;
- `last_visible_application`.

Pixels, base64 e caminhos não entram no Context ou Memory. Durante até 120 segundos,
perguntas como “O que significa esse erro?” podem reutilizar a análise estruturada,
sem nova captura. Depois do TTL, uma nova análise sob demanda é necessária.

## Performance multimodal real

Imagem sintética controlada, 900×500, contendo apenas “TESTE VISUAL / Nenhum erro”:

| Etapa | Latência |
|---|---:|
| Preprocessamento | 13,90 ms |
| Inferência local | 3.310,79 ms |
| Total | 3.324,96 ms |

O resultado repetiu somente os dois textos presentes, sem inventar aplicativo,
objeto ou erro.

Teste end-to-end da tela real, redimensionada para no máximo 1280 px:

| Etapa | Latência |
|---|---:|
| Captura | 134,01 ms |
| Preprocessamento | 76,21 ms |
| Inferência local | 17.288,53 ms |
| Total | 17.498,77 ms |

A diferença depende da complexidade visual e do aquecimento do modelo. A captura
temporária foi removida antes do retorno e não restaram arquivos no shutdown.

OCR separado não foi instalado: o modelo visual reconheceu o texto controlado. Uma
tela densa ainda pode produzir pequenas imprecisões; por isso a resposta expõe
confiança e deve declarar incerteza em detalhes ilegíveis.

## Privacidade e escopo

As capturas permanecem locais e só são enviadas ao Ollama em `localhost` após um
pedido visual explícito. Vision não possui provider cloud. A câmera só é aberta
após pedido explícito e não participa da compreensão de tela nesta fase.

Facial recognition is intentionally outside the current ISABELLA Core scope.

Não existem cadastro, identificação, autenticação, biometria ou banco de faces.
