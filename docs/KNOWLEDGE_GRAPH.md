# Knowledge Graph da I.S.A.B.E.L.L.A.

O Knowledge Graph armazena relações estruturadas; Memory continua armazenando lembranças. A implementação usa SQLite local e não requer Neo4j ou qualquer servidor externo.

## Modelo e API

Entidades usam os tipos `SYSTEM`, `PROJECT`, `NODE`, `DEVICE`, `SKILL`, `APPLICATION`, `SERVICE`, `PERSON_REFERENCE` e `CONCEPT`. Relações suportam `USES`, `RUNS`, `CONNECTED_TO`, `BELONGS_TO`, `DEPENDS_ON`, `PREFERS`, `HAS_CAPABILITY`, `CONTROLS` e `RELATED_TO`.

A API oferece `add_entity`, `get_entity`, `find_entity`, `add_relation`, `remove_relation`, `neighbors`, `find_path` e `search_relations`. Relações exigem provenance e confidence entre 0 e 1. Duplicatas idênticas são idempotentes.

## Integrações

- o projeto ISABELLA é relacionado explicitamente a Python e Ollama;
- Skills de aplicações/navegador recebem relações `CONTROLS` conservadoras;
- Nodes descobertos e suas capabilities são registrados por eventos, incluindo `CONNECTED_TO` ao Primary PC;
- somente a preferência explícita `preferred_browser` é promovida de Memory para `USER_REFERENCE PREFERS APPLICATION`.

Não há migração geral da Memory, inferência ampla ou ingestão automática de resultados web. O Brain consulta apenas relações relevantes para perguntas relacionais e nunca envia o Graph inteiro ao LLM.

## Operação

O banco fica em `data/knowledge/isabella_knowledge.db`. Criação e remoção publicam `knowledge.entity_created`, `knowledge.relation_created` e `knowledge.relation_removed`. Diagnostics expõe saúde e contagens, enquanto o Control Center apresenta um viewer tabular simples.

O teste de performance consulta um Graph pequeno 200 vezes dentro de um limite de 250 ms no ambiente de teste.
