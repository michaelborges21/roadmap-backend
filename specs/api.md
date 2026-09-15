# Contrato HTTP — roadmapAPI

Fonte da verdade executável: `src/main.py` (OpenAPI em `/docs`). Este documento fixa o que o
cliente pode esperar; mudança aqui é mudança de contrato.

Erros de domínio respondem `{"detail": "<mensagem legível>"}`. Erros de validação de entrada
(`422` do FastAPI) respondem `{"detail": [ ... ]}` no formato padrão do Pydantic.

| Código | Significado |
|---|---|
| `415` | Tipo de arquivo não suportado. |
| `422` | Extração ambígua, livro sem sumário, sumário sem páginas sem fonte confiável na web, ou pedido inválido. Nunca um número silenciosamente errado. |
| `502` | O LLM respondeu fora do contrato (schema, capítulo faltando, resposta cortada). |
| `503` | Modelo não configurado, Ollama inacessível, ou SearXNG inacessível quando o roadmap depende da pesquisa. |

---

## `POST /books`

Upload de sumário ou capa. `multipart/form-data`, campo `arquivo`.

| Tipo | Tratamento |
|---|---|
| `application/pdf` | Extração determinística (sumário, ou capa se não houver sumário). |
| `image/jpeg`, `image/png` | Capa lida por modelo de visão. |
| `.txt`, `.md` (qualquer `content_type`; a extensão desempata) | Mesma extração determinística do PDF, sem fonte e sem marca d'água. |

O sumário pode vir **com** página ao fim de cada linha (`Capítulo 2: Título 32`) ou **sem**
paginação (e-book, página de loja, texto digitado). Sem paginação, os capítulos saem com
`pag_inicio` e `paginas` nulos; as páginas vêm da pesquisa na web ao gerar o roadmap.

| Resposta | Quando |
|---|---|
| `201` | Livro criado. |
| `200` | Mesmo arquivo já enviado (cache por SHA-256 do conteúdo, entre usuários). |
| `415` | Outro tipo de arquivo. |
| `422` | Extração ambígua (ver RF-EXT-07). |
| `502` / `503` | Só para imagem de capa. |

Exemplo ilustrativo (lista abreviada):

```json
{
  "id": 12,
  "titulo": "Nome do Livro",
  "origem": "sumario",
  "paginas_conteudo": 310,
  "paginas_fisicas": null,
  "capitulos": [
    {"num": 1, "titulo": "Introdução", "pag_inicio": 15, "paginas": 18,
     "subtopicos": ["Por que este livro", "..."]}
  ],
  "falta_sumario": false
}
```

`origem`: `"sumario"` ou `"capa"`. Capa → `capitulos: []`, páginas `null`, `falta_sumario: true`.

---

## `POST /books/{book_id}/roadmaps`

```json
{"senioridade": "Pleno", "disponibilidade_horas": 6}
```

| Campo | Regra |
|---|---|
| `senioridade` | `"Junior"`, `"Pleno"` ou `"Senior"`. |
| `disponibilidade_horas` | Horas por semana, `0 < x <= 168`. |

| Resposta | Quando |
|---|---|
| `201` | Roadmap criado. Na primeira vez para o livro, pesquisa na web e classifica (1 a 2 min com `gemma4:12b`); depois reaproveita as duas coisas. |
| `404` | Livro inexistente. |
| `422` | Livro sem sumário (só capa), sumário sem páginas e sem fonte confiável na web, ou pedido inválido. |
| `502` / `503` | Falha do classificador ou da pesquisa (só na primeira vez para o livro). Pesquisa fora do ar só vira `503` se o sumário não tem páginas; com páginas, vira aviso. |

Exemplo ilustrativo do formato (valores não vêm de uma execução real):

```json
{
  "id": 7,
  "book_id": 12,
  "senioridade": "Pleno",
  "disponibilidade_horas": 6.0,
  "total_horas": 31.7,
  "semanas": 6,
  "dias_por_semana": 3,
  "capitulos": [
    {"num": 2, "titulo": "Fundamentos", "nivel": "intermediario", "paginas": 22,
     "leitura": 0.9, "codigo": 0.0, "escrita": 0.14, "total": 1.04,
     "fatores": {"senioridade": 1.0, "gap": 1.0, "baixo_nivel": 1.0, "peso": 1.0}}
  ],
  "excluidos": [{"num": 1, "titulo": "Introdução", "nivel": "iniciante"}],
  "cronograma": [
    {"semana": 1, "horas": 6.0, "capitulos": [{"num": 2, "horas": 1.04}, {"num": 3, "horas": 4.96}]}
  ],
  "paginas": {
    "origem": "pesquisa",
    "paginas_totais": 344,
    "paginas_conteudo": 316,
    "url_fonte": "https://editora.exemplo/livro",
    "regra": "Sumário sem paginação: 92% do total de páginas pesquisado (o resto é prefácio, apêndice e índice)..."
  },
  "avisos": ["Outras contagens de páginas vistas na web (podem ser de outro livro ou edição): 488 páginas (rede.exemplo)"]
}
```

- `capitulos`: só os que entram no cronograma, com páginas, horas e `fatores` aplicados.
- `excluidos`: capítulos com nível abaixo da senioridade ([ADR 0002](adr/0002-senioridade-filtra-capitulos.md)).
  Livro inteiro abaixo do nível → `capitulos: []`, `semanas: 0`, sem erro.
- Um capítulo aparece em mais de uma semana só se forem semanas adjacentes.
- `paginas.origem`: `"sumario"` (páginas do próprio sumário) ou `"pesquisa"` (total achado na
  web, com `url_fonte`, repartido pela `regra` — estimado). `null` em roadmaps criados antes da
  pesquisa de fatos ([ADR 0004](adr/0004-pesquisa-web-com-fatos-verificaveis.md)).
- `avisos`: divergências e limitações que o leitor precisa saber (contagens diferentes na web,
  pesquisa indisponível, sumário maior que a fonte pesquisada).

---

## `GET /roadmaps/{roadmap_id}/explicar?capitulo=N`

Responde "por que esse capítulo deu X horas?" (ou "por que ficou de fora?"). O LLM só redige; os
números vêm dos `fatores` persistidos no roadmap.

| Resposta | Quando |
|---|---|
| `200` | `{"explicacao": "..."}` |
| `404` | Roadmap inexistente, ou capítulo fora do livro/roadmap. |
| `502` / `503` | Falha do LLM. |

---

## `GET /` — interface de teste, fora do contrato

Serve `static/index.html`, uma página sem estilo com formulários para os três endpoints acima.
Existe só para exercitar a API sem o Swagger; não tem schema, não tem teste próprio e pode
mudar ou sumir sem aviso de versão. `include_in_schema=False`: não aparece em `/docs`.

Fluxo pensado para quem estuda, sem ids na tela (os ids da API ficam guardados na página):

1. Envia até 5 arquivos, um de cada vez ao `POST /books`; cada um vira um `Book` independente e
   aparece na lista pelo título.
2. Com senioridade e horas escolhidas uma vez, gera **um roadmap por livro** (`POST
   /books/{id}/roadmaps`, um livro de cada vez), cada um num bloco com o título do livro.
3. Explicar: escolhe o **livro** e depois o **capítulo** pelo nome; a página usa o `roadmap_id` do
   roadmap daquele livro gerado no passo 2 (a explicação depende da senioridade e das horas dele).

Não existe (nem é objetivo) roadmap combinando vários livros, o que exigiria tabela de junção
(princípio 4 da spec). A lista de livros vive só na página aberta: recarregar exige reenviar os
arquivos, o que é instantâneo pelo cache por hash. Sem endpoint novo nem mudança de schema.
