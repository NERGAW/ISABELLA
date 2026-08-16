# ISABELLA Home Gateway

O fluxo é `Core → Skill específica → Security → Home Gateway → MQTT → dispositivo allowlisted`. O LLM nunca acessa MQTT ou hardware diretamente. O gateway roda inicialmente no PRIMARY_PC como `HOME`/`home.gateway`; dispositivos maiores continuam usando ISABELLA Protocol.

## MQTT

O adaptador usa `isabella/home/{device_id}/heartbeat`, `/telemetry` e `/command`, QoS 1. Ele não inclui broker nem abre firewall. O padrão é MQTT desligado e `127.0.0.1:1883`; quando ativado, somente loopback ou LAN privada é aceita e credenciais vêm de `ISABELLA_MQTT_USERNAME`/`ISABELLA_MQTT_PASSWORD`. Broker público é rejeitado. TLS pode ser habilitado localmente.

Descoberta MQTT não concede confiança. Uma mensagem só é aceita se `device_id` estiver em `config/home.json`, a capability tiver sido declarada e o payload possuir exatamente capability, value, unit e timestamp ISO-8601 com timezone.

## Dispositivos e segurança

Cada dispositivo declara ID, nome, tipo, status, capabilities reais, heartbeat, gateway, metadata e risk level. Relé genérico não pode ser SAFE e qualquer atuação em dispositivo `CRITICAL` é bloqueada integralmente nesta V1. Nesta fase só existem `virtual_light_1` e `virtual_temperature_sensor`; nenhum motor, aquecedor, fechadura ou carga real é configurado.

Skills: `home.light_on` (CAUTION), `home.light_off` (SAFE), `home.get_temperature` (SAFE) e `home.get_device_status` (SAFE). Não existe `home.execute_raw_command`. Dispositivo desconhecido, offline ou sem capability é negado. Automations pode reagir a `home.telemetry` para notificar; ações críticas continuam passando pela política.

## ESP32 de laboratório

O exemplo [esp32_isabella_test.ino](../examples/esp32_isabella_test/esp32_isabella_test.ino) limita comandos ao LED integrado e publica temperatura simples/heartbeat. SSID e credenciais são configurados localmente. O exemplo não controla cargas externas.

Diagnostics expõe gateway, broker, devices online/offline e erros de telemetria. Context mantém apenas IDs online e últimos valores, e o HUD mostra uma linha HOME compacta.
