# Skill System

O Skill System é a única camada autorizada a transformar pedidos estruturados em ações reais. O fluxo é:

```text
User → Router → Brain → SkillRequest/Plan → Registry → Validation → Executor allowlisted
```

O modelo nunca fornece Python, PowerShell, shell ou executáveis para essa camada.

## Registry e validação

Cada `SkillDefinition` declara identificador, nome, descrição, categoria, parâmetros, nível de risco, executor e estado habilitado. O `SkillRegistry` rejeita Skills desconhecidas ou desabilitadas, argumentos ausentes ou extras e tipos incorretos. Todo executor retorna um `SkillResult` estruturado, enquanto detalhes técnicos ficam no log.

## Níveis de risco

- `SAFE`: abertura de aplicativos/URLs e screenshot.
- `CAUTION`: fechamento normal de aplicativos e alteração de volume.
- `CRITICAL`: suspensão, desligamento, reinício e timer de desligamento.

Uma ação `CRITICAL` retorna `confirmation_required` sem executar. Apenas a resposta literal `sim`, digitada pelo usuário na CLI, autoriza a segunda chamada ao Registry.

## Aplicativos

`applications.open` resolve aliases usando `config/applications.json`, caminho configurado, PATH, locais padrão, Menu Iniciar e App Paths do Windows. Não há varredura integral do disco. `applications.close` solicita encerramento normal e não usa encerramento forçado automaticamente.

## Browser

`browser.open_url` aceita YouTube, Google, GitHub e URLs completas. Somente os esquemas `http` e `https` são permitidos.

## Sistema

`system.screenshot` grava em `data/screenshots/`, que permanece ignorado pelo Git. `system.set_volume` aceita inteiros de 0 a 100. As ações de energia usam comandos fixos internos e somente após confirmação explícita.

## Adicionando uma Skill

1. Implemente um executor determinístico que receba argumentos validados e retorne `SkillResult`.
2. Declare uma `SkillDefinition` com schema mínimo e nível de risco correto.
3. Registre a definição em `build_default_registry()`.
4. Adicione testes de validação, sucesso, erro e segurança.
5. Nunca aceite código ou comandos executáveis provenientes do modelo.
