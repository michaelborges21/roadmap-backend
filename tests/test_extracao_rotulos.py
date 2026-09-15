"""Variações de rótulo e de título vistas em sumários reais de editora. Sintético: não depende de PDF."""

import pytest

from src.extracao import ExtracaoAmbigua, classificar_linhas, extrair_texto, fechar_paginas, titulo_cabecalho


def test_separador_quadradinho_no_rotulo():
    linhas = ["Parte I ▪ Construindo scrapers 17", "Capítulo 1 ▪ Como a internet funciona 18", "Redes 19",
              "Capítulo 2 ▪ Legalidade e ética 34", "Índice 50"]
    caps, _ = fechar_paginas(classificar_linhas(linhas))
    assert [(c.titulo, c.paginas) for c in caps] == [("Como a internet funciona", 16), ("Legalidade e ética", 16)]


def test_rotulo_so_com_numero_quando_nao_ha_outro_rotulo():
    texto = (
        "Entendendo Algoritmos\n\nSumário\nApresentação 13\nPrefácio 15\nAgradecimentos 17\n"
        "1 Introdução a algoritmos 25\nPesquisa binária 27\n2 Ordenação por seleção 45\nArrays 50\n"
        "3 Recursão 65\nÍndice 80\n"
    )
    ext = extrair_texto(texto.encode(), markdown=False)
    assert [(c.num, c.titulo, c.paginas, c.subtopicos) for c in ext.capitulos] == [
        (1, "Introdução a algoritmos", 20, ["Pesquisa binária"]),
        (2, "Ordenação por seleção", 20, ["Arrays"]),
        (3, "Recursão", 15, []),
    ]


def test_numero_solto_nao_vira_capitulo_em_livro_com_rotulo_normal():
    texto = "Livro\n\nSumário\nCapítulo 1: A 10\n3 dicas de estudo 12\nCapítulo 2: B 20\nÍndice 30\n"
    ext = extrair_texto(texto.encode(), markdown=False)
    assert [(c.num, c.subtopicos) for c in ext.capitulos] == [(1, ["3 dicas de estudo"]), (2, [])]


def test_numero_solto_com_numeracao_quebrada_e_422():
    texto = "Livro\n\nSumário\nIntrodução 5\n10 dicas para estudar 7\n2 Coisas 9\nÍndice 12\n"
    with pytest.raises(ExtracaoAmbigua, match="lacunas ou repetições"):
        extrair_texto(texto.encode(), markdown=False)


def test_titulo_do_cabecalho_corrido():
    paginas = [
        ["Sumário", "Prefácio 9"],
        ["4 Web Scraping com Python – 3ª Edição", "Redes 19"],
        ["Sumário 5", "Camada física 20"],
        ["6 Web Scraping com Python – 3ª Edição", "APIs 30"],
    ]
    assert titulo_cabecalho(paginas) == "Web Scraping com Python – 3ª Edição"
    com_barra = [["Sumário"], ["6 | O Livro de Produto", "x 1"], ["Sumário | 7", "y 2"], ["8 | O Livro de Produto", "z 3"]]
    assert titulo_cabecalho(com_barra) == "O Livro de Produto"


def test_sem_cabecalho_repetido_nao_ha_titulo():
    assert titulo_cabecalho([["Sumário", "Prefácio 9"], ["8 Sumário", "Arrays 50"], ["Escolha um modelo 66"]]) is None
