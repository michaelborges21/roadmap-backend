# ADR 0005 — Jobs assíncronos para geração inicial de roadmap

- **Status:** aceito
- **Data:** 2026-10-11
- **Implementação:** `src/jobs.py`, `src/main.py`, contrato em `specs/api.md`, `specs/requisitos.md` (RF-API-08)

## Contexto

A primeira geração de roadmap para um livro exige busca no SearXNG, download de páginas web, geração de embeddings no pgvector e classificação via LLM local (`gemma4:12b`). Esse pipeline pode levar de 1 a 2 minutos.

Manter uma conexão HTTP síncrona aberta durante todo esse tempo traz problemas reais:
1. **Timeouts de rede**: navegadores, proxies reversos (como Nginx, Traefik, Cloudflare) e clientes HTTP possuem timeouts padrão de conexão ociosa (frequentemente 60 segundos), resultando em erro `504 Gateway Timeout` mesmo com o servidor funcionando normalmente.
2. **Experiência do usuário**: sem feedback intermediário, o usuário não sabe se o sistema está pesquisando na web, chamando o LLM ou se travou.
3. **Cancelamento desperdiçado**: se o usuário fecha a aba ou cancela a requisição HTTP síncrona, o processamento pode continuar rodando sem que o cliente receba o resultado.

## Decisão

Adotar suporte a **jobs assíncronos não-bloqueantes** com polling:

1. **Ativação transparente (RFC 7240 e query param)**:
   - Em `POST /books/{id}/roadmaps`, se o cliente enviar o header `Prefer: respond-async` ou a query string `?modo=async`, o endpoint retorna `202 Accepted` imediatamente, com cabeçalho `Location: /jobs/{job_id}` e corpo com o status inicial.
   - Requisições sem esse cabeçalho ou parâmetro continuam funcionando de forma síncrona (`201 Created`), garantindo **100% de compatibilidade retroativa** com clientes e testes existentes.

2. **Acompanhamento de status (`GET /jobs/{job_id}`)**:
   - Retorna o estado atual: `pendente`, `pesquisando`, `classificando`, `calculando`, `concluido` ou `falhou`.
   - Quando `concluido`, retorna `roadmap_id` e o objeto completo do roadmap gerado.
   - Quando `falhou`, retorna o detalhe do erro legível.

3. **Cancelamento (`DELETE /jobs/{job_id}`)**:
   - Permite abortar um processamento longo antes que consuma recursos desnecessários. Responde `204 No Content`.

4. **Armazenamento em memória (sem 4ª tabela no banco)**:
   - Para respeitar estritamente o princípio do projeto de **três tabelas** (`book`, `roadmap`, `chunk`), os metadados de jobs vivem em memória no processo da aplicação via `asyncio.Task` e dicionário com limpeza automática (expira após 1 hora).
   - Se o servidor for reiniciado durante um job, nada é corrompido: as etapas já salvas pelo pipeline no banco (`book.pesquisa` e `book.classificacao`) continuam preservadas, e uma nova tentativa de geração reutiliza o cache imediatamente em frações de segundo.

## Consequências

**Ganhos**
- Fim de timeouts de 60s em clientes e proxies.
- Feedback de progresso granular durante a espera.
- Compatibilidade total com a API existente.
- Sem novas tabelas, migrations ou sobrecarga de DDL.

**Custos**
- O cliente precisa fazer polling periódico (ex: a cada 1 a 2 segundos) em `GET /jobs/{id}` até o status ser `concluido`.
- Reinicialização do processo descarta a lista de jobs passados em memória (mitigado pelo fato de que o roadmap concluído já foi persistido na tabela `roadmap`).

## Alternativas rejeitadas

| Alternativa | Por que não |
|---|---|
| Mudar `POST /books/{id}/roadmaps` para 202 incondicional | Quebraria contrato de todos os clientes atuais e exigiria reescrever toda a suíte de testes. |
| Server-Sent Events (SSE) ou WebSockets | Complexidade desnecessária para operações ocasionais de 1 minuto; polling REST é universal e funciona em qualquer ambiente. |
| Tabela `job` no PostgreSQL | Viola a regra de arquitetura de 3 tabelas sem necessidade, já que o estado durável relevante (roadmap, pesquisa, classificação) já é persistido. |
| Celery / Redis / RabbitMQ | Infraestrutura externa pesada; viola o princípio de sidecars mínimos locais gratuitos. |
