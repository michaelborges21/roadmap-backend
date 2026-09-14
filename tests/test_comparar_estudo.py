"""Comparação do diário de estudo com o previsto: lógica pura, sem banco e sem LLM."""

from pathlib import Path

import pytest

from evals.comparar_estudo import Anotacao, comparar, ler
from src.calculo import planejar
from src.classificador import CapituloClassificado, Classificacao
from src.extracao import Capitulo

CAPS = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=1, paginas=p) for i, p in [(1, 30), (2, 20), (3, 40)]]
CLS = Classificacao(
    tipo_livro="hibrido",
    densidade="media",
    nivel_natural="intermediario",
    linguagens=[],
    capitulos=[CapituloClassificado(num=i, nivel=n, peso=1.0) for i, n in [(1, "iniciante"), (2, "intermediario"), (3, "avancado")]],
)
PLANO = planejar(CAPS, CLS, "Pleno", 5)  # Pleno: cap. 1 (iniciante) fica em `excluidos`


def previsto_min(num: int) -> float:
    return next(h.total for h in PLANO.capitulos if h.num == num) * 60


def test_razao_de_tempo_e_matriz_do_corte():
    anotacoes = [
        Anotacao(roadmap_id=7, capitulo=2, minutos_reais=previsto_min(2) * 2, precisava_ler="sim"),
        Anotacao(roadmap_id=7, capitulo=3, minutos_reais=previsto_min(3), precisava_ler="nao"),
        Anotacao(roadmap_id=7, capitulo=1, minutos_reais=None, precisava_ler="sim"),
    ]
    rel = comparar(anotacoes, {7: PLANO})
    assert [round(t.real_h / t.previsto_h, 2) for t in rel.tempos] == [2.0, 1.0]
    assert rel.razao_mediana == pytest.approx(1.5)
    assert rel.corte == {"no cronograma e precisava": 1, "no cronograma sem precisar": 1, "excluído mas precisava": 1}


def test_avisos_sem_quebrar():
    rel = comparar(
        [
            Anotacao(roadmap_id=99, capitulo=1, precisava_ler="sim"),
            Anotacao(roadmap_id=7, capitulo=42, precisava_ler="sim"),
            Anotacao(roadmap_id=7, capitulo=1, minutos_reais=30, precisava_ler="sim"),  # excluído: sem previsão
        ],
        {7: PLANO},
    )
    assert rel.tempos == [] and rel.razao_mediana is None
    assert len(rel.avisos) == 3


def test_ler_csv_com_minutos_vazio(tmp_path: Path):
    arquivo = tmp_path / "estudo.csv"
    arquivo.write_text("roadmap_id,capitulo,minutos_reais,precisava_ler,observacao\n7,3,100,sim,longo\n7,1,,nao,\n", encoding="utf-8")
    assert [(a.capitulo, a.minutos_reais, a.precisava_ler) for a in ler(arquivo)] == [(3, 100.0, "sim"), (1, None, "nao")]


def test_modelo_do_repositorio_esta_vazio_e_valido():
    assert ler(Path(__file__).parent.parent / "evals" / "estudo.csv") == []
