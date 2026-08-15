# Skill Forge

A Skill Forge cria somente composições declarativas de Skills locais já
registradas. Ela não gera Python executável, não usa shell, não instala pacotes,
não altera Core, Runtime ou Security e não faz commits automaticamente.

## Fluxo controlado

```text
Especificação estruturada
  → DRAFT → VALIDATING → TESTING → WAITING_APPROVAL
  → aprovação explícita → APPROVED
  → habilitação explícita → ENABLED
  → DISABLED ou REJECTED
```

A aprovação usa um token derivado do checksum. Aprovar e habilitar são operações
distintas: uma candidata `APPROVED` ainda não existe no Registry executável. Uma
alteração posterior na especificação invalida o checksum e bloqueia a ativação.

## Especificação

Cada candidata registra `id`, nome, descrição, entradas, saídas, risco,
dependências, passos e permissões. Os passos referenciam exclusivamente Skills
existentes e podem mapear argumentos a entradas com `{"$input": "nome"}`.
O risco final é o maior risco dentre todos os passos.

Exemplo conceitual seguro:

```json
{
  "skill_id": "custom.prepare_work",
  "name": "Preparar trabalho",
  "description": "Abre VS Code, Chrome e GitHub.",
  "steps": [
    {"skill_id": "applications.open", "arguments": {"name": "vscode"}},
    {"skill_id": "applications.open", "arguments": {"name": "chrome"}},
    {"skill_id": "browser.open_url", "arguments": {"url": "https://github.com"}}
  ],
  "permissions": ["launch_application", "open_public_url"]
}
```

## Validação, sandbox e aprovação

O validador rejeita IDs duplicados, wrappers redundantes, Skills desconhecidas,
referências de entrada inválidas, recursão, ausência de testes e excesso de
passos. A defesa estática também bloqueia `eval`, `exec`, imports de subprocesso
ou rede, acesso a segredos e tentativas de modificar Security ou Runtime.

O sandbox é um dry-run sem efeitos: resolve argumentos e reutiliza a validação de
schema do Registry, mas nunca chama executores. A prévia de aprovação mostra
passos, permissões, arquivo persistido, risco, dependências e token. Dependências
novas exigem aprovação separada e ainda assim não são instaladas pela Forge.

## Execução e persistência

Somente `enable()` registra a composição, na categoria `skillforge`. Cada passo
volta a passar pelo Registry e pelo Security Policy Engine; o servidor, modelo ou
especificação nunca são fonte de autorização. Artefatos versionados ficam em
`data/skills/` com timestamps, origem e SHA-256. O diretório é ignorado pelo Git,
exceto pelo marcador, e `export_for_commit()` apenas libera o caminho de uma
candidata já aprovada para uma ação manual posterior.

Eventos publicados: `skillforge.draft_created`, `validation_failed`,
`waiting_approval`, `approved`, `enabled` e `rejected`. Diagnostics informa totais
gerados, habilitados, desabilitados e validações com falha.

## Limites desta versão

- criação apenas por chamada manual estruturada;
- composições de Skills existentes são preferidas e são o único formato gerado;
- nenhum comando conversacional cria ou habilita Skills automaticamente;
- nenhum código arbitrário, download seguido de execução ou `pip` silencioso;
- nenhuma alteração automática de Core, Runtime, Security ou Git.
