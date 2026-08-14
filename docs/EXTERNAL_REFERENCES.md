# Referências externas

Nenhum código foi reutilizado diretamente. A implementação da ISABELLA é própria e usa apenas conceitos arquiteturais gerais observados nas referências.

## isair/jarvis

- URL: https://github.com/isair/jarvis
- Commit analisado: `d22ed8b975792842dc09e49861f31a39cbb302a6`
- Licença: licença própria para uso não comercial; uso comercial requer licença separada.
- Componentes estudados: abstração de provider, integração HTTP com Ollama, prompts, seleção de ferramentas, planner, configuração, logging, timeout e fallbacks.
- Inspiração conceitual: separar o provider do fluxo de decisão, limitar timeouts, retornar falhas controladas e manter planejamento estrutural.
- Código reutilizado diretamente: nenhum.

## llm-guy/jarvis

- URL: https://github.com/llm-guy/jarvis
- Commit analisado: `f278f5c0e5dbfeb60b6a4e0d9fc3f4c768db6df4`
- Licença: não declarada no repositório analisado; portanto nenhum código foi reutilizado.
- Componentes estudados: configuração do Qwen 3 1.7B no Ollama, system prompt, tool calling, logging e tratamento geral de erros.
- Inspiração conceitual: adequação do modelo pequeno para baixa latência e respostas concisas.
- Código reutilizado diretamente: nenhum.
