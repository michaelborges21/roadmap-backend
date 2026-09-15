# ADR 0004 — O modelo pesquisa fatos na web; o Python só aceita o que está escrito na fonte

- **Status:** aceito
- **Data:** 2026-09-15
- **Substitui:** [ADR 0003](0003-rag-nao-aceito.md) (o RAG de referências entre capítulos foi removido)
- **Implementação:** `src/pesquisa.py`, `src/calculo.py` (`distribuir_paginas`), `searxng/`,
  `docker-compose.yml`, migration `5a9d2c7e1b34`

## Contexto

Um sumário real enviado pelo usuário ("LLMs – As Partes Difíceis", texto de página de e-commerce)
tinha capítulos e subtópicos, mas **nenhum número de página**. O sistema respondia `422`, porque a
conta de horas depende de `paginas` por capítulo e a extração só reconhecia linhas terminadas em
página.

O dono do produto decidiu que o serviço de revisão de tempo deve ser feito com o modelo local,
apoiado em uma base RAG e em pesquisa na internet, em sites responsáveis, tudo gratuito e local.
Das opções apresentadas, escolheu: pesquisa traz fatos e o Python calcula; SearXNG para busca;
pgvector como base; `gemma4:12b` como modelo.

O ADR 0001 (aritmética fora do LLM) continua valendo: a pesquisa muda de onde vem o número de
páginas, não quem faz a conta.

## Evidência medida antes de implementar (2026-09-15)

| Fonte | O que trouxe | Observação |
|---|---|---|
| Editora (Novatec) | "Ano: 2026 **Páginas: 344**" | `trafilatura.extract()` **descarta** essa ficha (não é "conteúdo principal"); `html2txt()` preserva. |
| Livraria (Amazon) | "Número de páginas ‏ : ‎ **344** páginas" | Marcas de direção invisíveis entre rótulo e número. |
| Post em rede social (snippet da busca) | "Livro **488** páginas" | Diverge das fontes oficiais; o post mistura vários livros. |
| Marketplace (snippet da busca) | "Número de páginas: **448**" | **Outro livro**, de título parecido ("Arquitetura de Software: As Partes Difíceis"). |
| Google Books API, sem chave | — | Cota anônima global esgotada ("Quota exceeded... Queries per day"). |
| Open Library API | — | Não tem a tradução brasileira (nem o original, pelo título). |

Conclusões: (1) o número certo existe na web em fontes oficiais; (2) a web também tem números
errados para o mesmo título, então um parser que pega "o primeiro número perto de páginas"
erraria; (3) extrair só o "conteúdo principal" perde justamente a fonte mais confiável.

## Decisão

**Fluxo (Python conduz; o modelo não decide quando buscar):**

1. SearXNG local (contêiner, sem chave) com `"<título> livro número de páginas"`.
2. Snippets de todos os resultados + download das primeiras páginas. Texto visível completo via
   `trafilatura.html2txt`.
3. Só as **janelas de texto em volta de "páginas" que contêm uma contagem** vão para a base RAG
   (`chunk`, com a URL em `fonte`). É ficha técnica; corpo de livro não cabe na janela nem passa
   no filtro de contagem.
4. Recupera do pgvector os trechos mais próximos da consulta (`embeddinggemma`).
5. `gemma4:12b` responde via structured output: `mesmo_livro`, `paginas_totais` (transcrito),
   `url_fonte`, `justificativa`. O prompt proíbe calcular e estimar.
6. **O Python só aceita o número** se o modelo disse que é o mesmo livro, se o número está escrito
   no trecho da URL citada e se é plausível (≥ nº de capítulos). Senão, `paginas_totais` fica nulo
   com o motivo.
7. Contagens diferentes vistas nos trechos viram `outras_contagens` e, depois, **aviso no plano**.
8. Resultado em `book.pesquisa`; na segunda vez para o mesmo livro, a base RAG é reaproveitada e a
   web não é consultada.

**Uso do fato:**

- Sumário **sem paginação**: `distribuir_paginas` reparte o total na proporção de
  1 + nº de subtópicos (maior resto, soma exata). O plano registra `origem="pesquisa"`, a URL e a
  regra — o número é marcado como estimado.
- Sumário **com paginação**: as páginas do sumário valem; a pesquisa só confere (aviso se o
  sumário soma mais do que o total pesquisado).
- Sem páginas e sem fonte confiável → `422`. SearXNG fora do ar → `503` se o livro depende da
  pesquisa; aviso se o sumário é paginado.

**Fora da v1:** Google Books e Open Library, apesar de escolhidos junto com o SearXNG — medidos
como inutilizáveis para este caso (tabela acima). Reavaliar o Google Books com chave gratuita.
Uma fonte que exija chave, cadastro ou custo passa a ser checkpoint.

## Consequências

**Ganhos**

- Sumários sem paginação (e-book, página de loja, texto digitado) viram roadmap.
- Nenhum número do modelo entra na conta sem estar escrito numa fonte citada; alucinação de
  número é descartada por construção (testado).
- Divergência na web fica visível ao usuário em vez de ser resolvida em silêncio.
- A base RAG passa a ter um papel mensurável (reuso sem nova ida à web) em vez de uma melhora de
  qualidade que não se sustentou (ADR 0003).

**Custos**

- **Dependência da web.** Sites mudam layout e bloqueiam robôs; o eval
  `tests/eval/test_pesquisa_web.py` pode quebrar sem regressão de código.
- **Estimativa, não medida.** O total pesquisado inclui páginas pré e pós-textuais (prefácio,
  apêndice, índice), então as horas tendem a sair acima do real; a repartição por subtópicos é
  uma aproximação. Calibrar com `evals/estudo.csv`.
- **Primeira geração mais lenta:** busca + downloads + embeddings + modelo, 1 a 2 minutos.
- **Mais um serviço** no `docker-compose.yml` (SearXNG).
- A escolha da fonte ainda é julgamento do modelo: se ele disser que um trecho de outro livro é
  deste e o número estiver escrito nesse trecho, o número passa. A mitigação é a divergência
  aparecer como aviso.

## Alternativas rejeitadas

| Alternativa | Por que não |
|---|---|
| Modelo estima as horas a partir da pesquisa | Tira do Python a conta e a explicação pelos fatores (ADR 0001). |
| Contar subtópicos como "páginas" sem pesquisar | Inventa um número sem base externa; viola a falha explícita. |
| Regex pega "o primeiro número perto de páginas" | Erraria com dados reais (488 de um post, 448 de outro livro). |
| `trafilatura.extract()` (conteúdo principal) | Descarta a ficha técnica da editora. |
| Function calling (o modelo decide quando e o que buscar) | Menos previsível e testável com modelo local de 12B. |
| ChromaDB/Qdrant como base vetorial | Mais um serviço sem ganho; o pgvector já está no projeto. |
