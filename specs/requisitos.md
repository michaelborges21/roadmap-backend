# Requisitos — roadmapAPI

Spec de implementação: [`roadmapapi-spec.md`](../roadmapapi-spec.md). Contrato HTTP: [`api.md`](api.md).
Decisões: [`adr/`](adr/).

Todo requisito funcional aponta para o teste que o verifica. `tests/test_specs.py` falha se uma
referência apontar para teste inexistente ou se um RF ficar sem teste.

- `tests/*.py` — suíte padrão, LLM mockado: `uv run pytest` (~6s).
- `tests/eval/*.py` — LLM real, fora do CI: `uv run pytest -m eval` (~4 min propriedades, ~6 min RAG).

## Extração (spec 2.1–2.5)

| ID | Requisito | Verificado por |
|---|---|---|
| RF-EXT-01 | Só a janela do sumário é lida; prefácio e capítulos de amostra são descartados antes de qualquer processamento. | `tests/test_extracao.py::test_amostra_com_corpo_le_so_sumario` |
| RF-EXT-02 | Cabeçalho, rodapé e marca d'água não viram capítulo nem subtópico. | `tests/test_extracao.py::test_ruido_nao_vira_capitulo_nem_subtopico` |
| RF-EXT-03 | Capítulo é identificado por rótulo (`Capítulo N:`, `Capítulo N ■`, `N.`), com o mesmo shape. | `tests/test_extracao.py::test_dois_formatos_de_rotulo_mesmo_shape` |
| RF-EXT-04 | Páginas do capítulo = próxima fronteira (capítulo **ou marcador**) − início. | `tests/test_extracao.py::test_fronteira_por_marcador`, `tests/test_extracao.py::test_fechar_paginas_sintetico`, `tests/test_extracao.py::test_richards_golden` |
| RF-EXT-05 | Front matter (romano ou arábico) fica fora da contagem. | `tests/test_extracao.py::test_front_matter_romano_e_arabico_fora_da_conta` |
| RF-EXT-06 | `paginas_conteudo <= paginas_fisicas` quando há ficha CIP. | `tests/test_extracao.py::test_sanidade_cip` |
| RF-EXT-07 | Extração ambígua retorna `422` com mensagem clara: sequência não crescente, capítulo sem fronteira final, PDF que parece o livro completo. | `tests/test_extracao.py::test_sequencia_nao_crescente_422`, `tests/test_extracao.py::test_capitulo_sem_fronteira_final_422`, `tests/test_extracao.py::test_livro_completo_422` |
| RF-EXT-08 | Título vem da ficha CIP; sem ficha, da maior fonte da capa. | `tests/test_extracao.py::test_richards_golden`, `tests/test_extracao.py::test_titulo_cip_com_barra_no_fim_da_linha` |
| RF-EXT-09 | PDF só de capa gera `Book` com `capitulos=[]` e sem páginas. | `tests/test_extracao.py::test_capa_isolada_sem_inventar_paginas` |
| RF-EXT-10 | Capa em imagem é transcrita por modelo de visão (título, subtítulo, autor, edição — nunca páginas). | `tests/test_classificador.py::test_capa_em_imagem`, `tests/test_classificador.py::test_capa_sem_titulo_e_invalida`, `tests/eval/test_propriedades.py::test_capa_jpeg` |

## Classificação (spec 2.6)

| ID | Requisito | Verificado por |
|---|---|---|
| RF-CLS-01 | Saída do LLM via structured output + Pydantic, com exatamente um julgamento por capítulo e peso em [0.5, 2.0]; fora disso, `502`. | `tests/test_classificador.py::test_resposta_valida_e_contrato_da_chamada`, `tests/test_classificador.py::test_capitulo_faltando_e_invalido`, `tests/test_classificador.py::test_peso_fora_da_faixa_e_invalido`, `tests/eval/test_propriedades.py::test_schema_100_por_cento` |
| RF-CLS-02 | Páginas nunca vão no prompt; subtópicos vão. | `tests/test_classificador.py::test_resposta_valida_e_contrato_da_chamada` |
| RF-CLS-03 | Classificação feita uma vez por livro e reaproveitada entre roadmaps. | `tests/test_api.py::test_fluxo_roadmap` |
| RF-CLS-04 | Modelo não configurado ou Ollama inacessível retorna `503`. | `tests/test_classificador.py::test_sem_modelo_configurado` |
| RF-CLS-05 | Variância das horas entre classificações repetidas < 15%. | `tests/eval/test_propriedades.py::test_variancia` |
| RF-CLS-06 | `linguagens` com nome curto; lista vazia quando o livro não ensina linguagem. | `tests/eval/test_propriedades.py::test_linguagens_vazia_em_livro_sem_linguagem`, `tests/eval/test_propriedades.py::test_baixo_nivel` |

## Cálculo e cronograma (spec 2.7–2.8)

| ID | Requisito | Verificado por |
|---|---|---|
| RF-CAL-01 | Horas por capítulo seguem a fórmula da spec 2.7, com constantes de `config.yaml`, e os fatores aplicados são persistidos. | `tests/test_calculo.py::test_formula_da_spec` |
| RF-CAL-02 | Senioridade filtra: capítulo com nível abaixo da senioridade sai do cronograma ([ADR 0002](adr/0002-senioridade-filtra-capitulos.md)). | `tests/test_calculo.py::test_senioridade_filtra_capitulos_abaixo_do_nivel`, `tests/test_calculo.py::test_livro_todo_abaixo_do_nivel_gera_plano_vazio`, `tests/eval/test_propriedades.py::test_diferenca_entre_niveis` |
| RF-CAL-03 | Monotonia: `h(Junior) ≥ h(Pleno) ≥ h(Senior)`; mais páginas com o mesmo peso ⇒ mais horas. | `tests/test_calculo.py::test_monotonia_senioridade`, `tests/test_calculo.py::test_monotonia_volume`, `tests/eval/test_propriedades.py::test_monotonia_senioridade`, `tests/eval/test_propriedades.py::test_monotonia_volume` |
| RF-CAL-04 | Fator de gap (livro acima da senioridade) e de baixo nível (C, Rust...). | `tests/test_calculo.py::test_gap_quando_livro_acima_da_senioridade`, `tests/test_calculo.py::test_baixo_nivel`, `tests/eval/test_propriedades.py::test_baixo_nivel` |
| RF-CRO-01 | `semanas = ceil(total / disponibilidade)`, capítulos em ordem, divididos só entre semanas adjacentes, `dias = min(7, ceil(disp / 2))`. | `tests/test_calculo.py::test_cronograma_coerente`, `tests/eval/test_propriedades.py::test_coerencia_aritmetica` |

## API

| ID | Requisito | Verificado por |
|---|---|---|
| RF-API-01 | `POST /books`: `201` ao criar, `200` para o mesmo arquivo (cache por hash), `415` para tipo não suportado, `422` para extração ambígua. | `tests/test_api.py::test_upload_book_cache_e_erros` |
| RF-API-02 | `POST /books/{id}/roadmaps`: `201`; `404` sem livro; `422` sem sumário ou com pedido inválido. | `tests/test_api.py::test_fluxo_roadmap` |
| RF-API-03 | `GET /roadmaps/{id}/explicar`: o LLM redige a partir dos fatores persistidos ou do motivo da exclusão; `404` para capítulo inexistente. | `tests/test_api.py::test_fluxo_roadmap`, `tests/test_classificador.py::test_explicar_recebe_dados_calculados` |

## RAG (spec 4) — implementado, desligado ([ADR 0003](adr/0003-rag-nao-aceito.md))

| ID | Requisito | Verificado por |
|---|---|---|
| RF-RAG-01 | Um chunk por capítulo (título + subtópicos), nunca corpo do livro. | `tests/test_api.py::test_fluxo_roadmap` |
| RF-RAG-02 | Referências só de **outros** livros já classificados, acima de `similaridade_min`. | `tests/test_api.py::test_referencias_so_de_outros_livros_classificados` |
| RF-RAG-03 | Com `rag.ativo: false`, o prompt é idêntico ao da baseline. | `tests/test_classificador.py::test_referencias_rag_entram_no_prompt_so_quando_existem` |
| RF-RAG-04 | Aceite só se a variância cair contra controle. | `tests/eval/test_rag.py::test_relatorio_rag` |

## Não funcionais

| ID | Requisito | Como se verifica |
|---|---|---|
| RNF-01 | Nenhuma aritmética sai do LLM; o único número julgado é o peso, limitado e validado. | [ADR 0001](adr/0001-aritmetica-fora-do-llm.md); RF-CLS-01, RF-CAL-01 |
| RNF-02 | Só sumário e capa são armazenados; nunca o corpo do livro. | RF-EXT-01, RF-RAG-01 |
| RNF-03 | LLM e embedding locais (Ollama); sem provider cloud. | `config.yaml` (`llm`), revisão |
| RNF-04 | Suíte padrão roda sem LLM e sem GPU. | `uv run pytest` com Ollama desligado |
| RNF-05 | Mudança de prompt ou constante só entra comparada contra `evals/baseline.json`, com controle que isole a mudança. | `tests/eval/test_propriedades.py::test_baseline` |
