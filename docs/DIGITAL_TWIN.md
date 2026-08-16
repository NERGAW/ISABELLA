# Digital Twin da I.S.A.B.E.L.L.A.

O Digital Twin é uma projeção virtual, somente leitura, dos componentes físicos e lógicos conhecidos. Ele representa estado, capabilities, telemetria, saúde e referências ao Knowledge Graph; não é uma simulação física e não executa comandos.

## Entidades e fontes

São suportados `PRIMARY_PC`, `MOBILE`, `HOME_GATEWAY`, `ESP32`, `SENSOR`, `APPLICATION` e `SERVICE`. `HELMET` fica reservado e não possui implementação real.

O manager consome eventos de Nodes, Home, Runtime e Event Bus. CPU, RAM, memória do processo, uptime e saúde de serviços chegam pelo relatório já produzido por Diagnostics, evitando uma segunda coleta. Dispositivos Home são sincronizados pelo HomeManager e telemetria mantém seu timestamp original. IDs do Knowledge Graph são apenas referenciados, sem duplicar relações.

## Atualização e stale

Não há polling próprio. Cada atualização publica `twin.created`, `twin.updated`, `twin.stale` ou `twin.offline`. Dados acima do limite configurado são marcados `STALE` e não são apresentados como atuais. O histórico avançado não é mantido nesta versão.

Context recebe somente resumos derivados: Twins online, capabilities disponíveis e mapa de estados. O Brain responde perguntas como “Quais dispositivos estão online?”, “Como está o celular?” e “Qual é o estado do sistema da casa?” usando esse estado real.

## Segurança e interface

O Digital Twin não expõe edição de estado nem controle de dispositivo. Ações continuam exclusivamente em Skills autorizadas pelo Security Policy Engine. O Control Center possui um viewer tabular simples, sem 3D, engine gráfica ou taxa de atualização elevada.
