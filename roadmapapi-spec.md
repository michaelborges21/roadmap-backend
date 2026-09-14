# roadmapAPI — Spec de Implementação

API que gera cronogramas de estudo personalizados a partir de **sumários** de livros.

## Princípios não-negociáveis

1. **LLM só julga, Python calcula.** Nenhuma aritmética sai do modelo.
2. **Só sumário e capa.** Nunca o corpo do livro — nem no upload, nem no banco, nem no
   índice vetorial. Arquivo que contém capítulos inteiros é rejeitado ou tem só a seção
   de sumário aproveitada (ver 2.1).
3. **Código enxuto.** Sem repository pattern sobre SQLAlchemy, sem DTO que espelha model.
   Função de 3 linhas usada uma vez é inline.
4. **Banco simples.** 3 tabelas. FK sempre `int` → `id`. Sem chave composta, sem tabela de
   junção, sem herança. Variabilidade vai pra JSONB.
5. **Falha explícita.** Extração ambígua retorna `422`, nunca número silenciosamente errado.

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
    texto: Mapped[str]                                   # título do cap + subtópicos
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
```

**O que entra em `capitulos`** — só metadados, jamais conteúdo:

```json
[{"num": 1, "titulo": "O Cenário do Aprendizado de Máquina",
  "pag_inicio": 2, "paginas": 26,
  "subtopicos": ["O que É Aprendizado de Máquina?", "Tipos de Sistemas..."]}]
```

**Decisões de schema:**

- `hash_fonte` (não `hash_sumario`): a fonte pode ser capa, sumário, ou os dois. É o cache
  entre usuários — mesmo arquivo, pula extração e classificação.
- `paginas_conteudo` e `capitulos` aceitam vazio: upload só de capa produz um `Book` válido
  com título e sem cronograma até o sumário chegar.
- `paginas_fisicas` vem da ficha CIP quando o PDF a inclui (ex.: "640 p."). Serve de
  sanidade: `paginas_conteudo` maior que `paginas_fisicas` é erro de extração.
- JSONB para o que ainda vai mudar de formato. Tabela `Capitulo` normalizada = migration
  a cada iteração por zero ganho de query.
- FKs `int` simples, sem `relationship()` bidirecional, sem cascade. Chunks de um livro:
  `select(Chunk).where(Chunk.book_id == id)`.
- Sem `User` por enquanto. Entra com autenticação, como `roadmap.user_id`.

**pgvector:** `CREATE EXTENSION vector;` na primeira migration. Índice `ivfflat` só acima de
~10k chunks.

---

## 2. Pipeline

```
upload → localizar_sumario() → limpar_ruido() → classificar_linhas()
       → fechar_paginas() → [cache hit?] → classificar_llm() → calcular() → cronograma()
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
    r"^Cap[ií]tulo\s+(\d+)\s*[:.■]?\s*(.+?)\s+(\d+)$", # "Capítulo 2: ...", "CAPÍTULO 2: ...",
                                                        # "Capítulo 2 ■ ...", "Capítulo 2 ..."
    r"^(\d+)\s?\.\s+(.+?)\s+(\d+)$",                    # "2. Projeto ML... 28", "2 . Introdução... 27"
]
MARCADOR = r"^(Parte\b|Pref[áa]cio\b|Ap[êe]ndice|[A-Z]\.\s|[ÍI]ndice\b)"  # case-insensitive
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

### 2.5 Upload só de capa

A capa fornece título, subtítulo, autor, edição e — quando o título nomeia uma linguagem —
alimenta `linguagens` do classificador. Não fornece página nenhuma.

Título: da ficha CIP quando houver (texto antes de ` / ` e de ` : `); senão, a linha de
maior fonte da página 1. Capa em imagem (JPEG/PNG): modelo de visão local (checkpoint 4),
`gemma4:12b` (checkpoint 1) — structured output com título, subtítulo, autor e edição;
nunca páginas.

Resultado: `Book` com `origem="capa"`, `capitulos=[]`, sem cronograma. A API responde `201`
com o livro criado e sinaliza que falta o sumário. Nunca inventa páginas.

### 2.6 LLM Classificador

Único ponto onde o modelo é chamado. Structured output com JSON schema nativo, validado
por Pydantic. Nada de parsear string.

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

Prompt curto, sem regras de cálculo — elas não são dele:

> Você classifica livros técnicos. Dado o título e o sumário abaixo, identifique tipo,
> densidade, nível natural do conteúdo e linguagens de programação abordadas. Para cada
> capítulo, julgue o nível do conteúdo (iniciante = fundamentos que um profissional pleno
> já domina; intermediario = exige base prévia; avancado = aproveitado mesmo por quem já é
> sênior) e um peso relativo de esforço (1.0 = médio, entre 0.5 e 2.0). Não estime tempo.

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

### 2.8 Cronograma

```python
semanas = ceil(total_horas / disponibilidade)
dias_por_semana = min(7, ceil(disponibilidade / 2))   # ~2h por sessão
```

Distribui capítulos nas semanas respeitando ordem, sem partir capítulo entre semanas
não-adjacentes.

---

## 3. Harness de avaliação

`tests/eval/` — LLM real, **fora do CI padrão** (`pytest -m eval` ou job nightly).
CI normal roda com classificador mockado.

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

---

## 4. RAG

Entra **depois** do harness verde. Sem baseline não há como provar melhora.

- Chunk = título do capítulo + `subtopicos` (1 chunk por capítulo). Nunca corpo do livro.
- Embedding local via Ollama.
- Uso: alimentar o Classificador com contexto real em vez de inferência pelo título.
- `GET /roadmap/{id}/explicar?capitulo=N`: recupera chunk + `fatores` persistidos, LLM
  redige a explicação.

**Critério de aceite:** variância do golden set cai, ou `tipo_livro` bate mais com
julgamento humano. Se nenhum dos dois, RAG não vai pra produção.

---

## 5. Fora de escopo (v1)

- Gemini como segundo provider — só Ollama até o harness fechar.
- Autenticação e persistência de progresso.
- OCR de capa: **decidido** (checkpoint 4) — modelo de visão local, pendente da escolha
  de modelo.

---

## 6. Checkpoints — parar e avisar

O desenvolvimento **para e consulta** nestes pontos:

1. **Escolha do modelo Ollama** (classificador e embedding) — avisar antes de fixar
   qualquer modelo, para pesquisa prévia.
2. **Antes de rodar testes com LLM real** — avisar para troca de modelo/esforço do lado
   Anthropic.
3. **Mudança nas constantes de `config.yaml`** — é calibração, não implementação.
4. **Capa em imagem (JPEG/PNG):** decidir entre modelo de visão local (custo: mais um
   modelo, mais uma dimensão no harness) e campo de título digitado pelo usuário
   (custo: 4 segundos de UX). PDF de capa com camada de texto não precisa de visão.
5. **Criar tabela nova ou adicionar FK.**

---

## 7. Ordem de execução

1. Schema + migrations + pipeline de extração (2.1–2.5) + `422` explícito
2. Testes de extração determinísticos com os 3 fixtures
3. Classificador com structured output + calculadora + cronograma
4. Property tests com LLM + baseline
5. RAG, medido contra o baseline
6. Specs formais em `specs/` + ADR "por que a aritmética não é do LLM"
