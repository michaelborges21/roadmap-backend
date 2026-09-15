# roadmapAPI — Spec de Implementação

API que gera cronogramas de estudo personalizados a partir de **sumários** de livros.

## Princípios não-negociáveis

1. **LLM julga e pesquisa fatos com fonte; Python calcula.** Nenhuma aritmética sai do modelo.
   Na pesquisa na web (2.9), o modelo só escolhe qual trecho é deste livro e transcreve o número;
   o Python só aceita o número se ele estiver escrito no trecho da fonte citada.
2. **Só sumário e capa.** Nunca o corpo do livro — nem no upload, nem no banco, nem no
   índice vetorial. Arquivo que contém capítulos inteiros é rejeitado ou tem só a seção
   de sumário aproveitada (ver 2.1). Da web, só a ficha técnica em volta de "páginas" (2.9).
3. **Código enxuto.** Sem repository pattern sobre SQLAlchemy, sem DTO que espelha model.
   Função de 3 linhas usada uma vez é inline.
4. **Banco simples.** 3 tabelas. FK sempre `int` → `id`. Sem chave composta, sem tabela de
   junção, sem herança. Variabilidade vai pra JSONB.
5. **Falha explícita.** Extração ambígua retorna `422`, nunca número silenciosamente errado.
   Número estimado (páginas repartidas a partir de um total pesquisado) é marcado como tal no plano.

---

## 1. Modelo de dados

Três tabelas. Só isso.

```python
class Book(Base):
    __tablename__ = "book"
    id: Mapped[int] = mapped_column(primary_key=True)
    hash_fonte: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    titulo: Mapped[str]
    origem: Mapped[str]                                  # "sumario" | "capa" | "ambos"
    paginas_conteudo: Mapped[int | None]                 # 1º capítulo → 1º marcador final
    paginas_fisicas: Mapped[int | None]                  # da ficha CIP, quando houver
    capitulos: Mapped[list] = mapped_column(JSONB, default=list)
    classificacao: Mapped[dict | None] = mapped_column(JSONB)
    pesquisa: Mapped[dict | None] = mapped_column(JSONB)  # fatos da web com fonte (2.9)


class Roadmap(Base):
    __tablename__ = "roadmap"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"))
    senioridade: Mapped[str]
    disponibilidade_horas: Mapped[float]
    plano: Mapped[dict] = mapped_column(JSONB)           # cronograma + fatores aplicados
    criado_em: Mapped[datetime] = mapped_column(server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"))
    texto: Mapped[str]                                   # trecho web em volta de "páginas" (ficha técnica)
    fonte: Mapped[str]                                   # URL de origem do trecho
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
```

**O que entra em `capitulos`** — só metadados, jamais conteúdo:

```json
[{"num": 1, "titulo": "O Cenário do Aprendizado de Máquina",
  "pag_inicio": 2, "paginas": 26,
  "subtopicos": ["O que É Aprendizado de Máquina?", "Tipos de Sistemas..."]}]
```

Sumário sem paginação: `pag_inicio` e `paginas` ficam `null` até a pesquisa (2.9).

**Decisões de schema:**

- `hash_fonte` (não `hash_sumario`): a fonte pode ser capa, sumário, ou os dois. É o cache
  entre usuários — mesmo arquivo, pula extração e classificação.
- `paginas_conteudo` e `capitulos` aceitam vazio: upload só de capa produz um `Book` válido
  com título e sem cronograma até o sumário chegar.
- `paginas_fisicas` vem da ficha CIP quando o PDF a inclui (ex.: "640 p."). Serve de
  sanidade: `paginas_conteudo` maior que `paginas_fisicas` é erro de extração.
- JSONB para o que ainda vai mudar de formato. Tabela `Capitulo` normalizada = migration
  a cada iteração por zero ganho de query. `pesquisa` também é JSONB pelo mesmo motivo.
- FKs `int` simples, sem `relationship()` bidirecional, sem cascade. Chunks de um livro:
  `select(Chunk).where(Chunk.book_id == id)`.
- `Chunk` é a base RAG de fatos pesquisados (4): um trecho de página web por linha, com a URL.
- Sem `User` por enquanto. Entra com autenticação, como `roadmap.user_id`.

**pgvector:** `CREATE EXTENSION vector;` na primeira migration. Índice `ivfflat` só acima de
~10k chunks.

---

## 2. Pipeline

```
upload → localizar_sumario() → limpar_ruido() → classificar_linhas()
       → fechar_paginas() | sumario_sem_paginas() → [cache hit?]
       → pesquisar() (web + RAG, 2.9) → distribuir_paginas() se o sumário não tem páginas
       → classificar_llm() → calcular() → cronograma()
```

### 2.1 Localizar o sumário

Um upload raramente é só o sumário. Amostras de editora trazem capa, ficha catalográfica,
sumário, prefácio e o primeiro capítulo no mesmo PDF.

- Procura a âncora (`Sumário`, `Conteúdo`, `Índice`, `Table of Contents`) e consome linhas
  até o padrão de sumário cessar (N linhas seguidas sem número de página ao final).
- Fora dessa janela, nada é lido. O prefácio e o capítulo 1 da amostra são descartados
  antes de qualquer processamento.
- É livro completo → `422` se o PDF tiver nº de páginas ≥ `livro_completo_fracao` (0.5)
  × `paginas_conteudo`, ou se não houver âncora e o texto tiver parágrafos corridos.
  (A regra antiga "janela > 15% do documento" foi descartada: arquivo só de sumário é
  quase todo sumário — Richards 87%, amostra Géron 32% — e ela rejeitava os fixtures.)

**`.txt`/`.md` (2026-09-15, para verificação sem PDF/imagem):** mesma âncora, mesma classificação
por rótulo, mesmo `fechar_paginas` — o número ao fim da linha continua sendo a página real do
livro original, só que digitado à mão em vez de extraído da camada de texto do PDF. O que muda:
sem fonte (sem marca d'água, sem "maior fonte" — título vem da ficha CIP se o texto incluir uma,
senão da marcação `Titulo: Nome` escrita pelo usuário em qualquer linha antes da âncora — também
`Título do livro:` e `Nome do livro:`; `Título original:` não conta —, senão da 1ª linha. O `.txt` é
montado à mão pelo usuário final, sem ordem garantida) e sem contagem de páginas do arquivo (a checagem de "livro
completo" por `livro_completo_fracao` não se aplica; só a heurística de parágrafo corrido
continua valendo). Markdown: `#`/`##`, `-`/`*`/`+` de lista e `*`/`_`/`` ` `` são removidos antes
de classificar — mas `N. Título` de lista numerada é preservado, porque já é o próprio formato
de rótulo que o parser espera.

**Sumário sem paginação (2026-09-15):** se nenhuma linha da janela termina em página **arábica**
(e-book, página de loja, texto digitado), `sumario_sem_paginas` lê as linhas depois da âncora:
capítulo pelo mesmo rótulo sem a página, subtópico pela posição, e apresentação/prefácio/parte/
apêndice como marcadores fora dos capítulos. A lista termina no índice remissivo ou numa linha de
texto corrido (termina em ponto e tem ≥ `paragrafo_min_chars`). Os capítulos saem com `paginas`
nulo — a extração nunca inventa página. (Uma janela só com "páginas" romanas não conta como
paginada: título que termina em "mil" ou "civil" casa o padrão romano.)

### 2.2 Limpar ruído

Cabeçalhos, rodapés e marca d'água se repetem página a página e poluem a extração real:

- `viii | Mãos à Obra: Aprendizado de Máquina...`
- `Criado em: 17/11/19 Modificado em: 01/08/2025 Etapa por: ...`
- `CG_HandOnMachine.indb 8`, `Amostra`, `Sumário | ix`

Heurística: linha **de borda** (4 primeiras/últimas da página) que se repete em ≥3 páginas
com variação só de número (arábico ou romano) é ruído. Descarta antes de classificar. Só
borda, porque subtópicos como "Exercícios 27" e "Resumo 405" se repetem no miolo e são reais.

Marca d'água ("Amostra" em letras gigantes) injeta caracteres dentro das palavras; é
filtrada por tamanho de fonte (`watermark_min_size`) antes de montar as linhas.

### 2.3 Classificar linhas

Numeração romana **não** identifica front matter de forma confiável — um dos livros de
referência numera o prefácio em romano (xv) e o outro em arábico (15). A classificação é
por **rótulo**:

```python
CAPITULO = [  # case-insensitive
    r"^Cap[ií]tulo\s+(\d+)\s*[:.■▪]?\s*(.+?)\s+(\d+)$", # "Capítulo 2: ...", "CAPÍTULO 2: ...",
                                                         # "Capítulo 2 ■ ...", "Capítulo 2 ▪ ...", "Capítulo 2 ..."
    r"^(\d+)\s?\.\s+(.+?)\s+(\d+)$",                     # "2. Projeto ML... 28", "2 . Introdução... 27"
]
# Só se nenhum rótulo acima casa no sumário inteiro: "1 Introdução a algoritmos 25".
CAPITULO_NUMERO_SOLTO = r"^(\d{1,2})\s+(?![\d.])(.+?)\s+(\d+)$"
MARCADOR = r"^(Parte\b|Pref[áa]cio\b|Apresenta[çc][ãa]o\b|Ap[êe]ndice|[A-Z]\.\s|[ÍI]ndice\b)"  # case-insensitive
```

Antes de casar, a linha é normalizada: `U+FFFD` (glifo sem mapa unicode — vira tanto o
ponto de "1." quanto o pontilhado) passa a `.`, e pontilhados (`....` ou `. . .`) são
colapsados.

- **Capítulo** — casa `CAPITULO`. Define fronteira e entra em `capitulos`.
- **Marcador** — casa `MARCADOR`. Não é capítulo, mas fecha a contagem do capítulo anterior.
  Inclui apêndice por letra (`A. Checklist do Projeto... 594`).
- **Subtópico** — qualquer outra linha com número de página ao final. Anexa ao capítulo
  corrente em `subtopicos`; não é fronteira.
- Entradas com página em romano são front matter: registradas como marcador, fora da conta.

Separador de dígitos: o número de página é o último grupo numérico da linha. Pontilhados
podem ou não existir — a extração de texto perde os pontos em alguns PDFs, então o parser
nunca depende deles. A indentação também se perde; por isso o nível vem do rótulo, não do
recuo.

### 2.4 Fechar páginas

```
paginas[n] = pag_inicio(próxima fronteira) - pag_inicio[n]
```

Fronteira = próximo **capítulo ou marcador**, o que vier antes. Ignorar marcadores infla a
contagem, porque a página de abertura de parte não é conteúdo do capítulo anterior:

| Caso | Próximo capítulo | Próximo marcador | Correto |
|---|---|---|---|
| Richards, cap. 8 (p.113) | cap. 9 (134) → 21 | Parte II (133) → **20** | 20 |
| Géron, cap. 9 (p.197) | cap. 10 (227) → 30 | PARTE II (225) → **28** | 28 |

`paginas_conteudo` = primeiro marcador após o último capítulo − primeiro capítulo.

Validações que disparam `422`: sequência não crescente, capítulo sem fronteira final,
`paginas_conteudo > paginas_fisicas`.

Sumário sem paginação não passa por aqui: não há fronteira para fechar. As páginas vêm de 2.9.

### 2.5 Upload só de capa

A capa fornece título, subtítulo, autor, edição e — quando o título nomeia uma linguagem —
alimenta `linguagens` do classificador. Não fornece página nenhuma.

Título: da ficha CIP quando houver (texto antes de ` / ` e de ` : `); senão, a linha de
maior fonte da página 1 — que não vale se for a própria âncora ("Sumário", em PDF que começa
direto no sumário); aí, o cabeçalho corrido que se repete no topo das páginas ("6 | Nome do
Livro"). Sem nenhum desses, `422` com instrução para enviar a capa ou um `.txt` com o título:
título errado levaria a pesquisa de fatos (2.9) a outro livro. Capa em imagem (JPEG/PNG): modelo de visão local (checkpoint 4),
`gemma4:12b` (checkpoint 1) — structured output com título, subtítulo, autor e edição;
nunca páginas.

Resultado: `Book` com `origem="capa"`, `capitulos=[]`, sem cronograma. A API responde `201`
com o livro criado e sinaliza que falta o sumário. Nunca inventa páginas.

### 2.6 LLM Classificador

Structured output com JSON schema nativo, validado por Pydantic. Nada de parsear string.

```python
class Classificacao(BaseModel):
    tipo_livro: Literal["teorico", "pratico", "hibrido"]
    densidade: Literal["leve", "media", "densa"]
    nivel_natural: Literal["iniciante", "intermediario", "avancado"]
    linguagens: list[str]
    capitulos: list[CapituloClassificado]    # exatamente um por capítulo extraído


class CapituloClassificado(BaseModel):
    num: int
    nivel: Literal["iniciante", "intermediario", "avancado"]   # nível do conteúdo
    peso: float = Field(ge=0.5, le=2.0)                          # esforço relativo (1.0 = médio)
```

Lista de objetos em vez de `dict[int, float]`: chave inteira vira `additionalProperties` no
JSON schema, que modelo local segue mal. Resposta fora do schema, ou sem exatamente um
julgamento por capítulo → `502`. Modelo não configurado ou Ollama fora → `503`.

Prompt curto, sem regras de cálculo — elas não são dele. Texto vivo em
`src/classificador.py` (`PROMPT`); não duplicar aqui. Regras que ele precisa manter:

- **Todo campo categórico tem definição própria** (`tipo_livro`, `densidade`,
  `nivel_natural`, `nivel` do capítulo). Campo sem definição deriva: sem a de
  `nivel_natural`, o modelo marcou "avancado" em 5 de 6 livros.
- `linguagens`: nomes curtos, lista vazia se o livro não ensina linguagem.
- Frase de consistência ("capítulos com profundidade e esforço equivalentes recebem o mesmo
  nível e peso"): CV na Huyen caiu de 8,1% para 0,0% (N=5).
- "Não estime tempo."
- Mudança de prompt só entra depois de comparar contra `evals/baseline.json`, com controle
  que isole a mudança.

Páginas não vão no prompt: sem número à vista, o modelo não é convidado a fazer conta.

Os `subtopicos` extraídos vão no prompt — é o que permite distinguir um capítulo denso de
um leve dentro do mesmo livro.

### 2.7 Calculadora (Python puro, determinística)

```python
MIN_PAGINA   = {"leve": 2.0, "media": 2.5, "densa": 3.0}
F_SENIOR     = {"Junior": 1.3, "Pleno": 1.0, "Senior": 0.7}
RATIO_CODIGO = {"teorico": 0.0, "hibrido": 0.4, "pratico": 0.8}
BAIXO_NIVEL  = {"c", "c++", "rust", "assembly", "zig"}
NIVEIS       = ["iniciante", "intermediario", "avancado"]
SENIORIDADES = ["Junior", "Pleno", "Senior"]
```

```python
def calcular(cap, cls, senioridade) -> dict:
    gap = 1.3 if NIVEIS.index(cls.nivel_natural) > SENIORIDADES.index(senioridade) else 1.0
    baixo = 1.3 if BAIXO_NIVEL & {l.lower() for l in cls.linguagens} else 1.0
    peso = next(j.peso for j in cls.capitulos if j.num == cap.num)
    fator = F_SENIOR[senioridade] * gap * baixo * peso

    leitura = cap.paginas * MIN_PAGINA[cls.densidade] / 60 * fator
    codigo = leitura * RATIO_CODIGO[cls.tipo_livro]
    return {"leitura": leitura, "codigo": codigo, "escrita": leitura * RATIO_ESCRITA,
            "fatores": {"senioridade": F_SENIOR[senioridade], "gap": gap,
                        "baixo_nivel": baixo, "peso": peso}}
```

**Senioridade filtra, além de pesar** (decisão de produto: se todo leitor lê todos os
capítulos, o projeto não tem razão de existir). Antes de calcular, capítulo com `nivel`
abaixo da senioridade sai do cronograma e vai para `excluidos` no plano:

| Senioridade | Entra no cronograma |
|---|---|
| Junior | todos |
| Pleno | `intermediario` e `avancado` |
| Senior | só `avancado` |

O LLM julga o nível; a exclusão é Python. Livro inteiro abaixo do nível → plano vazio
(0 semanas), não erro.

`fatores` é persistido. É o que responde "por que esse capítulo deu 6h?" sem chamar LLM.

Constantes em `config.yaml`. Calibrar = mudar número, não reescrever prompt.

`calcular` exige `cap.paginas`: sumário sem paginação passa antes por `distribuir_paginas` (2.9).

### 2.8 Cronograma

```python
semanas = ceil(total_horas / disponibilidade)
dias_por_semana = min(7, ceil(disponibilidade / 2))   # ~2h por sessão
```

Distribui capítulos nas semanas respeitando ordem, sem partir capítulo entre semanas
não-adjacentes.

### 2.9 Pesquisa de fatos na web (2026-09-15, [ADR 0004](specs/adr/0004-pesquisa-web-com-fatos-verificaveis.md))

Roda na primeira geração de roadmap de cada livro; o resultado fica em `book.pesquisa` e é
reaproveitado, como a classificação.

**Fluxo** (o Python conduz; o modelo não decide quando buscar):

1. SearXNG local (`docker-compose.yml`, sem chave) com `"<título> livro número de páginas"`.
2. Snippets dos resultados + download das primeiras páginas (`pesquisa.paginas_baixadas`), texto
   visível completo via `trafilatura.html2txt` — `extract()` descarta a ficha técnica da editora.
3. Só janelas de texto em volta de "páginas" que trazem uma contagem vão para a base RAG (4).
4. Os trechos mais próximos da consulta vão ao `gemma4:12b`, que responde `PaginasEncontradas`
   (`mesmo_livro`, `paginas_totais` transcrito, `url_fonte`, `justificativa`). O prompt proíbe
   calcular e estimar.
5. **Validação Python:** o número só vale se `mesmo_livro`, se está escrito no trecho da URL
   citada e se é plausível (≥ nº de capítulos). Senão, `paginas_totais` nulo com o motivo.
6. Contagens diferentes vistas nos trechos que o modelo marcou como deste livro
   (`trechos_deste_livro`, em qualquer edição) viram aviso no plano (`outras_contagens`); trecho de
   outro livro não gera aviso. Descartados já na busca: domínios de rede social
   (`pesquisa.dominios_ignorados` — nos testes reais só trouxeram contagens de outros livros: post
   com 488; LinkedIn com 65, 252, 262 e 401) e URLs de e-book/Kindle (`pesquisa.padroes_url_ignorados`
   — contam páginas de tela: o mesmo livro deu 561 no Kindle e 344 no impresso). O prompt pede a
   edição impressa. Caso real medido: editora e livraria com 344; outro livro de título parecido com 448.

**Uso:**

- Sumário **sem paginação**: o total da web inclui páginas pré e pós-textuais (prefácio,
  apêndice, índice). `paginas_de_conteudo` fica com a fração calibrada
  (`calculo.fracao_conteudo_pesquisa` = 0.92 — medido em 6 livros paginados, conteúdo = 90–95%
  do total, checkpoint 3 aprovado em 2026-09-15) e `distribuir_paginas` reparte essas páginas na
  proporção de 1 + nº de subtópicos, pelo maior resto (soma exata). O plano registra
  `paginas.origem = "pesquisa"`, `paginas_totais`, `paginas_conteudo`, a URL e a regra. Caso
  medido: mesmo livro em PDF paginado com 238 páginas de conteúdo; pelo `.txt` sem paginação,
  264 estimadas sem a fração e 243 com ela.
- Sumário **com paginação**: vale o sumário; a pesquisa só confere (aviso se o sumário soma mais
  que o total pesquisado).

**Falhas:** sem páginas e sem fonte confiável → `422`. SearXNG fora do ar → `503` se o livro
depende da pesquisa; aviso no plano se o sumário é paginado. Página que bloqueia robô é pulada.

**Catálogos fora da v1:** Google Books sem chave estava com a cota anônima esgotada e a Open
Library não tinha a tradução brasileira testada. Reavaliar o Google Books com chave gratuita
(checkpoint 6).

---

## 3. Harness de avaliação

`tests/eval/` — LLM real, **fora do CI padrão** (`pytest -m eval` ou job nightly).
CI normal roda com classificador, web e embedding mockados.

### Testes de extração (determinísticos, sem LLM)

| Propriedade | Assert |
|---|---|
| Fronteira por marcador | Richards cap. 8 = 20 pág.; Géron cap. 9 = 28 pág. |
| Amostra com corpo | PDF com capítulo completo → só o sumário é lido |
| Ruído | cabeçalhos/rodapés repetidos não viram capítulo nem subtópico |
| Dois formatos de rótulo | `Capítulo N:` e `N.` produzem o mesmo shape |
| Front matter | prefácio em romano e em arábico ficam ambos fora da conta |
| Capa isolada | gera `Book` com `capitulos=[]`, sem inventar páginas |
| Sanidade CIP | `paginas_conteudo <= paginas_fisicas` |
| Sem paginação | capítulos e subtópicos reconhecidos, `paginas` nulo |

### Property tests (com LLM)

| Propriedade | Assert |
|---|---|
| Monotonia senioridade | `h(Junior) ≥ h(Pleno) ≥ h(Senior)` |
| Diferença entre níveis | no golden set, `caps(Junior) > caps(Senior)` em livros não-iniciantes — "tudo avançado" ou "tudo iniciante" em todo livro é falha do modelo |
| Monotonia volume | mais páginas ⇒ mais horas |
| Schema | 100% das respostas validam em `Classificacao` |
| Baixo nível | sumário com C/Rust/Assembly ⇒ `baixo_nivel == 1.3` |
| Coerência aritmética | `semanas * disponibilidade >= total_horas` |
| Variância | N=10 mesmo input ⇒ desvio padrão < 15% |

### Pesquisa (com web e LLM reais)

| Propriedade | Assert |
|---|---|
| Fato verificado | livro real sem paginação ⇒ total de páginas encontrado, com `url_fonte` entre as fontes consultadas |

### Golden set

`tests/fixtures/` — 10 a 15 sumários reais. Os três primeiros já cobrem os casos difíceis:

1. **Fundamentos da Arquitetura de Software, 2ª ed.** — rótulo `Capítulo N:`, tudo em
   arábico inclusive prefácio, partes sem numeração própria, 27 capítulos, 454 páginas de
   conteúdo. Variação extrema de tamanho: cap. 15 tem 52 páginas, cap. 23 tem 8 — fator
   6,5x que justifica `peso_relativo`.
2. **Mãos à Obra: ML com Scikit-Learn, Keras & TensorFlow, 3ª ed.** — rótulo `N.`, prefácio
   em romano, partes com página própria, apêndices por letra, sem pontilhado no texto
   extraído, ficha CIP com 640 páginas, e o arquivo é uma amostra que inclui prefácio e
   capítulo 1 completos.
3. **JavaScript: O Guia Definitivo, 7ª ed.** — capa isolada, sem sumário.

Baseline em `evals/baseline.json`. Toda mudança de prompt ou constante compara contra ele.

**Estado (2026-09-15):** os arquivos do golden set e `evals/baseline.json` foram removidos a
pedido do usuário. Os testes que dependem deles pulam ("fixture ausente"); `test_baseline`
regrava a baseline na próxima rodada de eval com um golden set novo.

---

## 4. RAG

Base de fatos pesquisados na web, por livro (2026-09-15, [ADR 0004](specs/adr/0004-pesquisa-web-com-fatos-verificaveis.md)).
Substitui o RAG de referências entre capítulos de outros livros, que não passou no critério de
aceite e foi removido do código ([ADR 0003](specs/adr/0003-rag-nao-aceito.md): variância 8,1% sem,
0,0% com, 0,9% no controle — o ganho vinha do texto extra no prompt, não da busca).

- Chunk = janela de texto de página web em volta de "páginas" que traz uma contagem, com a URL
  em `fonte`. Nunca corpo de livro.
- Embedding local via Ollama: `embeddinggemma` (checkpoint 1) — multilíngue, 768 dim, 2048
  tokens; prefixos de recuperação (documento × consulta) em `config.yaml` (`pesquisa`).
- Uso: entregar ao modelo, na pesquisa de fatos (2.9), os trechos mais relevantes já coletados.
  Na segunda vez para o mesmo livro, a base é reaproveitada e a web não é consultada.
- Banco: pgvector no Postgres do projeto — sem banco vetorial separado.
- `GET /roadmaps/{id}/explicar?capitulo=N`: recupera texto do capítulo + `fatores`
  persistidos (ou o motivo da exclusão), LLM redige a explicação via structured output.

---

## 5. Fora de escopo (v1)

- Gemini como segundo provider — só Ollama até o harness fechar.
- Autenticação e persistência de progresso.
- OCR de capa: **decidido** (checkpoint 4, 2026-09-14) — modelo de visão local
  (`gemma4:12b`), não campo de título digitado. Implementado (§2.5).
- Pesquisa na web: **em escopo** desde 2026-09-15 (§2.9), só com ferramentas gratuitas e locais.

---

## 6. Checkpoints — parar e avisar

O desenvolvimento **para e consulta** nestes pontos:

1. **Escolha do modelo Ollama** (classificador e embedding) — avisar antes de fixar
   qualquer modelo, para pesquisa prévia.
   **Decidido (2026-09-14):** `gemma4:12b` (classificador, visão, explicação, pesquisa) e
   `embeddinggemma` (embedding) — ver `specs/adr/`.
2. **Antes de rodar testes com LLM real** — avisar para troca de modelo/esforço do lado
   Anthropic.
3. **Mudança nas constantes de `config.yaml`** — é calibração, não implementação.
4. **Capa em imagem (JPEG/PNG):** decidir entre modelo de visão local (custo: mais um
   modelo, mais uma dimensão no harness) e campo de título digitado pelo usuário
   (custo: 4 segundos de UX). PDF de capa com camada de texto não precisa de visão.
   **Decidido (2026-09-14):** modelo de visão local, `gemma4:12b` — ver seção 5.
5. **Criar tabela nova ou adicionar FK.**
6. **Fonte de pesquisa que exige chave, cadastro ou custo** (ex.: Google Books com chave, APIs
   de SERP) — não há orçamento para serviço pago.

---

## 7. Ordem de execução

1. Schema + migrations + pipeline de extração (2.1–2.5) + `422` explícito
2. Testes de extração determinísticos com os 3 fixtures
3. Classificador com structured output + calculadora + cronograma
4. Property tests com LLM + baseline
5. RAG, medido contra o baseline
6. Specs formais em `specs/` + ADR "por que a aritmética não é do LLM"
7. Pesquisa de fatos na web (2.9) + RAG como base de fatos (4) — 2026-09-15
