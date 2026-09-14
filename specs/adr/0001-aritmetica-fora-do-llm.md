# ADR 0001 — A aritmética não é do LLM

- **Status:** aceito
- **Data:** 2026-09-14
- **Implementação:** `src/calculo.py`, constantes em `config.yaml` (`calculo`)

## Contexto

O produto entrega horas, semanas e dias de estudo por capítulo. O modelo disponível é local e
pequeno: 7–12B parâmetros numa RTX 3060 de 12 GB, sem orçamento para provider cloud.

A tentação é pedir ao modelo "quanto tempo leva este capítulo?". Três observações pesam contra:

1. **O julgamento do modelo já varia sozinho.** Pedindo só categorias (tipo, densidade, nível e
   peso por capítulo), a mesma entrada rendeu horas com coeficiente de variação de 8,1% em 5
   rodadas (Huyen, `gemma4:12b`, antes da frase de consistência). Se o modelo também fizesse a
   conta, o erro de aritmética se somaria ao de julgamento, e não haveria como separar os dois.
2. **Saída longa quebra.** O `qwen2.5vl:7b` levou 99s e devolveu JSON fora do schema ao
   classificar 27 capítulos. Cada número a mais que o modelo precisa emitir é mais saída e mais
   chance de falha.
3. **Na implementação anterior deste projeto, a estimativa por LLM falhou de forma silenciosa.**
   Com uma chamada por capítulo, um sumário lido com granularidade errada (158 linhas tratadas
   como 158 capítulos, em vez de 10) inflou as horas e gerou 49 períodos para um único livro.
   Nenhuma validação pegou, porque cada número isolado parecia plausível.

## Decisão

O LLM **julga**; Python **calcula**.

- O LLM emite só categorias (`tipo_livro`, `densidade`, `nivel_natural`, `nivel` por capítulo),
  `linguagens` e um `peso` relativo por capítulo, limitado a [0.5, 2.0] e validado por Pydantic.
  O peso é o único número julgado, e é uma escala, não uma quantidade.
- Horas, semanas e dias saem de `src/calculo.py`, com constantes em `config.yaml`.
- Páginas nunca vão no prompt: sem número à vista, o modelo não é convidado a fazer conta.
- Cada capítulo do plano persiste os `fatores` aplicados.

## Consequências

**Ganhos**

- **Coerência por construção.** `semanas × disponibilidade ≥ total` e as monotonias de
  senioridade e de volume valem sempre; os testes que as verificam não dependem do modelo.
- **Testes baratos.** A suíte padrão (cálculo, cronograma, API) roda em ~6s sem GPU; só a
  qualidade do julgamento exige eval com LLM real.
- **Explicável sem LLM.** "Por que esse capítulo deu 6h?" é respondido pelos `fatores`
  persistidos; o endpoint `explicar` só redige o texto em cima deles.
- **Calibrar é mudar número.** Ajustar minutos por página ou fator de senioridade é editar
  `config.yaml` e comparar contra `evals/baseline.json`, sem reescrever prompt nem reclassificar.
- **Troca de modelo contida.** Um modelo novo só precisa acertar categorias; a fórmula não muda.

**Custos**

- **A fórmula é simples e não foi calibrada contra tempo real de estudo.** Minutos por página
  fixos por densidade são uma aproximação; os valores de `config.yaml` são os iniciais da spec.
- **O modelo não enxerga nuances fora das categorias.** Um capítulo com exercícios longos só
  pesa mais se o modelo traduzir isso no `peso`.
- **O peso ainda é julgamento numérico.** Está limitado e validado, mas continua sujeito à
  variância do modelo.

## Alternativas rejeitadas

| Alternativa | Por que não |
|---|---|
| LLM estima horas por capítulo | Soma erro de conta à variância de julgamento; sem como explicar ou calibrar; já falhou em silêncio antes. |
| LLM estima o total e Python só distribui | Mesmo problema, num único número difícil de validar. |
| Python calcula e o LLM "ajusta" o resultado | Reintroduz a aritmética no modelo por outra porta e destrói a explicação pelos fatores. |
