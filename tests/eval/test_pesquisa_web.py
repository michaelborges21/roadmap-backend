"""Pesquisa real: SearXNG + páginas web + embeddinggemma + gemma4:12b (spec 2.9, ADR 0004).

`uv run pytest -m eval tests/eval/test_pesquisa_web.py`. Depende da web: um site que muda de layout
pode derrubar o teste sem que o código tenha regredido — confira o `justificativa` na falha.
"""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.extracao import Capitulo
from src.models import Book
from src.pesquisa import Pesquisa, pesquisar

pytestmark = pytest.mark.eval

# Livro real, sumário sem paginação. Editora e Amazon trazem 344 páginas; um post na web cita 488.
TITULO = "LLMs – As Partes Difíceis"
CAPS = [
    Capitulo(num=1, titulo="Princípios básicos: o que considerar antes de construir com LLMs", pag_inicio=None, paginas=None),
    Capitulo(num=2, titulo="A lacuna da avaliação", pag_inicio=None, paginas=None),
    Capitulo(num=3, titulo="Ferramentas de avaliação para aplicações baseadas em LLMs", pag_inicio=None, paginas=None),
    Capitulo(num=4, titulo="Dos dados ao contexto", pag_inicio=None, paginas=None),
]


def test_encontra_total_de_paginas_com_fonte_verificada():
    async def rodar() -> Pesquisa:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as s:
                book = Book(
                    hash_fonte=f"eval-pesquisa-{uuid.uuid4().hex}",
                    titulo=TITULO,
                    origem="sumario",
                    capitulos=[c.model_dump() for c in CAPS],
                )
                s.add(book)
                await s.flush()
                resultado = await pesquisar(s, book, CAPS)
                await s.rollback()  # nada persiste
                return resultado
        finally:
            await engine.dispose()

    p = asyncio.run(rodar())
    assert p.paginas_totais == 344, p
    assert p.url_fonte and p.url_fonte in p.fontes_consultadas
