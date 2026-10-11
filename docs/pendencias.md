# Controle de Pendências e Próximos Passos

Este documento centraliza as tarefas pendentes do projeto, divididas entre a **fila de implementação imediata** (o plano de 3 etapas em andamento) e as **melhorias para versões futuras**.

---

## 📌 1. Fila de Implementação Imediata

| Item | Descrição | Status | Onde será implementado |
|---|---|---|---|
| **Item 1** | **Jobs assíncronos para geração inicial**<br>- Polling via `202 Accepted` + `Location: /jobs/{id}`<br>- Status: `pendente`, `pesquisando`, `classificando`, `calculando`, `concluido`, `falhou`<br>- Cancelamento via `DELETE /jobs/{id}`<br>- Em memória com TTL (1 hora), sem alterar tabelas do Postgres. | ✅ **Concluído e Testado** *(aguardando commit)* | `src/jobs.py`, `src/main.py`, `tests/test_api.py`, `specs/adr/0005-...md` |
| **Item 2** | **Exportação de Cronograma**<br>- `GET /roadmaps/{id}/exportar/ics`: formato iCalendar RFC 5545 (`text/calendar`) para importar sessões de estudo no Google Agenda / Outlook / Apple Calendar.<br>- `GET /roadmaps/{id}/exportar/markdown`: formato Markdown (`text/markdown`) com checklist `[ ] Semana N: Cap X...`. | ⏳ **Pendente (Próximo passo)** | `src/main.py`, `tests/test_api.py`, `specs/api.md`, `specs/requisitos.md` |
| **Item 3** | **Dockerização Completa da API**<br>- Criar `Dockerfile` multi-stage com `python:3.12-slim` e `uv`.<br>- Criar `.env.example` com defaults para contêineres.<br>- Adicionar serviço `api` no `docker-compose.yml` conectado ao `db`, `ollama` e `searxng`.<br>- **Regra estrita:** não embutir modelos de LLM na imagem Docker. | ⏳ **Pendente** | `Dockerfile`, `.env.example`, `docker-compose.yml` |

---

## 🔮 2. Melhorias e Evoluções para o Futuro (Backlog)

Estas tarefas estão fora do escopo da versão atual, mas registradas para evolução do sistema:

1. **Acompanhamento de Progresso do Leitor:**
   - Adicionar endpoints para o usuário marcar capítulos como "concluídos" ou "em andamento".
   - Recalcular semanas restantes com base no ritmo real de estudo.

2. **Autenticação e Multi-Tenancy:**
   - Introduzir autenticação segura (ex.: JWT / OAuth2) para que cada usuário veja e gerencie apenas os seus próprios livros e roadmaps.

3. **Calibração Empírica das Constantes de Tempo:**
   - As constantes em `config.yaml` (minutos de leitura por página, pesos de tópicos) são estimativas heurísticas.
   - Alimentar `evals/estudo.csv` com dados reais de sessões de leitura de diferentes pessoas e rodar `uv run python -m evals.comparar_estudo` para calibração estatística contínua.

4. **Fila Persistente para Jobs (Escalabilidade):**
   - Atualmente, os jobs de geração de roadmap vivem na memória do processo (`src/jobs.py`) para respeitar o princípio de 3 tabelas no banco de dados.
   - Se o volume de usuários aumentar muito ou houver múltiplos nós de backend, migrar o gerenciador de jobs para uma fila distribuída leve (Redis / Celery / ARQ).

---

## 🗂️ 3. Onde as Mudanças Já Feitas Estão Documentadas

- **Decisão Arquitetural do Modo Assíncrono:** [`specs/adr/0005-jobs-assincronos-para-geracao-de-roadmap.md`](file:///home/mborges/LWH/learning/proj/personal_proj/roadmap-backend/specs/adr/0005-jobs-assincronos-para-geracao-de-roadmap.md)
- **Contrato HTTP da API (Endpoints e Respostas):** [`specs/api.md`](file:///home/mborges/LWH/learning/proj/personal_proj/roadmap-backend/specs/api.md)
- **Rastreabilidade de Requisitos Funcionais:** [`specs/requisitos.md`](file:///home/mborges/LWH/learning/proj/personal_proj/roadmap-backend/specs/requisitos.md) (RF-API-08)
- **Guia Didático do Projeto:** [`docs/mapa-do-projecto.md`](file:///home/mborges/LWH/learning/proj/personal_proj/roadmap-backend/docs/mapa-do-projecto.md)
- **Limitações Gerais da Versão:** [`README.md`](file:///home/mborges/LWH/learning/proj/personal_proj/roadmap-backend/README.md) (seção *Limitações conhecidas*)
