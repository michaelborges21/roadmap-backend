# ADR 0003 — RAG implementado, mas não aceito em produção

- **Status:** substituído pelo [ADR 0004](0004-pesquisa-web-com-fatos-verificaveis.md) em 2026-09-15.
  O RAG de referências entre capítulos foi removido do código; o RAG atual é uma base de fatos
  pesquisados na web. As medições abaixo continuam válidas como histórico.
- **Data:** 2026-09-14
- **Implementação:** `src/rag.py`, medição em `tests/eval/test_rag.py`, resultado em `evals/rag.json`

## Contexto

A spec 4 previa RAG para dar ao classificador "contexto real". O sumário do próprio livro o
classificador já vê inteiro; o contexto novo possível são **capítulos parecidos de outros livros
já classificados**, com o nível e o peso que receberam, como referência de calibração.

Critério de aceite, decidido pelo usuário: **só variância**. O critério alternativo da spec
("`tipo_livro` bate mais com julgamento humano") foi descartado, porque o julgamento depende de
quanto a pessoa conhece cada livro.

## Experimento

- Embedding `embeddinggemma` (multilíngue, 768 dim), 1 chunk por capítulo.
- Limiar `similaridade_min: 0.78`. O melhor vizinho entre livros vai de 0,67 a 0,84; na mediana
  (0,73) os assuntos são só vagamente parecidos.
- Referências leave-one-out no golden set (cada livro vê só os outros), em transação desfeita.
- Variância das horas (Pleno) no livro da Huyen, N=5, `gemma4:12b`.

| Variante | CV das horas |
|---|---|
| Sem RAG (prompt da época) | 8,1% |
| Com RAG | 0,0% |
| **Controle: nota de referências no prompt, nenhuma referência** | **0,9%** |

Só 1 dos 10 capítulos da Huyen recebeu referência, e só 10 dos 109 capítulos do golden set.

## Decisão

O RAG **não** vai para produção. A queda de variância vinha do texto extra no prompt, não da
busca: o controle sem nenhuma referência estabilizou quase igual.

O efeito foi aproveitado sem RAG: uma frase de consistência no prompt ("capítulos com
profundidade e esforço equivalentes recebem o mesmo nível e peso") deu CV 0,0% (N=5) e entrou
na baseline.

O código continua no repositório, desligado e testado com mocks, porque:

- os chunks já são gerados na classificação e o corpus cresce com o uso;
- reavaliar é mudar uma chave e rodar `tests/eval/test_rag.py`.

## Consequências

- O prompt de produção ficou o da baseline; com `rag.ativo: false` ele era idêntico (havia
  um teste para isso, removido junto com o código em 2026-09-15).
- Enquanto existiu, cada classificação fazia uma chamada extra de embedding (~1s), mesmo com o
  RAG desligado. Deixou de existir com o ADR 0004.
- **Reavaliar quando o corpus tiver muitos livros da mesma área.** Com 6 livros de áreas
  diferentes, quase nenhum capítulo tem vizinho real.
- Lição de processo: todo ganho de prompt precisa de controle que isole a mudança. Sem ele,
  este RAG teria ido para produção por um efeito que não era dele.
