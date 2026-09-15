# Requisitos — roadmapAPI

Spec de implementação: [`roadmapapi-spec.md`](../roadmapapi-spec.md). Contrato HTTP: [`api.md`](api.md).
Decisões: [`adr/`](adr/).

Todo requisito funcional aponta para o teste que o verifica. `tests/test_specs.py` falha se uma
referência apontar para teste inexistente ou se um RF ficar sem teste.

- `tests/*.py` — suíte padrão, LLM, web e embedding mockados: `uv run pytest` (~8s).
- `tests/eval/*.py` — LLM e web reais, fora do CI: `uv run pytest -m eval` (~4 min propriedades,
  ~1–2 min pesquisa na web).

## Extração (spec 2.1–2.5)

| ID | Requisito | Verificado por |
|---|---|---|
| RF-EXT-01 | Só a janela do sumário é lida; prefácio e capítulos de amostra são descartados antes de qualquer processamento. | `tests/test_extracao.py::test_amostra_com_corpo_le_so_sumario` |
| RF-EXT-02 | Cabeçalho, rodapé e marca d'água não viram capítulo nem subtópico. | `tests/test_extracao.py::test_ruido_nao_vira_capitulo_nem_subtopico` |
| RF-EXT-03 | Capítulo é identificado por rótulo (`Capítulo N:`, `Capítulo N ■`, `Capítulo N ▪`, `N.`), com o mesmo shape. Rótulo só com o número (`1 Título 25`) vale apenas se nenhum outro rótulo casa no sumário inteiro. | `tests/test_extracao.py::test_dois_formatos_de_rotulo_mesmo_shape`, `tests/test_extracao_rotulos.py::test_separador_quadradinho_no_rotulo`, `tests/test_extracao_rotulos.py::test_rotulo_so_com_numero_quando_nao_ha_outro_rotulo`, `tests/test_extracao_rotulos.py::test_numero_solto_nao_vira_capitulo_em_livro_com_rotulo_normal`, `tests/test_extracao_rotulos.py::test_numero_solto_com_numeracao_quebrada_e_422`, `tests/test_extracao.py::test_rotulo_so_com_numero_e_arquivo_sem_titulo`, `tests/test_extracao.py::test_pdf_que_comeca_no_sumario_usa_cabecalho_como_titulo` |
| RF-EXT-04 | Páginas do capítulo = próxima fronteira (capítulo **ou marcador**) − início. | `tests/test_extracao.py::test_fronteira_por_marcador`, `tests/test_extracao.py::test_fechar_paginas_sintetico`, `tests/test_extracao.py::test_richards_golden` |
| RF-EXT-05 | Front matter (romano ou arábico) fica fora da contagem. | `tests/test_extracao.py::test_front_matter_romano_e_arabico_fora_da_conta` |
| RF-EXT-06 | `paginas_conteudo <= paginas_fisicas` quando há ficha CIP. | `tests/test_extracao.py::test_sanidade_cip`, `tests/test_extracao_texto.py::test_txt_sanidade_cip_ainda_vale_se_o_texto_incluir_a_ficha` |
| RF-EXT-07 | Extração ambígua retorna `422` com mensagem clara: sequência não crescente, capítulo sem fronteira final, PDF que parece o livro completo. | `tests/test_extracao.py::test_sequencia_nao_crescente_422`, `tests/test_extracao.py::test_capitulo_sem_fronteira_final_422`, `tests/test_extracao.py::test_livro_completo_422` |
| RF-EXT-08 | Título vem da ficha CIP. Sem ficha, no PDF: da maior fonte da página 1 (nunca a própria palavra "Sumário") e depois do cabeçalho corrido que se repete nas páginas. No texto montado pelo usuário: da marcação `Titulo: Nome` (também `Título do livro:` e `Nome do livro:`) em qualquer linha antes do sumário — `Título original:` não conta —, senão da 1ª linha. Sem nenhum desses, `422` com instrução — nunca um título inventado, que levaria a pesquisa ao livro errado. | `tests/test_extracao.py::test_richards_golden`, `tests/test_extracao.py::test_titulo_cip_com_barra_no_fim_da_linha`, `tests/test_extracao_texto.py::test_txt_mesma_convencao_da_pdf`, `tests/test_extracao_rotulos.py::test_titulo_do_cabecalho_corrido`, `tests/test_extracao_rotulos.py::test_sem_cabecalho_repetido_nao_ha_titulo`, `tests/test_extracao_texto.py::test_marcacao_de_titulo_escrita_pelo_usuario`, `tests/test_extracao_texto.py::test_marcacao_de_titulo_fora_da_primeira_linha`, `tests/test_extracao_texto.py::test_titulo_original_da_ficha_nao_e_marcacao` |
| RF-EXT-09 | Arquivo só de capa gera `Book` com `capitulos=[]` e sem páginas. | `tests/test_extracao.py::test_capa_isolada_sem_inventar_paginas`, `tests/test_extracao_texto.py::test_txt_so_titulo_vira_capa` |
| RF-EXT-10 | Capa em imagem é transcrita por modelo de visão (título, subtítulo, autor, edição — nunca páginas). | `tests/test_classificador.py::test_capa_em_imagem`, `tests/test_classificador.py::test_capa_sem_titulo_e_invalida`, `tests/eval/test_propriedades.py::test_capa_jpeg` |
| RF-EXT-11 | `.txt`/`.md` seguem as mesmas regras do sumário em PDF, sem fonte, marca d'água nem checagem de "livro completo" por nº de páginas do arquivo. Markdown é despojado sem perder a lista numerada. Detecção por `content_type` ou extensão. | `tests/test_extracao_texto.py::test_txt_mesma_convencao_da_pdf`, `tests/test_extracao_texto.py::test_md_despoja_marcacao_mas_preserva_lista_numerada`, `tests/test_api.py::test_upload_texto_capa_cache_e_erros` |
| RF-EXT-12 | Sumário sem paginação (nenhuma linha termina em página arábica): capítulos por rótulo, subtópicos por posição, apresentação/prefácio/apêndice fora. A lista para no índice remissivo ou em texto corrido. Páginas ficam nulas até a pesquisa (RF-PES-01) — nunca inventadas na extração. | `tests/test_extracao_texto.py::test_sumario_sem_paginacao_reconhece_capitulos_sem_inventar_paginas`, `tests/test_extracao_texto.py::test_sumario_sem_paginacao_e_sem_capitulo_continua_422`, `tests/test_api.py::test_upload_texto_capa_cache_e_erros` |

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

## Pesquisa de fatos e base RAG (spec 2.9 e 4, [ADR 0004](adr/0004-pesquisa-web-com-fatos-verificaveis.md))

| ID | Requisito | Verificado por |
|---|---|---|
| RF-PES-01 | Sumário sem paginação: o total de páginas vem da pesquisa na web; só a fração de conteúdo calibrada (`calculo.fracao_conteudo_pesquisa`, 92%) é repartida pelo Python entre os capítulos, na proporção de 1 + nº de subtópicos, com soma exata. O plano registra total, conteúdo, fonte e regra. | `tests/test_distribuir_paginas.py::test_soma_fecha_exatamente_no_total`, `tests/test_distribuir_paginas.py::test_proporcional_a_um_mais_subtopicos`, `tests/test_distribuir_paginas.py::test_conteudo_e_a_fracao_calibrada_do_total_pesquisado`, `tests/test_api.py::test_roadmap_de_sumario_sem_paginas` |
| RF-PES-02 | O modelo só escolhe a fonte e transcreve o número. O Python só o aceita se o modelo disser que o trecho é deste livro, se o número estiver escrito no trecho da URL citada e se for plausível para o nº de capítulos. | `tests/test_pesquisa.py::test_aceita_numero_escrito_na_fonte_citada_e_lista_divergencias`, `tests/test_pesquisa.py::test_descarta_numero_que_nao_esta_na_fonte_citada`, `tests/test_pesquisa.py::test_descarta_numero_de_outra_fonte_que_nao_a_citada`, `tests/test_pesquisa.py::test_descarta_url_que_nao_foi_consultada`, `tests/test_pesquisa.py::test_respeita_quando_o_modelo_diz_que_nao_e_o_mesmo_livro`, `tests/test_pesquisa.py::test_descarta_total_implausivel` |
| RF-PES-03 | A base RAG (pgvector) guarda só janelas de texto em volta de "páginas" que trazem uma contagem, com a URL de origem — ficha técnica, nunca corpo de livro. Na segunda pesquisa do mesmo livro, a base é reaproveitada sem voltar à web. | `tests/test_pesquisa.py::test_trechos_guardam_so_a_ficha_em_volta_de_paginas`, `tests/test_pesquisa.py::test_contagem_le_formatos_reais_e_ignora_isbn`, `tests/test_pesquisa.py::test_ano_antes_do_rotulo_paginas_nao_e_contagem`, `tests/test_api.py::test_pesquisa_guarda_trechos_na_base_e_reaproveita` |
| RF-PES-04 | Contagens de páginas divergentes vistas em trechos que o modelo reconheceu como deste livro viram aviso no plano, não somem. Trecho de outro livro não gera aviso; domínio de rede social (`pesquisa.dominios_ignorados`) e página de e-book/Kindle (`pesquisa.padroes_url_ignorados`, conta páginas de tela) nem entram na base. | `tests/test_pesquisa.py::test_aceita_numero_escrito_na_fonte_citada_e_lista_divergencias`, `tests/test_pesquisa.py::test_contagem_de_outro_livro_nao_vira_aviso`, `tests/test_pesquisa.py::test_rede_social_nunca_e_fonte`, `tests/test_pesquisa.py::test_pagina_de_ebook_nunca_e_fonte`, `tests/test_api.py::test_pesquisa_guarda_trechos_na_base_e_reaproveita`, `tests/test_api.py::test_roadmap_de_sumario_sem_paginas` |
| RF-PES-05 | Sem páginas no sumário e sem fonte confiável → `422`. Pesquisa fora do ar → `503` se o roadmap depende dela; aviso no plano se o sumário é paginado. | `tests/test_api.py::test_roadmap_de_sumario_sem_paginas` |
| RF-PES-06 | Com web e modelo reais, um livro sem paginação tem o total encontrado com fonte verificada. | `tests/eval/test_pesquisa_web.py::test_encontra_total_de_paginas_com_fonte_verificada` |

## API

| ID | Requisito | Verificado por |
|---|---|---|
| RF-API-01 | `POST /books`: `201` ao criar, `200` para o mesmo arquivo (cache por hash), `415` para tipo não suportado, `422` para extração ambígua. | `tests/test_api.py::test_upload_texto_capa_cache_e_erros`, `tests/test_api.py::test_upload_pdf_cache_e_erros` |
| RF-API-02 | `POST /books/{id}/roadmaps`: `201`; `404` sem livro; `422` sem sumário ou com pedido inválido; o plano traz `paginas` (origem) e `avisos`. | `tests/test_api.py::test_fluxo_roadmap`, `tests/test_api.py::test_roadmap_de_sumario_sem_paginas` |
| RF-API-03 | `GET /roadmaps/{id}/explicar`: o LLM redige a partir dos fatores persistidos ou do motivo da exclusão; `404` para capítulo inexistente. | `tests/test_api.py::test_fluxo_roadmap`, `tests/test_classificador.py::test_explicar_recebe_dados_calculados` |

## Não funcionais

| ID | Requisito | Como se verifica |
|---|---|---|
| RNF-01 | Nenhuma aritmética sai do LLM. Ele julga categorias e peso (limitado e validado) e, na pesquisa, só escolhe a fonte e transcreve o número. | [ADR 0001](adr/0001-aritmetica-fora-do-llm.md), [ADR 0004](adr/0004-pesquisa-web-com-fatos-verificaveis.md); RF-CLS-01, RF-CAL-01, RF-PES-02 |
| RNF-02 | Só sumário, capa e ficha técnica pesquisada são armazenados; nunca o corpo do livro. | RF-EXT-01, RF-PES-03 |
| RNF-03 | LLM, embedding e busca na web locais e gratuitos (Ollama, SearXNG); sem provider cloud nem API paga. | `config.yaml` (`llm`, `pesquisa`), `docker-compose.yml`, revisão |
| RNF-04 | Suíte padrão roda sem LLM, sem GPU e sem internet. | `uv run pytest` com Ollama e SearXNG desligados |
| RNF-05 | Mudança de prompt ou constante só entra comparada contra `evals/baseline.json`, com controle que isole a mudança. | `tests/eval/test_propriedades.py::test_baseline` |
