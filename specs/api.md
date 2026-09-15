# Contrato HTTP — roadmapAPI

Fonte da verdade executável: `src/main.py` (OpenAPI em `/docs`). Este documento fixa o que o
cliente pode esperar; mudança aqui é mudança de contrato.

Erros de domínio respondem `{"detail": "<mensagem legível>"}`. Erros de validação de entrada
(`422` do FastAPI) respondem `{"detail": [ ... ]}` no formato padrão do Pydantic.

| Código | Significado |
|---|---|
| `415` | Tipo de arquivo não suportado. |
| `422` | Extração ambígua, livro sem sumário ou pedido inválido. Nunca um número silenciosamente errado. |
| `502` | O LLM respondeu fora do contrato (schema, capítulo faltando, resposta cortada). |
| `503` | Modelo não configurado ou Ollama inacessível. |

---

## `POST /books`

Upload de sumário ou capa. `multipart/form-data`, campo `arquivo`.

| Tipo | Tratamento |
|---|---|
| `application/pdf` | Extração determinística (sumário, ou capa se não houver sumário). |
| `image/jpeg`, `image/png` | Capa lida por modelo de visão. |

| Resposta | Quando |
|---|---|
| `201` | Livro criado. |
| `200` | Mesmo arquivo já enviado (cache por SHA-256 do conteúdo, entre usuários). |
| `415` | Outro tipo de arquivo. |
| `422` | Extração ambígua (ver RF-EXT-07). |
| `502` / `503` | Só para imagem de capa. |

Exemplo (título, páginas e 1º capítulo reais do sumário do Richards; lista abreviada):

```json
{
  "id": 1,
  "titulo": "Fundamentos da arquitetura de software",
  "origem": "sumario",
  "paginas_conteudo": 454,
  "paginas_fisicas": null,
  "capitulos": [
    {"num": 1, "titulo": "Introdução", "pag_inicio": 20, "paginas": 11,
     "subtopicos": ["Definindo a arquitetura de software", "..."]}
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
| `201` | Roadmap criado. Na primeira vez para o livro, classifica (~45s com `gemma4:12b`); depois reaproveita. |
| `404` | Livro inexistente. |
| `422` | Livro sem sumário (só capa) ou pedido inválido. |
| `502` / `503` | Falha do classificador (só na primeira classificação do livro). |

Exemplo ilustrativo do formato (valores não vêm de uma execução real):

```json
{
  "id": 7,
  "book_id": 1,
  "senioridade": "Pleno",
  "disponibilidade_horas": 6.0,
  "total_horas": 31.7,
  "semanas": 6,
  "dias_por_semana": 3,
  "capitulos": [
    {"num": 2, "titulo": "Pensamento arquitetural", "nivel": "intermediario",
     "leitura": 0.9, "codigo": 0.0, "escrita": 0.14, "total": 1.04,
     "fatores": {"senioridade": 1.0, "gap": 1.0, "baixo_nivel": 1.0, "peso": 1.0}}
  ],
  "excluidos": [{"num": 1, "titulo": "Introdução", "nivel": "iniciante"}],
  "cronograma": [
    {"semana": 1, "horas": 6.0, "capitulos": [{"num": 2, "horas": 1.04}, {"num": 3, "horas": 4.96}]}
  ]
}
```

- `capitulos`: só os que entram no cronograma, com horas e `fatores` aplicados.
- `excluidos`: capítulos com nível abaixo da senioridade ([ADR 0002](adr/0002-senioridade-filtra-capitulos.md)).
  Livro inteiro abaixo do nível → `capitulos: []`, `semanas: 0`, sem erro.
- Um capítulo aparece em mais de uma semana só se forem semanas adjacentes.

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
