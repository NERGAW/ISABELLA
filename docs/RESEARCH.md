# Pesquisa web e fontes externas

## Decisão de pesquisa

Research é um subsistema externo opcional. Ele não substitui o LLM local e não
transforma toda pergunta em busca.

O Router usa a intenção `research` quando o pedido:

- solicita explicitamente pesquisa, consulta ou fontes;
- depende de termos temporais como hoje, atual, últimas ou notícias;
- pergunta por versão atual/recente;
- pede verificação na internet.

`ResearchManager.should_search(question, local_sufficient=False)` também permite
ao chamador indicar que o conhecimento local é insuficiente. Perguntas atemporais,
como explicações conceituais, continuam no fluxo `conversation` do Ollama.

## Arquitetura

```text
User / Voice / HUD
        ↓
Router → Intent.RESEARCH
        ↓
ResearchManager
        ├─ SearchProvider configurável
        ├─ WebFetcher público e limitado
        ├─ Source records
        ├─ sumarização pelo Ollama local
        └─ citações verificáveis
        ↓
BrainResponse.sources → HUD / CLI / TTS
```

O Runtime controla `Research` como serviço dependente de Intelligence. Diagnostics
informa provider, cache e falhas recentes. A HUD usa o estado simples `SEARCHING`
durante a consulta e mostra as fontes no texto da resposta.

## Provider

O contrato `SearchProvider` desacopla o manager do mecanismo de busca. A
configuração inicial usa `duckduckgo_html`, um endpoint público sem chave:

```json
{
  "provider": "duckduckgo_html",
  "search_endpoint": "https://html.duckduckgo.com/html/",
  "max_results": 3
}
```

Providers futuros devem implementar `search(query, limit)` e `health_check()`.
Quando um provider exigir credencial, `api_key_environment_variable` deve conter
somente o nome da variável de ambiente; chaves não pertencem ao JSON.

## Fetch seguro

Somente páginas públicas `http` e `https` são aceitas. O fetch bloqueia:

- `file`, `javascript` e demais esquemas;
- credenciais embutidas na URL;
- localhost, nomes `.local`, IPs privados, loopback e redes especiais;
- redirects para destinos bloqueados;
- conteúdo não textual;
- mais de três redirects;
- páginas acima do limite configurado.

Cada redirect é validado antes da próxima requisição. O download é transmitido em
blocos e interrompido ao atingir `max_page_bytes`.

## Conteúdo não confiável e prompt injection

Texto web é sempre DATA, nunca instrução. Scripts, estilos e elementos não textuais
são removidos. Padrões explícitos de prompt injection são neutralizados e cada
fonte é delimitada como `DADOS WEB NÃO CONFIÁVEIS` no prompt de sumarização.

O texto de uma página não pode:

- alterar Security Policy ou permissões;
- mudar prompts do sistema;
- confirmar ações;
- chamar Skills, MCP ou ferramentas;
- solicitar segredos.

Somente o código local escolhe provider, ferramentas e políticas.

## Fontes e citações

Uma fonte só entra na resposta depois de a página ser baixada com sucesso. Cada
registro contém:

- `title`;
- `url`;
- `domain`;
- `retrieved_at` em UTC;
- `snippet`.

Resultados de busca que não puderam ser consultados são descartados e nunca
citados. Se o provider estiver offline e nenhuma página for consultada, a resposta
declara indisponibilidade e não inventa fontes. Se apenas o Ollama falhar, os
snippets e as fontes já verificadas são preservados.

## Cache e memória

O cache é LRU, limitado por `cache_max_entries` e expira por
`cache_ttl_seconds`. Ele existe apenas em memória e é apagado no shutdown.

Resultados web não são escritos automaticamente na memória SQLite. O histórico de
conversa e a working memory continuam transitórios conforme os contratos atuais.

## Eventos

- `research.started`;
- `research.completed`;
- `research.failed`.

Os eventos registram query, estado e quantidade de fontes, nunca o conteúdo bruto
das páginas.

## Validação da Fase 17

A suíte cobre decisão temporal/atemporal, pedido explícito, provider offline, URL
inválida, rede privada, prompt injection, cache, fallback do Ollama e respostas com
duas ou três fontes.

O teste real consultou três páginas públicas para `Python latest stable release
official`: duas em `python.org` e uma em `devguide.python.org`. Nenhuma conta,
login ou navegação autônoma foi utilizada.
