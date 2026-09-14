import asyncio

import pytest

from tests.eval.golden import Resultado, classificar_golden, extrair_golden


@pytest.fixture(scope="session")
def golden() -> dict[str, Resultado]:
    """Golden set classificado sem RAG, uma vez por sessão."""
    livros = extrair_golden()
    if not livros:
        pytest.skip("golden set ausente em test_files/")
    return asyncio.run(classificar_golden(livros))
