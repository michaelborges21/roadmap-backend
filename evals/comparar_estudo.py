"""Compara o tempo real de estudo e o corte por senioridade com o que o sistema previu.

Uso:
  1. Gere um roadmap (POST /books/{id}/roadmaps) e anote o `id` da resposta.
  2. Ao estudar, preencha evals/estudo.csv — uma linha por capítulo:

       roadmap_id,capitulo,minutos_reais,precisava_ler,observacao
       7,3,100,sim,exercícios longos
       7,1,,nao,já conhecia o assunto

     - minutos_reais: tempo total no capítulo (leitura + código + anotações); vazio se não estudou.
     - precisava_ler: sim/nao — você de fato precisava desse capítulo, estando ele no cronograma
       ou em `excluidos`?
  3. uv run python -m evals.comparar_estudo

Nada é alterado. A sugestão de calibração é só sugestão: mudar config.yaml é checkpoint 3.
"""

import asyncio
import csv
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from src.calculo import Plano
from src.config import config
from src.db import Session, engine
from src.models import Roadmap

ARQUIVO = Path(__file__).parent / "estudo.csv"
MINIMO_PARA_SUGERIR = 3


class Anotacao(BaseModel):
    roadmap_id: int
    capitulo: int
    minutos_reais: float | None = Field(default=None, gt=0)
    precisava_ler: Literal["sim", "nao"]
    observacao: str = ""


class Tempo(BaseModel):
    roadmap_id: int
    capitulo: int
    previsto_h: float
    real_h: float


class Relatorio(BaseModel):
    tempos: list[Tempo]
    razao_mediana: float | None  # real / previsto
    corte: dict[str, int]
    avisos: list[str]


def ler(caminho: Path) -> list[Anotacao]:
    with caminho.open(newline="", encoding="utf-8") as f:
        return [
            Anotacao.model_validate({k: (v or "").strip() or None if k == "minutos_reais" else (v or "").strip() for k, v in linha.items()})
            for linha in csv.DictReader(f)
        ]


def comparar(anotacoes: list[Anotacao], planos: dict[int, Plano]) -> Relatorio:
    tempos: list[Tempo] = []
    corte: Counter[str] = Counter()
    avisos: list[str] = []
    for a in anotacoes:
        plano = planos.get(a.roadmap_id)
        if plano is None:
            avisos.append(f"roadmap {a.roadmap_id} não encontrado no banco")
            continue
        horas = next((h for h in plano.capitulos if h.num == a.capitulo), None)
        excluido = any(e.num == a.capitulo for e in plano.excluidos)
        if horas is None and not excluido:
            avisos.append(f"capítulo {a.capitulo} não existe no roadmap {a.roadmap_id}")
            continue

        incluido, precisava = horas is not None, a.precisava_ler == "sim"
        corte[
            {
                (True, True): "no cronograma e precisava",
                (True, False): "no cronograma sem precisar",
                (False, True): "excluído mas precisava",
                (False, False): "excluído e não precisava",
            }[(incluido, precisava)]
        ] += 1
        if horas and a.minutos_reais:
            tempos.append(Tempo(roadmap_id=a.roadmap_id, capitulo=a.capitulo, previsto_h=horas.total, real_h=a.minutos_reais / 60))
        elif a.minutos_reais:
            avisos.append(f"capítulo {a.capitulo} (roadmap {a.roadmap_id}) estava excluído: sem previsão de tempo para comparar")

    razoes = [t.real_h / t.previsto_h for t in tempos if t.previsto_h > 0]
    return Relatorio(tempos=tempos, razao_mediana=statistics.median(razoes) if razoes else None, corte=dict(corte), avisos=avisos)


async def carregar_planos(ids: set[int]) -> dict[int, Plano]:
    try:
        async with Session() as s:
            linhas = await s.execute(select(Roadmap.id, Roadmap.plano).where(Roadmap.id.in_(ids)))
            return {roadmap_id: Plano.model_validate(plano) for roadmap_id, plano in linhas}
    finally:
        await engine.dispose()


def main() -> None:
    caminho = Path(sys.argv[1]) if len(sys.argv) > 1 else ARQUIVO
    anotacoes = ler(caminho)
    if not anotacoes:
        print(f"{caminho}: nenhuma anotação ainda. Veja o formato em evals/comparar_estudo.py.")
        return
    rel = comparar(anotacoes, asyncio.run(carregar_planos({a.roadmap_id for a in anotacoes})))

    print("== Tempo (capítulos do cronograma que você estudou)")
    for t in rel.tempos:
        print(f"  roadmap {t.roadmap_id} cap. {t.capitulo}: previsto {t.previsto_h:.1f}h | real {t.real_h:.1f}h | {t.real_h / t.previsto_h:.2f}x")
    if rel.razao_mediana is not None:
        print(f"  mediana real/previsto: {rel.razao_mediana:.2f}x")
        if len(rel.tempos) >= MINIMO_PARA_SUGERIR:
            # Todas as parcelas (leitura, código, escrita) são proporcionais aos minutos por página.
            sugestao = {k: round(v * rel.razao_mediana, 2) for k, v in config.calculo.min_pagina.items()}
            print(f"  sugestão (checkpoint 3, não aplicada): min_pagina {dict(config.calculo.min_pagina)} → {sugestao}")
        else:
            print(f"  (sugestão de calibração a partir de {MINIMO_PARA_SUGERIR} capítulos com tempo anotado)")

    print("\n== Corte por senioridade")
    for chave, n in sorted(rel.corte.items()):
        print(f"  {chave}: {n}")
    erros = rel.corte.get("no cronograma sem precisar", 0) + rel.corte.get("excluído mas precisava", 0)
    if rel.corte:
        print(f"  corte errado em {erros} de {sum(rel.corte.values())} capítulos")

    for aviso in rel.avisos:
        print(f"\n  aviso: {aviso}")


if __name__ == "__main__":
    main()
