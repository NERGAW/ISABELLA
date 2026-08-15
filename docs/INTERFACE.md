# Interface gráfica da I.S.A.B.E.L.L.A.

A HUD é a interface padrão do projeto. Execute na raiz:

```powershell
.\.venv\Scripts\python.exe main.py
```

Para manter a experiência anterior no terminal:

```powershell
.\.venv\Scripts\python.exe main.py --cli
```

## Uso

- **Enter** envia o texto; **Shift+Enter** cria uma nova linha.
- **Microfone** ativa ou pausa a captura de voz.
- **Parar voz** interrompe imediatamente a fala da Isabella.
- O cabeçalho mostra o estado atual: `IDLE`, `LISTENING`, `TRANSCRIBING`,
  `THINKING`, `PLANNING`, `EXECUTING`, `SPEAKING` ou `ERROR`.
- O painel lateral apresenta a saúde do núcleo, LLM, entrada e saída de voz,
  Skills e planejador. `DEGRADED` significa que a interface segue utilizável,
  mas aquele serviço está indisponível.

Processamento do cérebro, verificações e ações rodam fora da thread da janela.
Os resultados retornam por sinais Qt, preservando a responsividade. A conversa
visível e o modelo em memória são limitados às 100 mensagens mais recentes.
Ações críticas continuam exigindo confirmação explícita em uma caixa de diálogo.

## Estrutura

- `hud.py`: janela, componentes e estilo visual centralizado.
- `controller.py`: ponte por sinais entre apresentação e serviços.
- `workers.py`: tarefas sequenciais no `QThreadPool`.
- `models.py`: estados e mensagens independentes dos widgets.

Para ambientes sem tela, use `QT_QPA_PLATFORM=offscreen` ao executar os testes.
