# roadmap-backend

API que gera cronogramas de estudo personalizados a partir de arquivos de  sumários de livros.

**Spec de implementação:** @roadmapapi-spec.md

Leia a spec antes de qualquer alteração em `src/`. Os princípios não-negociáveis
da seção 1 valem para todo o código.

## Ambiente

- Python 3.12.9, gerenciado com **uv** em modo `--no-package`
- Instalar dependências **sempre** com `uv add` — nunca `pip install`, nunca editar
  `pyproject.toml` à mão
- PostgreSQL 16+ em contêiner, com extensão `vector`
- Ollama como serviço sidecar no `docker-compose.yml` (não instalar no host)

## Comandos

```bash
uv run uvicorn src.main:app --reload    # dev server
uv run pytest                           # testes (LLM mockado)
uv run pytest -m eval                   # evals com LLM real — ver checkpoints
uv run alembic upgrade head             # migrations
docker compose up -d                    # Postgres + Ollama
```
Obs: Se tiver algum problema com o uv e seu ambiente, consultar o arquivo config_uv.md, nele há possiveis soluções para problemas.

## Estilo de código

- **Enxuto.** Sem repository pattern sobre SQLAlchemy, sem DTO que espelha model,
  sem camada de serviço que só repassa chamada. Função de 3 linhas usada uma vez
  é inline.
- Async em todo o caminho de I/O (FastAPI, SQLAlchemy 2.0 async).
- Pydantic v2 para validação de entrada e de saída do LLM.
- Type hints obrigatórios. Sem `Any` fora de fronteira com JSONB.
- Erro de domínio vira exceção própria, capturada em handler do FastAPI.
  Nada de `try/except` genérico enterrado no meio da lógica.

## Banco de dados

- **Três tabelas:** `book`, `roadmap`, `chunk`. Não criar outras sem discussão.
- FK sempre `int` → `id`. Sem chave composta, sem tabela de junção, sem herança,
  sem `relationship()` bidirecional, sem cascade elaborado.
- Dado de formato instável (capítulos, classificação, cronograma) vai em **JSONB**,
  não em tabela normalizada.
- Toda mudança de schema passa por Alembic.

## Regras de arquitetura

- **LLM só julga, Python calcula.** Nenhuma aritmética sai do modelo. O LLM
  classifica (tipo, densidade, nível, linguagens, pesos); horas, semanas e dias
  são calculados em Python puro.
- Saída de LLM sempre via structured output com JSON schema + validação Pydantic.
  Nunca parsear string ou remover fences de markdown.
- Constantes de cálculo vivem em `config.yaml`, nunca hardcoded no código ou no prompt.
- Extração ambígua retorna `422` com mensagem clara. Nunca produzir número
  silenciosamente errado.

## Checkpoints — parar e perguntar

Não decidir sozinho nestes pontos. Parar, explicar o trade-off e aguardar resposta:

1. **Escolha de modelo do Ollama** (classificador ou embedding) — avisar antes de
   fixar qualquer modelo, para pesquisa prévia.
2. **Antes de rodar testes com LLM real** (`pytest -m eval`) — avisar para troca de
   modelo/esforço do lado Anthropic.
3. **Mudança em constantes de `config.yaml`** — é calibração, não implementação.
4. **Criar tabela nova ou adicionar FK** — ver regras de banco acima.

## Fora de escopo (v1)

OCR de capa / multimodal, Gemini como segundo provider, autenticação e persistência
de progresso. Não implementar, não deixar hook preparado.
