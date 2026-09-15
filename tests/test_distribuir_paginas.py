"""Distribuição do total pesquisado entre capítulos de um sumário sem paginação (spec 2.9)."""

from src.calculo import distribuir_paginas
from src.extracao import Capitulo


def caps(*n_subtopicos: int) -> list[Capitulo]:
    return [
        Capitulo(num=i + 1, titulo=f"Cap {i + 1}", pag_inicio=None, paginas=None, subtopicos=["s"] * n)
        for i, n in enumerate(n_subtopicos)
    ]


def test_soma_fecha_exatamente_no_total():
    for total in (97, 120, 344, 1000):
        assert sum(c.paginas for c in distribuir_paginas(caps(3, 0, 7, 12, 1), total)) == total  # type: ignore[misc]


def test_proporcional_a_um_mais_subtopicos():
    # pesos 2, 3, 4 (de 1, 2, 3 subtópicos) sobre 120: 26,67 / 40 / 53,33 → maior resto vai para o 1º.
    assert [c.paginas for c in distribuir_paginas(caps(1, 2, 3), 120)] == [27, 40, 53]


def test_capitulo_sem_subtopico_ainda_recebe_paginas_e_ordem_e_preservada():
    resultado = distribuir_paginas(caps(10, 0), 50)
    assert [c.num for c in resultado] == [1, 2]
    assert all(c.paginas and c.paginas >= 1 for c in resultado)


def test_nao_altera_os_capitulos_originais():
    originais = caps(1, 1)
    distribuir_paginas(originais, 10)
    assert [c.paginas for c in originais] == [None, None]
