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

## Multimodalidade

O Ollama local informou para `qwen3:1.7b` as capacidades `completion`, `tools` e
`thinking`. Não declarou `vision`. O modelo principal não foi trocado.

Ao perguntar “O que está aparecendo na minha tela?”, a Isabella realiza a
captura e informa claramente que o modelo atual não possui capacidade multimodal.
`multimodal_model` permanece `null` em `config/vision.json` como opção separada
para uma fase futura. Não existe upload silencioso.

## Privacidade e escopo

As capturas permanecem locais. Vision nunca envia imagens ao LLM em interações
comuns e não possui provider cloud. A câmera só é aberta após pedido explícito.

Facial recognition is intentionally outside the current ISABELLA Core scope.

Não existem cadastro, identificação, autenticação, biometria ou banco de faces.

