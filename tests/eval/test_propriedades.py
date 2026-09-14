"""Property tests com LLM real (spec 3). Fora do CI padrão: `uv run pytest -m eval`.

O golden set é classificado UMA vez por sessão (tests/eval/conftest.py) e reaproveitado.
Baseline: `ATUALIZAR_BASELINE=1 uv run pytest -m eval` grava evals/baseline.json;
sem a variável, a rodada compara contra ele.
"""

import asyncio
import json
import os
from collections import Counter
from itertools import combinations

import pytest

from src.calculo import SENIORIDADES, calcular, planejar
from src.classificador import Classificacao, classificar, ler_capa
from src.extracao import Capitulo, Extraido
from tests.eval.golden import (
    DISPONIBILIDADE,
    FIXTURES,
    LIVRO_VARIANCIA,
    N_VARIANCIA,
    RAIZ,
    TOLERANCIA,
    Resultado,
    cv_horas,
    validos,
)

pytestmark = pytest.mark.eval

BASELINE = RAIZ / "evals" / "baseline.json"


def test_schema_100_por_cento(golden: dict[str, Resultado]):
    falhas = {n: str(c) for n, (_, c) in golden.items() if not isinstance(c, Classificacao)}
    assert not falhas


def test_monotonia_senioridade(golden: dict[str, Resultado]):
    for nome, (ext, cls) in validos(golden).items():
        h = [planejar(ext.capitulos, cls, s, DISPONIBILIDADE).total_horas for s in SENIORIDADES]
        assert h[0] >= h[1] >= h[2], nome


def test_diferenca_entre_niveis(golden: dict[str, Resultado]):
    niveis_por_livro = {}
    for nome, (ext, cls) in validos(golden).items():
        caps = {s: len(planejar(ext.capitulos, cls, s, DISPONIBILIDADE).capitulos) for s in SENIORIDADES}
        niveis_por_livro[nome] = {c.nivel for c in cls.capitulos}
        if cls.nivel_natural != "iniciante":
            assert caps["Junior"] > caps["Senior"], f"{nome}: senioridade não mudou os capítulos ({caps})"
    # "Tudo num nível só" em todo livro = modelo não diferencia (o projeto perde a razão de existir).
    assert any(len(n) > 1 for n in niveis_por_livro.values()), niveis_por_livro


def test_monotonia_volume(golden: dict[str, Resultado]):
    for nome, (ext, cls) in validos(golden).items():
        horas = {c.num: calcular(c, cls, "Pleno") for c in ext.capitulos}
        for a, b in combinations(ext.capitulos, 2):
            if horas[a.num].fatores.peso == horas[b.num].fatores.peso and a.paginas != b.paginas:
                maior, menor = (a, b) if a.paginas > b.paginas else (b, a)
                assert horas[maior.num].total > horas[menor.num].total, (nome, maior.num, menor.num)


def test_coerencia_aritmetica(golden: dict[str, Resultado]):
    for nome, (ext, cls) in validos(golden).items():
        for s in SENIORIDADES:
            plano = planejar(ext.capitulos, cls, s, DISPONIBILIDADE)
            assert plano.semanas * DISPONIBILIDADE >= plano.total_horas - 1e-9, (nome, s)


def test_baixo_nivel():
    caps = [
        Capitulo(num=1, titulo="Primeiros programas em C", pag_inicio=1, paginas=20, subtopicos=["Compilando com gcc", "printf e scanf"]),
        Capitulo(num=2, titulo="Ponteiros", pag_inicio=21, paginas=30, subtopicos=["Aritmética de ponteiros", "Ponteiros para função"]),
        Capitulo(num=3, titulo="Gerenciamento de memória", pag_inicio=51, paginas=25, subtopicos=["malloc e free", "Vazamentos de memória"]),
    ]
    cls = asyncio.run(classificar("Programação em C: do zero aos ponteiros", caps))
    assert calcular(caps[0], cls, "Pleno").fatores.baixo_nivel == 1.3, cls.linguagens


def test_linguagens_vazia_em_livro_sem_linguagem(golden: dict[str, Resultado]):
    if not isinstance(cls := golden.get("richards", (None, None))[1], Classificacao):
        pytest.skip("Richards sem classificação válida")
    assert cls.linguagens == [], cls.linguagens


def test_capa_jpeg():
    imagem = FIXTURES / "cb5c11e4-2eb1-4e05-97ed-9cbcb6f152ca.jpeg"
    if not imagem.exists():
        pytest.skip("capa ausente")
    capa = asyncio.run(ler_capa(imagem.read_bytes()))
    assert capa.titulo.strip().lower() == "javascript", capa
    assert "domine" not in (capa.subtitulo or "").lower(), capa  # slogan não é subtítulo


def test_variancia(golden: dict[str, Resultado]):
    if LIVRO_VARIANCIA not in golden:
        pytest.skip("livro da variância ausente")
    ext = golden[LIVRO_VARIANCIA][0]

    async def rodadas() -> list[Classificacao]:
        return [await classificar(ext.titulo, ext.capitulos) for _ in range(N_VARIANCIA)]

    cv = cv_horas(ext, asyncio.run(rodadas()))
    assert cv < TOLERANCIA, f"desvio {cv:.1%} em {N_VARIANCIA} rodadas"


def resumo(ext: Extraido, cls: Classificacao) -> dict[str, object]:
    return {
        "titulo": ext.titulo,
        "capitulos": len(ext.capitulos),
        "paginas_conteudo": ext.paginas_conteudo,
        "tipo_livro": cls.tipo_livro,
        "densidade": cls.densidade,
        "nivel_natural": cls.nivel_natural,
        "linguagens": cls.linguagens,
        "niveis": dict(Counter(c.nivel for c in cls.capitulos)),
        "senioridades": {
            s: {"capitulos": len(p.capitulos), "total_horas": round(p.total_horas, 2), "semanas": p.semanas}
            for s in SENIORIDADES
            if (p := planejar(ext.capitulos, cls, s, DISPONIBILIDADE))
        },
    }


def test_baseline(golden: dict[str, Resultado]):
    atual = {n: resumo(e, c) for n, (e, c) in validos(golden).items()}
    if os.environ.get("ATUALIZAR_BASELINE") or not BASELINE.exists():
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps(atual, ensure_ascii=False, indent=2) + "\n")
        return

    base = json.loads(BASELINE.read_text())
    diferencas = []
    for nome, b in base.items():
        if nome not in atual:
            diferencas.append(f"{nome}: sem classificação válida nesta rodada")
            continue
        a = atual[nome]
        diferencas += [f"{nome}.{k}: {b[k]} → {a[k]}" for k in ("tipo_livro", "densidade", "nivel_natural") if a[k] != b[k]]
        for s in SENIORIDADES:
            hb, ha = b["senioridades"][s]["total_horas"], a["senioridades"][s]["total_horas"]
            if hb and abs(ha - hb) / hb > TOLERANCIA:
                diferencas.append(f"{nome}.{s}.total_horas: {hb} → {ha}")
    assert not diferencas, "\n".join(diferencas)
