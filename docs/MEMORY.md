# Memória da I.S.A.B.E.L.L.A.

A memória é local, seletiva e dividida entre contexto temporário da sessão e
registros persistentes explícitos. Ela não salva automaticamente toda a conversa.

## Tipos

- `WORKING_MEMORY`: últimas mensagens da sessão, somente em RAM.
- `PREFERENCE`: escolhas persistentes, como navegador preferido.
- `FACT`: fatos que o usuário pediu explicitamente para guardar.
- `PROJECT`: contexto persistente de um projeto.
- `EPISODIC`: evento conscientemente registrado pelo usuário.

## Persistência

Os registros persistentes usam SQLite em
`data/memory/isabella_memory.db`. O diretório é criado em runtime e `data/*`
está no `.gitignore`, portanto o banco não é publicado no GitHub.

O schema guarda `id`, tipo, chave, valor, origem, datas de criação e atualização,
confiança, tags, metadata e estado ativo. A combinação tipo/chave é única: uma
nova gravação atualiza a memória existente e preserva seu identificador. Exclusão
é lógica (`active=0`). Todas as consultas usam parâmetros SQLite.

## Operações

- `remember()`: cria ou atualiza uma memória persistente permitida.
- `recall()`: consulta por chave e tipo opcional.
- `forget()`: desativa uma chave.
- `search()`: busca limitada por chave, tags e palavras-chave.
- `list_memories()`: lista registros ativos para uso técnico controlado.
- `clear_working_memory()`: apaga apenas o contexto RAM da sessão.

Exemplos de linguagem natural:

```text
Lembre que meu navegador preferido é Chrome.
Qual navegador eu prefiro?
Abra meu navegador.
Esqueça qual é meu navegador preferido.
Lembre que o projeto atual se chama ISABELLA.
```

## Working Memory e contexto

A Working Memory mantém no máximo 30 mensagens e descarta automaticamente as
mais antigas. Ela nunca é gravada no SQLite. Antes de uma conversa, o Brain pode
enviar ao Ollama apenas as mensagens recentes e até cinco memórias persistentes
relevantes. O banco completo nunca é colocado no prompt.

O limite de mensagens e de retrieval é configurado em `config/memory.json`.
Busca semântica e embeddings permanecem desativados nesta fase.

## Integração com Skills

Antes de executar `applications.open` para “Abra meu navegador”, o Brain consulta
`PREFERENCE/preferred_browser`. Se existir, passa o valor resolvido para a Skill.
Sem preferência, pede ao usuário que escolha, sem inventar uma memória.

## Privacidade e segurança

- Banco e Working Memory são locais.
- Somente trechos relevantes podem entrar no contexto do Ollama configurado.
- Conteúdo não é inferido e persistido pelo LLM.
- Senhas, tokens, API keys, secrets, private keys, cartões e credenciais são
  recusados mesmo quando o usuário pede explicitamente.
- A memória não é criptografada e não deve ser tratada como cofre.
- Logs registram identificador, tipo e contagem, nunca o valor completo.

Se o SQLite estiver indisponível ou corrompido, Memory assume `ERROR`; a HUD e a
conversa básica continuam funcionando. O status aparece no painel de subsistemas.

## Desempenho medido

Em 100 operações locais com poucas memórias:

| Operação | Média | Máximo |
|---|---:|---:|
| Escrita/update | 3,969 ms | 6,133 ms |
| Recall por chave | 0,130 ms | 0,294 ms |
| Retrieval por palavras | 0,390 ms | 0,681 ms |

