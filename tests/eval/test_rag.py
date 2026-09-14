"""RAG medido contra o classificador sem referências (spec 4).

`uv run pytest -m eval tests/eval/test_rag.py` — grava evals/rag.json para comparar com o
julgamento humano. Critério de aceite: variância cai, ou tipo_livro bate mais com o humano.
"""

import asyncio
import json
from collections import Counter

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.classificador import Classificacao, Referencia, RespostaLLMInvalida, classificar
from src.config import config, settings
from src.models import Book
from src.rag import indexar, referencias
from tests.eval.golden import LIVRO_VARIANCIA, N_VARIANCIA, RAIZ, Resultado, classificar_golden, cv_horas, validos

pytestmark = pytest.mark.eval

RELATORIO = RAIZ / "evals" / "rag.json"
Rodada = tuple[dict[str, dict[int, list[Referencia]]], dict[str, Resultado], list[Classificacao], list[Classificacao]]


async def referencias_cruzadas(golden: dict[str, Resultado]) -> dict[str, dict[int, list[Referencia]]]:
    """Leave-one-out: cada livro só vê referências dos outros, julgados sem RAG. Tudo desfeito no fim."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as s:
            books = {
                nome: Book(
                    hash_fonte=f"eval-rag-{nome}",
                    titulo=ext.titulo,
                    origem="sumario",
                    paginas_conteudo=ext.paginas_conteudo,
                    capitulos=[c.model_dump() for c in ext.capitulos],
                    classificacao=cls.model_dump(),
                )
                for nome, (ext, cls) in validos(golden).items()
            }
            s.add_all(books.values())
            await s.flush()
            for nome, book in books.items():
                await indexar(s, book, golden[nome][0].capitulos)
            await s.flush()
            refs = {nome: await referencias(s, book.id, golden[nome][0].capitulos) for nome, book in books.items()}
            await s.rollback()
            return refs
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def rodada_rag(golden: dict[str, Resultado]) -> Rodada:
    async def rodar() -> Rodada:
        refs = await referencias_cruzadas(golden)
        livros = {n: e for n, (e, _) in golden.items()}
        com_rag = await classificar_golden(livros, refs)
        ext = livros[LIVRO_VARIANCIA]
        sem = [await classificar(ext.titulo, ext.capitulos) for _ in range(N_VARIANCIA)]
        com = [await classificar(ext.titulo, ext.capitulos, refs.get(LIVRO_VARIANCIA)) for _ in range(N_VARIANCIA)]
        return refs, com_rag, sem, com

    if LIVRO_VARIANCIA not in golden:
        pytest.skip("livro da variância ausente")
    return asyncio.run(rodar())


def perfil(cls: Classificacao | RespostaLLMInvalida) -> dict[str, object]:
    if not isinstance(cls, Classificacao):
        return {"erro": str(cls)}
    niveis = dict(Counter(c.nivel for c in cls.capitulos))
    return {"tipo_livro": cls.tipo_livro, "densidade": cls.densidade, "nivel_natural": cls.nivel_natural, "niveis": niveis}


def test_rag_encontra_referencias(rodada_rag: Rodada):
    total = sum(len(r) for por_cap in rodada_rag[0].values() for r in por_cap.values())
    assert total > 0, f"nenhuma referência acima de similaridade_min={config.rag.similaridade_min}"


def test_rag_schema_100_por_cento(rodada_rag: Rodada):
    falhas = {n: str(c) for n, (_, c) in rodada_rag[1].items() if not isinstance(c, Classificacao)}
    assert not falhas


def test_relatorio_rag(golden: dict[str, Resultado], rodada_rag: Rodada):
    refs, com_rag, sem, com = rodada_rag
    ext = golden[LIVRO_VARIANCIA][0]
    relatorio = {
        "modelo_embedding": config.llm.modelo_embedding,
        "similaridade_min": config.rag.similaridade_min,
        "referencias_por_capitulo": config.rag.referencias_por_capitulo,
        "variancia": {
            "livro": LIVRO_VARIANCIA,
            "n": N_VARIANCIA,
            "cv_sem_rag": round(cv_horas(ext, sem), 4),
            "cv_com_rag": round(cv_horas(ext, com), 4),
        },
        "livros": {
            nome: {
                "capitulos_com_referencia": f"{len(refs.get(nome, {}))}/{len(ext_livro.capitulos)}",
                "sem_rag": perfil(golden[nome][1]),
                "com_rag": perfil(com_rag[nome][1]),
                "exemplo_referencias": {
                    num: [f"{r.capitulo} ({r.livro}) {r.similaridade:.2f}" for r in rs] for num, rs in list(refs.get(nome, {}).items())[:3]
                },
            }
            for nome, (ext_livro, _) in golden.items()
        },
    }
    RELATORIO.parent.mkdir(exist_ok=True)
    RELATORIO.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n")
