"""Testes determinísticos de extração (spec 3) sobre PDFs reais em test_files/ (fora do git)."""

from functools import cache
from pathlib import Path

import pytest

from src import extracao
from src.extracao import ExtracaoAmbigua, classificar_linhas, extrair, fechar_paginas

FIXTURES = Path(__file__).parent.parent / "test_files"
RICHARDS = FIXTURES / "sumario-9788575229682.pdf"
GERON = FIXTURES / "AMOSTRA_MaosAObraAprendizadoDeMaquinaComScikit-LearnKerasTensorFlow.pdf"
CAPA_PDF = FIXTURES / "capa-9788575229170.pdf"  # página 1 do sumário da Nelson (pdfseparate)
HARRISON = FIXTURES / "sumario-9788575228173.pdf"


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
    # Sem isso o título caía no fallback de maior fonte da capa: "Al Sweigart".
    assert extraido(FIXTURES / "sumario-9788575229644.pdf").titulo == "Automatize tarefas maçantes com Python"


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
    assert "Engenharia de Software para Cientistas de Dados" in ext.titulo


def test_capitulo_sem_fronteira_final_422():
    if not HARRISON.exists():
        pytest.skip("fixture ausente")
    with pytest.raises(ExtracaoAmbigua, match="sem fronteira final"):
        extrair(HARRISON.read_bytes())


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
