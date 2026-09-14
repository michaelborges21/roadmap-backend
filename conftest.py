# Na raiz: coloca o projeto no sys.path para `from src...` nos testes.
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "eval: usa LLM real; só roda com `pytest -m eval`")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if "eval" in (config.getoption("-m") or ""):
        return
    pular = pytest.mark.skip(reason="LLM real: rode com `pytest -m eval`")
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(pular)
