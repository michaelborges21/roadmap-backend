"""Calculadora e cronograma: Python puro, sem LLM (spec 2.7–2.8 e property tests da spec 3)."""

from itertools import groupby
from math import ceil

import pytest

from src.calculo import SENIORIDADES, calcular, planejar
from src.classificador import CapituloClassificado, Classificacao, Nivel
from src.extracao import Capitulo


def caps(*paginas: int) -> list[Capitulo]:
    return [Capitulo(num=i + 1, titulo=f"Cap {i + 1}", pag_inicio=1, paginas=p) for i, p in enumerate(paginas)]


def cls(niveis: tuple[Nivel, ...], **kw: str | list[str]) -> Classificacao:
    base = {"tipo_livro": "hibrido", "densidade": "media", "nivel_natural": "iniciante", "linguagens": ["Python"]}
    return Classificacao.model_validate(
        {**base, **kw, "capitulos": [CapituloClassificado(num=i + 1, nivel=n, peso=1.0) for i, n in enumerate(niveis)]}
    )


def test_formula_da_spec():
    h = calcular(caps(20)[0], cls(("avancado",)), "Pleno")
    assert h.leitura == pytest.approx(20 * 2.5 / 60)
    assert h.codigo == pytest.approx(h.leitura * 0.4)
    assert h.escrita == pytest.approx(h.leitura * 0.15)
    assert h.fatores.model_dump() == {"senioridade": 1.0, "gap": 1.0, "baixo_nivel": 1.0, "peso": 1.0}


@pytest.mark.parametrize("niveis", [("avancado",) * 3, ("iniciante", "intermediario", "avancado")])
def test_monotonia_senioridade(niveis: tuple[Nivel, ...]):
    h = [planejar(caps(30, 20, 40), cls(niveis), s, 5).total_horas for s in SENIORIDADES]
    assert h[0] >= h[1] >= h[2]


def test_senioridade_filtra_capitulos_abaixo_do_nivel():
    planos = {s: planejar(caps(30, 20, 40), cls(("iniciante", "intermediario", "avancado")), s, 5) for s in SENIORIDADES}
    assert [c.num for c in planos["Junior"].capitulos] == [1, 2, 3]
    assert [c.num for c in planos["Pleno"].capitulos] == [2, 3]
    assert [c.num for c in planos["Senior"].capitulos] == [3]
    assert [e.num for e in planos["Senior"].excluidos] == [1, 2]


def test_livro_todo_abaixo_do_nivel_gera_plano_vazio():
    plano = planejar(caps(30, 20), cls(("iniciante", "iniciante")), "Senior", 5)
    assert (plano.capitulos, plano.semanas, plano.total_horas, plano.cronograma) == ([], 0, 0, [])


def test_monotonia_volume():
    c = cls(("avancado", "avancado"))
    menor, maior = (calcular(cap, c, "Pleno").total for cap in caps(10, 20))
    assert maior > menor


def test_baixo_nivel():
    assert calcular(caps(10)[0], cls(("avancado",), linguagens=["Rust"]), "Pleno").fatores.baixo_nivel == 1.3
    assert calcular(caps(10)[0], cls(("avancado",), linguagens=["Python"]), "Pleno").fatores.baixo_nivel == 1.0


def test_gap_quando_livro_acima_da_senioridade():
    c = cls(("avancado",), nivel_natural="avancado")
    assert calcular(caps(10)[0], c, "Junior").fatores.gap == 1.3
    assert calcular(caps(10)[0], c, "Senior").fatores.gap == 1.0


def test_cronograma_coerente():
    disponibilidade = 3.0
    plano = planejar(caps(30, 50, 20, 70), cls(("avancado",) * 4), "Junior", disponibilidade)
    assert plano.semanas == ceil(plano.total_horas / disponibilidade) == len(plano.cronograma)
    assert plano.semanas * disponibilidade >= plano.total_horas
    assert plano.dias_por_semana == 2
    assert all(s.horas <= disponibilidade + 1e-9 for s in plano.cronograma)

    trechos = [(t.num, s.semana, t.horas) for s in plano.cronograma for t in s.capitulos]
    assert [n for n, _, _ in trechos] == sorted(n for n, _, _ in trechos)  # ordem respeitada
    for num, grupo in groupby(trechos, key=lambda t: t[0]):
        itens = list(grupo)
        semanas = [s for _, s, _ in itens]
        assert semanas == list(range(semanas[0], semanas[0] + len(semanas)))  # só semanas adjacentes
        assert sum(h for _, _, h in itens) == pytest.approx(plano.capitulos[num - 1].total)
