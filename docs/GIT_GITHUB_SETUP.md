# Git, GitHub e VS Code

Configuração de versionamento utilizada pelo projeto:

- Git: `2.54.0.windows.1`
- GitHub CLI: `2.97.0`
- Branch principal: `main`
- Repositório: `NERGAW/ISABELLA`
- Remote: `https://github.com/NERGAW/ISABELLA.git`
- VS Code: integração Git nativa e extensão oficial `GitHub.vscode-pull-request-github`

## Comandos básicos

Verificar o estado:

```powershell
git status
```

Preparar e registrar alterações:

```powershell
git add .
git commit -m "descrição da alteração"
```

Enviar a branch principal:

```powershell
git push origin main
```

Verificar o remote:

```powershell
git remote -v
```

As credenciais são gerenciadas pelo Git Credential Manager e pelo GitHub CLI. Tokens, senhas e códigos de autenticação não devem ser armazenados no projeto.
