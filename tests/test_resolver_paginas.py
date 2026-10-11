"""resolver_paginas: origem das páginas e avisos, sem banco nem LLM."""

from src.calculo import REGRA_DISTRIBUICAO
from src.extracao import Capitulo
from src.main import resolver_paginas
from src.pesquisa import Pesquisa

PAGINADOS = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=i * 20, paginas=20) for i in (1, 2, 3)]
SEM_PAGINAS = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=None, paginas=None, subtopicos=["s"] * i) for i in (1, 2, 3)]
FONTE = "https://editora.exemplo/livro"


def test_sumario_paginado_usa_as_paginas_do_sumario_sem_avisos():
    caps, origem, avisos = resolver_paginas(PAGINADOS, 60, None)
    assert caps == PAGINADOS and avisos == []
    assert (origem.origem, origem.paginas_totais) == ("sumario", 60)


def test_sumario_sem_paginas_distribui_a_fracao_do_total_pesquisado():
    pesquisa = Pesquisa(paginas_totais=100, url_fonte=FONTE, justificativa="ficha")
    caps, origem, avisos = resolver_paginas(SEM_PAGINAS, None, pesquisa)
    assert sum(c.paginas or 0 for c in caps) == origem.paginas_conteudo == 92
    assert (origem.origem, origem.paginas_totais, origem.url_fonte, origem.regra) == ("pesquisa", 100, FONTE, REGRA_DISTRIBUICAO)
    assert avisos == []


def test_avisa_quando_sumario_passa_do_total_da_fonte_e_lista_outras_contagens():
    pesquisa = Pesquisa(paginas_totais=50, url_fonte=FONTE, justificativa="ficha", outras_contagens=["448 páginas (loja.exemplo)"])
    _, origem, avisos = resolver_paginas(PAGINADOS, 60, pesquisa)
    assert origem.origem == "sumario"
    assert "edição diferente?" in avisos[0] and "448 páginas (loja.exemplo)" in avisos[1]
