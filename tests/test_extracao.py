"""Testes determinísticos de extração (spec 3) sobre PDFs reais em test_files/ (fora do git)."""

from functools import cache
from pathlib import Path

import pytest

from src import extracao
from src.extracao import ExtracaoAmbigua, classificar_linhas, extrair, fechar_paginas, titulo_cip

FIXTURES = Path(__file__).parent.parent / "test_files"
RICHARDS = FIXTURES / "sumario-9788575229682.pdf"  # "Capítulo N:", prefácio arábico, partes com página própria
GERON = FIXTURES / "AMOSTRA_MaosAObraAprendizadoDeMaquinaComScikit-LearnKerasTensorFlow.pdf"  # "N.", marca d'água, CIP, amostra com corpo
WEB_SCRAPING = FIXTURES / "sumario-9788575229231.pdf"  # "Capítulo N ▪", começa no sumário, título no cabeçalho corrido
ENGENHEIRO = FIXTURES / "sumario-9788575229989.pdf"  # "CAPÍTULO N:", começa no sumário, cabeçalho "6 | Título"
ALGORITMOS = FIXTURES / "sumario-9788575229293.pdf"  # rótulo só com número; o título não aparece em lugar nenhum
CAPA_PDF = FIXTURES / "capa-9788575229965.pdf"  # página 1 do sumário de Engenharia de IA (pdfseparate)


@cache
def extraido(caminho: Path) -> extracao.Extraido:
    if not caminho.exists():
        pytest.skip(f"fixture ausente: {caminho.name}")
    return extrair(caminho.read_bytes())


def cap(caminho: Path, num: int) -> extracao.Capitulo:
    return next(c for c in extraido(caminho).capitulos if c.num == num)


def test_fronteira_por_marcador():
    assert cap(RICHARDS, 8).paginas == 20  # Parte II (133), não cap. 9 (134)
    assert cap(GERON, 9).paginas == 28  # PARTE II (225), não cap. 10 (227)


def test_richards_golden():
    ext = extraido(RICHARDS)
    assert len(ext.capitulos) == 27
    assert ext.paginas_conteudo == 454
    assert (cap(RICHARDS, 15).paginas, cap(RICHARDS, 23).paginas) == (52, 8)
    assert ext.titulo == "Fundamentos da arquitetura de software"


def test_titulo_cip_com_barra_no_fim_da_linha():
    # Linhas reais de uma ficha CIP: a barra fecha a linha. Sem a regra, o título caía no fallback de maior fonte.
    linhas = [
        "Dados Internacionais de Catalogação na Publicação (CIP)",
        "(Câmara Brasileira do Livro, SP, Brasil)",
        "Sweigart, Al",
        "Automatize tarefas maçantes com Python :",
        "programação prática para verdadeiros iniciantes /",
        "Al Sweigart ; tradução Márcio Martins. -- 3. ed. --",
    ]
    assert titulo_cip(linhas) == "Automatize tarefas maçantes com Python"


def test_amostra_com_corpo_le_so_sumario():
    ext = extraido(GERON)
    assert len(ext.capitulos) == 19
    assert ext.capitulos[-1].subtopicos[-1] == "Obrigado!"
    todos = {s for c in ext.capitulos for s in c.subtopicos}
    assert "O Tsunami do Aprendizado de Máquina" not in todos  # 1ª seção do prefácio da amostra


def test_ruido_nao_vira_capitulo_nem_subtopico():
    for caminho in (RICHARDS, GERON):
        textos = [t for c in extraido(caminho).capitulos for t in [c.titulo, *c.subtopicos]]
        for ruido in ("CG_HandOnMachine", "Criado em", "Sumário |", "| Fundamentos da Arquitetura", "Amostra"):
            assert not any(ruido in t for t in textos), ruido
        # Letras soltas da marca d'água "Amostra" não podem vazar para dentro de palavras.
        assert "Por que Usar o Aprendizado de Máquina?" in cap(GERON, 1).subtopicos


def test_dois_formatos_de_rotulo_mesmo_shape():
    r, g = cap(RICHARDS, 1), cap(GERON, 1)
    assert r.model_dump().keys() == g.model_dump().keys()
    assert (r.titulo, g.titulo) == ("Introdução", "O Cenário do Aprendizado de Máquina")


def test_front_matter_romano_e_arabico_fora_da_conta():
    # Prefácio xv (Géron) e 15 (Richards): contagem começa no 1º capítulo.
    assert extraido(GERON).capitulos[0].pag_inicio == 2
    assert extraido(RICHARDS).capitulos[0].pag_inicio == 20


def test_sanidade_cip():
    ext = extraido(GERON)
    assert ext.paginas_fisicas == 640
    assert ext.paginas_conteudo == 592 <= ext.paginas_fisicas


def test_capa_isolada_sem_inventar_paginas():
    ext = extraido(CAPA_PDF)
    assert ext.origem == "capa"
    assert ext.capitulos == [] and ext.paginas_conteudo is None
    assert "Engenharia de IA" in ext.titulo


def test_pdf_que_comeca_no_sumario_usa_cabecalho_como_titulo():
    ws = extraido(WEB_SCRAPING)
    assert (ws.titulo, len(ws.capitulos), ws.paginas_conteudo) == ("Web Scraping com Python – 3ª Edição", 20, 357)
    assert ws.capitulos[0].titulo == "Como a internet funciona"  # "Capítulo 1 ▪ ...": sem o "▪" no título
    eng = extraido(ENGENHEIRO)
    assert (eng.titulo, len(eng.capitulos), eng.paginas_conteudo) == ("O Engenheiro de Software com Mentalidade de Produto", 9, 238)


def test_rotulo_so_com_numero_e_arquivo_sem_titulo(monkeypatch: pytest.MonkeyPatch):
    if not ALGORITMOS.exists():
        pytest.skip(f"fixture ausente: {ALGORITMOS.name}")
    with pytest.raises(ExtracaoAmbigua, match="sem título legível"):
        extrair(ALGORITMOS.read_bytes())  # sem capa, ficha CIP nem cabeçalho: não inventa título

    # Com um título qualquer, os capítulos de rótulo só com número ("1 Introdução a algoritmos 25") são lidos.
    monkeypatch.setattr(extracao, "titulo_cabecalho", lambda _paginas: "Livro de algoritmos")
    ext = extrair(ALGORITMOS.read_bytes())
    assert (ext.capitulos[0].num, ext.capitulos[0].titulo, ext.capitulos[0].pag_inicio) == (1, "Introdução a algoritmos", 25)
    assert [c.num for c in ext.capitulos] == list(range(1, len(ext.capitulos) + 1)) and len(ext.capitulos) >= 3


def test_capitulo_sem_fronteira_final_422():
    with pytest.raises(ExtracaoAmbigua, match="sem fronteira final"):
        fechar_paginas(classificar_linhas(["Capítulo 1: A 10", "Capítulo 2: B 20"]))


def test_livro_completo_422(monkeypatch: pytest.MonkeyPatch):
    extraido(GERON)  # garante fixture
    monkeypatch.setattr(extracao.cfg, "livro_completo_fracao", 35 / 592)
    with pytest.raises(ExtracaoAmbigua, match="livro completo"):
        extrair(GERON.read_bytes())


def test_sequencia_nao_crescente_422():
    with pytest.raises(ExtracaoAmbigua, match="não crescente"):
        fechar_paginas(classificar_linhas(["Capítulo 1: A 10", "Capítulo 2: B 5", "Índice 20"]))


def test_fechar_paginas_sintetico():
    linhas = ["Prefácio xi", "Capítulo 1: A 1", "Seção 1.1 5", "Parte II 299", "Capítulo 2: B 300", "Índice 900"]
    caps, conteudo = fechar_paginas(classificar_linhas(linhas))
    assert [(c.paginas, c.subtopicos) for c in caps] == [(298, ["Seção 1.1"]), (600, [])]
    assert conteudo == 899
