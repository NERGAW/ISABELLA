# Segurança de dispositivos

A ISABELLA autentica Nodes por identidade Ed25519. `node_id`, nome e IP são apenas atributos; não concedem confiança. A chave privada PEM é criada e mantida exclusivamente no dispositivo, em `data/`, ignorada pelo Git. O Primary persiste somente a chave pública, estado e permissões.

## Pareamento

O modo de pareamento começa desligado. A Skill `nodes.start_pairing` (CAUTION) abre uma janela de 120 segundos. Um Node desconhecido pode então enviar apenas sua chave pública e permissões solicitadas. O Primary mostra um código de seis dígitos, temporário e single-use; após a comparação humana, a aprovação grava o dispositivo como `TRUSTED`. Código errado, expirado ou reutilizado falha.

Estados: `UNTRUSTED`, `PAIRING`, `PENDING_APPROVAL`, `TRUSTED` e `REVOKED`. Um dispositivo confiável não é administrador: `send_commands` e demais permissões são definidas localmente e continuam sujeitas ao Security Policy Engine. `nodes.list_trusted` é SAFE; `nodes.revoke` é CRITICAL e exige confirmação. Revogação invalida imediatamente a chave pública registrada.

## Conexão e replay

No handshake WebSocket, o Node assina com Ed25519 o próprio `node_id`, timestamp e message ID. O servidor verifica a assinatura, uma janela curta de timestamp e consumo único do message ID. Alterar identidade, permissões, trust ou configuração invalida a prova ou é ignorado. A chave privada e o código nunca entram em logs.

O bind padrão continua `127.0.0.1`. A configuração mantém acesso remoto desativado; uma futura conexão remota deve exigir `wss://`/TLS e validação de certificado antes de ser exposta.

Eventos: `pairing.started`, `pairing.requested`, `pairing.approved`, `pairing.failed`, `node.trusted`, `node.revoked`, `auth.success` e `auth.failed`. Diagnostics informa confiáveis, pendentes, revogados e falhas de autenticação sem alertas falados contínuos.
