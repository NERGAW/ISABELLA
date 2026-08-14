# Intelligence Core

```text
User Text
   ↓
 Router
   ↓
 Brain
   ├── Conversation → LLM
   ├── Single Skill → SkillRequest
   └── Multi Step → Planner
```

O provider desta fase é o Ollama via API HTTP local. Modelo, URL, temperatura, timeout, retry e limite de passos são centralizados em `config/intelligence.json`. O modelo inicial é `qwen3:1.7b`, escolhido por já estar instalado, ter baixa latência relativa e oferecer suporte a tools e saídas estruturadas no Ollama.

O Router usa regras e heurísticas rápidas para evitar chamadas extras ao modelo em pedidos evidentes. O Planner produz somente dados validados; não produz nem executa Python, shell ou ferramentas. Se o Ollama estiver indisponível, o Brain mantém a aplicação aberta e retorna uma mensagem controlada.

Nomes reservados para Skills futuras:

- `applications.open`
- `applications.close`
- `browser.open_url`
- `system.screenshot`
- `system.set_volume`
- `system.shutdown`
- `system.restart`
- `system.sleep`
- `system.shutdown_timer`
