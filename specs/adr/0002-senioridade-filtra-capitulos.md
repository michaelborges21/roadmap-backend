# ADR 0002 — Senioridade filtra capítulos, além de pesar

- **Status:** aceito
- **Data:** 2026-09-14
- **Implementação:** `src/calculo.py` (`planejar`), `nivel` por capítulo em `src/classificador.py`

## Contexto

A spec original usava senioridade só como multiplicador de tempo (`F_SENIOR`): todo capítulo
entrava no cronograma, e um sênior só lia mais rápido.

Para o dono do produto, isso esvazia o projeto: "se for para ler todos os capítulos, qual o
sentido? Se não há diferença entre júnior, pleno e sênior, o projeto é morto." O valor está em
dizer **o que** cada pessoa precisa ler, não só quanto tempo leva.

## Decisão

- O LLM julga o `nivel` do conteúdo de cada capítulo: `iniciante`, `intermediario` ou `avancado`.
- Python exclui do cronograma os capítulos com nível abaixo da senioridade:

  | Senioridade | Entra no cronograma |
  |---|---|
  | Junior | todos |
  | Pleno | `intermediario` e `avancado` |
  | Senior | só `avancado` |

- Os fatores de tempo da spec continuam valendo sobre os capítulos que ficam.
- Os capítulos cortados aparecem em `excluidos`, para o usuário ver o que foi pulado e por quê.
- Livro inteiro abaixo do nível gera plano vazio (0 semanas), não erro.

## Consequências

**Ganhos**

- A senioridade muda de fato o que se lê. Na baseline: Géron 19 → 15 → 9 capítulos;
  Downey 14 → 7 → 2.
- A regra de corte é determinística e testável; só o julgamento de nível depende do modelo.

**Custos**

- A qualidade depende inteiramente do `nivel` julgado. Modelos locais tendem a concentrar
  tudo em `intermediario`: no Richards, 23 de 27 capítulos, e o Senior fica com 1 capítulo.
- Sênior recebe 0 capítulos em livros sem conteúdo `avancado` (Nelson, Sweigart). É correto
  para livros introdutórios, mas amplifica erros de julgamento.
- Corte binário: um capítulo `intermediario` "quase avançado" some inteiro para o sênior.

## Tentativa rejeitada: redefinir os níveis pelo leitor que ainda ganha com o capítulo

**2026-09-14.** Para dar mais capítulos ao Senior, os níveis do prompt foram redefinidos para
espelhar a regra de corte: iniciante = o que um pleno já domina; intermediario = o que um pleno
ainda não domina, mas um sênior já conhece; avancado = o que acrescenta mesmo para um sênior.

Critérios de aceite fixados antes de rodar: schema 100%; tipo, densidade e nível do livro
iguais à baseline; Senior ganha capítulos em pelo menos 3 de 4 livros não-iniciantes; livro
iniciante continua com Senior em ~0; diferenciação Junior > Pleno > Senior mantida.

| Livro | Senior antes → depois |
|---|---|
| Richards | 1 → 13 |
| Nelson | 0 → 3 |
| Huyen | 2 → 2 |
| Géron | 9 → **5** |
| Downey | 2 → **0**, e o livro inteiro mudou: nível intermediario → iniciante, densidade densa → media |
| Sweigart | 0 → 0 |

**Rejeitada e revertida.** Resolveu o caso que a motivou (Richards), mas piorou Géron e
Downey e alterou campos do livro que a mudança não deveria tocar. Próximas tentativas
devem mudar uma definição por vez e ser medidas com o mesmo protocolo.

## Salvaguardas

- `tests/eval/test_propriedades.py::test_diferenca_entre_niveis` falha se a senioridade não
  mudar os capítulos em livros não-iniciantes, ou se o modelo marcar todo livro num nível só.
- A definição de cada nível no prompt é regra da spec 2.6.
