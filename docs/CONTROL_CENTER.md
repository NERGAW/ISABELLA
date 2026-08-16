# I.S.A.B.E.L.L.A. Engineering Control Center

O Control Center é uma janela técnica separada do HUD operacional. Ele consulta as interfaces existentes do Runtime, Diagnostics, Skills, Security, Memory, Event Bus, Automations, Scheduler, Nodes e Home; não substitui nenhum desses componentes.

## Abertura

- menu **Ferramentas > Control Center** no HUD;
- comando seguro “Isabella, abra o Control Center”;
- `python main.py --control-center`.

O painel inicia em modo somente leitura e atualiza o resumo a cada 1,5 segundo. Eventos recentes usam um buffer limitado e o leitor de logs lê somente o final de `logs/isabella.log`.

## Segurança

Credenciais, tokens e chaves são removidos dos dados apresentados. A interface não aceita código, não edita Skills e não concede confiança a Nodes. Excluir memória, alterar automações, cancelar tarefas e reiniciar serviços exige ativação explícita do modo administrativo. O Core nunca é reiniciado pelo painel; ações continuam limitadas às APIs validadas dos subsistemas.

Falhas da janela são isoladas do Runtime e do HUD. Fechar o Control Center não encerra a ISABELLA.
