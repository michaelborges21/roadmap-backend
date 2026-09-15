# roadmap-backend

API que gera cronogramas de estudo personalizados a partir do **sumário** de um livro técnico.
Você envia o sumário (PDF, `.txt`/`.md`) ou a capa (JPEG/PNG), informa sua senioridade e quantas
horas por semana tem, e recebe quais capítulos estudar, quantas horas cada um leva e em que semana.

O modelo de IA (local, via Ollama) **julga** — tipo do livro, nível e esforço de cada capítulo — e
**pesquisa fatos na web** com fonte, como o número de páginas. O Python **calcula** horas e semanas.
Nenhuma conta sai do modelo.

- Spec de implementação: [`roadmapapi-spec.md`](roadmapapi-spec.md)
- Requisitos (cada um ligado ao teste que o verifica): [`specs/requisitos.md`](specs/requisitos.md)
- Contrato HTTP: [`specs/api.md`](specs/api.md)
- Decisões de arquitetura: [`specs/adr/`](specs/adr/)

## Pré-requisitos

- Docker com Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python 3.12.9 é instalado por ele)
- GPU NVIDIA com `nvidia-container-toolkit` (recomendado; sem GPU o Ollama roda na CPU, bem mais lento)
- Internet, para a pesquisa de fatos na web

## Subir o sistema

```bash
# 1. Postgres (pgvector) + Ollama + SearXNG
docker compose up -d

# 2. Modelos locais (só na primeira vez; ~8 GB)
docker compose exec ollama ollama pull gemma4:12b
docker compose exec ollama ollama pull embeddinggemma

# 3. Dependências e banco
uv sync
uv run alembic upgrade head

# 4. API
uv run uvicorn src.main:app --reload --host 0.0.0.0
```

Com a API no ar:

- **Interface de teste:** http://localhost:8000/ — enviar arquivo, gerar roadmap, explicar capítulo
- **Swagger:** http://localhost:8000/docs
- De outro aparelho na mesma rede: troque `localhost` pelo IP da máquina

Depois de reiniciar a máquina, repita os passos 1 e 4.

| Serviço | Porta no host |
|---|---|
| API | 8000 |
| Postgres | 5433 |
| Ollama | 11435 |
| SearXNG | 8888 |

## Como usar

1. **Envie o sumário** (`POST /books`). A resposta traz o `id` do livro e os capítulos extraídos.
2. **Gere o roadmap** (`POST /books/{id}/roadmaps`) com `{"senioridade": "Pleno", "disponibilidade_horas": 6}`.
   `senioridade`: `Junior`, `Pleno` ou `Senior` — capítulos abaixo do seu nível ficam de fora.
   A **primeira vez** de cada livro pesquisa na web e classifica os capítulos: 20 a 60 segundos.
   As seguintes saem na hora.
3. **Peça a explicação** de um capítulo (`GET /roadmaps/{id}/explicar?capitulo=N`).

### Montando um `.txt` de sumário

Serve para livros sem PDF de sumário (e-book, página de loja). A ordem não precisa ser rígida:

```text
Titulo: Nome do Livro

(descrição, resenhas e o que mais você colar aqui é ignorado)

Sumário
Prefácio
CAPÍTULO 1: Nome do primeiro capítulo
Um subtópico
Outro subtópico
CAPÍTULO 2: Nome do segundo capítulo
Subtópico
APÊNDICE: Algo
Índice remissivo
```

- A linha `Titulo:` (ou `Título do livro:`, `Nome do livro:`) pode estar em qualquer lugar antes
  de `Sumário`. Sem ela, vale a primeira linha do arquivo.
- Capítulos: `CAPÍTULO N: Nome`, `Capítulo N ▪ Nome` ou `N. Nome`. As outras linhas são subtópicos.
- **Com** número de página no fim de cada linha (`Capítulo 2: Nome 32`), as páginas vêm do sumário.
  **Sem**, o total de páginas é pesquisado na web e repartido entre os capítulos — o plano avisa
  que é estimativa e de onde veio o número.

## Testes

```bash
uv run pytest            # suíte padrão: sem LLM, sem GPU, sem internet (~8s)
uv run pytest -m eval    # modelo e web reais (~5 min); ATUALIZAR_BASELINE=1 regrava evals/baseline.json
```

Os testes de extração usam PDFs reais em `test_files/`, **fora do git** (são PDFs de editora).
Sem esses arquivos, esses testes pulam.

Diário de estudo para calibrar as horas: anote em `evals/estudo.csv` e rode
`uv run python -m evals.comparar_estudo`.

## Limitações conhecidas

- As constantes de tempo (minutos por página etc.) ainda não foram calibradas com estudo real.
- A senioridade Senior costuma ficar com 0 a 2 capítulos: o modelo marca poucos como avançados.
- Sumário sem paginação: o total de páginas é confiável, a divisão entre capítulos é aproximada.
- Arquivo sem título em lugar nenhum (sem capa, ficha nem cabeçalho) é recusado com `422`.
- Fora de escopo nesta versão: autenticação e acompanhamento de progresso.
