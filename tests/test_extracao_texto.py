"""Extração de sumário a partir de .txt/.md: mesma convenção "Título ... página real do livro",
sem camada visual (sem fonte, sem marca d'água). Sintético — não depende de test_files/."""

import pytest

from src.extracao import ExtracaoAmbigua, extrair_texto

SUMARIO_TXT = """Fundamentos de Bancos de Dados

Sumário
Prefácio 9
Capítulo 1: Modelo relacional 15
Álgebra relacional 18
Normalização 25
Capítulo 2: SQL 40
Consultas básicas 42
Joins e subconsultas 55
Índice remissivo 70
"""

SUMARIO_MD = """# Fundamentos de Bancos de Dados

## Sumário
Prefácio 9
1. **Modelo relacional** 15
   - Álgebra relacional 18
   - Normalização 25
2. SQL 40
   - Consultas básicas 42
Índice remissivo 70
"""


def test_txt_mesma_convencao_da_pdf():
    ext = extrair_texto(SUMARIO_TXT.encode(), markdown=False)
    assert ext.titulo == "Fundamentos de Bancos de Dados"
    assert ext.origem == "sumario"
    assert [(c.num, c.titulo, c.paginas, c.subtopicos) for c in ext.capitulos] == [
        (1, "Modelo relacional", 25, ["Álgebra relacional", "Normalização"]),
        (2, "SQL", 30, ["Consultas básicas", "Joins e subconsultas"]),
    ]
    assert ext.paginas_conteudo == 55


def test_md_despoja_marcacao_mas_preserva_lista_numerada():
    ext = extrair_texto(SUMARIO_MD.encode(), markdown=True)
    assert ext.titulo == "Fundamentos de Bancos de Dados"  # veio do "# " H1, despojado
    assert [(c.num, c.titulo, c.paginas) for c in ext.capitulos] == [(1, "Modelo relacional", 25), (2, "SQL", 30)]
    assert ext.capitulos[0].subtopicos == ["Álgebra relacional", "Normalização"]  # bullets "- " removidos


def test_txt_sem_ancora_e_com_paragrafo_corrido_e_livro_completo():
    corpo = "\n".join(f"Este é um parágrafo bem longo de corpo de livro sem número de página nenhum, linha {i}." for i in range(15))
    with pytest.raises(ExtracaoAmbigua, match="corpo de livro"):
        extrair_texto(corpo.encode(), markdown=False)


def test_txt_so_titulo_vira_capa():
    ext = extrair_texto(b"JavaScript: O Guia Definitivo\n", markdown=False)
    assert (ext.origem, ext.capitulos, ext.titulo) == ("capa", [], "JavaScript: O Guia Definitivo")


def test_txt_nao_utf8_e_ambiguo():
    with pytest.raises(ExtracaoAmbigua, match="UTF-8"):
        extrair_texto("Sumário açúcar".encode("latin-1"), markdown=False)


SUMARIO_SEM_PAGINAS = """LLMs – As Partes Difíceis

Descrição do livro
Os LLMs transformaram o processamento de linguagem natural, mas implantá-los em aplicações introduz inúmeros desafios técnicos.

Sumário
Apresentação
Prefácio
CAPÍTULO 1: Princípios básicos: o que considerar antes de construir com LLMs
Por que open source?
Considerações estratégicas
CAPÍTULO 2: A lacuna da avaliação
Temperatura
CAPÍTULO 3: Epílogo: LLMBAs na era da queda dos custos de inferência
APÊNDICE: Ferramentas para implantação local de LLMs
Ollama
Índice remissivo
"""


def test_sumario_sem_paginacao_reconhece_capitulos_sem_inventar_paginas():
    ext = extrair_texto(SUMARIO_SEM_PAGINAS.encode(), markdown=False)
    assert (ext.titulo, ext.origem, ext.paginas_conteudo) == ("LLMs – As Partes Difíceis", "sumario", None)
    assert [(c.num, c.titulo, c.paginas, c.pag_inicio, c.subtopicos) for c in ext.capitulos] == [
        (1, "Princípios básicos: o que considerar antes de construir com LLMs", None, None, ["Por que open source?", "Considerações estratégicas"]),
        (2, "A lacuna da avaliação", None, None, ["Temperatura"]),
        (3, "Epílogo: LLMBAs na era da queda dos custos de inferência", None, None, []),  # apêndice não vira subtópico
    ]


def test_sumario_sem_paginacao_e_sem_capitulo_continua_422():
    with pytest.raises(ExtracaoAmbigua, match="nenhum capítulo reconhecido"):
        extrair_texto(b"Livro\n\nSumario\nIntroducao\nConclusao\n", markdown=False)


def test_txt_sanidade_cip_ainda_vale_se_o_texto_incluir_a_ficha():
    texto = (
        "Dados Internacionais de Catalogação na Publicação (CIP)\n"
        "Autor, Fulano\n"
        "Fundamentos de Bancos de Dados / Fulano Autor. -- São Paulo, 2025.\n"
        "50 p.\n\nSumário\nCapítulo 1: Modelo relacional 15\nÍndice remissivo 90\n"
    )
    with pytest.raises(ExtracaoAmbigua, match="ficha CIP"):
        extrair_texto(texto.encode(), markdown=False)
