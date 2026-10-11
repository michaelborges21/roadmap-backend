"""Robustez da API: limite de upload, status de erro por herança e health check. Não precisa de banco."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src import main
from src.classificador import LLMIndisponivel


class LLMSemModelo(LLMIndisponivel):
    """Subclasse futura de um erro de domínio: deve herdar o status do pai."""


class SessaoFalsa:
    async def execute(self, *_: object) -> None:
        return None


def cliente() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://teste")


@pytest.mark.asyncio
async def test_upload_acima_do_limite_da_413(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main.config.api, "upload_max_mb", 0.001)  # ~1 KB
    async with cliente() as http:
        grande = await http.post("/books", files={"arquivo": ("grande.txt", b"x" * 2048, "text/plain")})
    assert grande.status_code == 413 and "limite" in grande.json()["detail"]


@pytest.mark.asyncio
async def test_subclasse_de_erro_de_dominio_herda_o_status():
    resposta = await main.erro_de_dominio(None, LLMSemModelo("modelo ausente"))  # type: ignore[arg-type]
    assert resposta.status_code == 503


@pytest.mark.asyncio
async def test_health_200_com_tudo_ok_e_503_com_dependencia_fora(monkeypatch: pytest.MonkeyPatch):
    async def sessao() -> AsyncIterator[SessaoFalsa]:
        yield SessaoFalsa()

    main.app.dependency_overrides[main.get_session] = sessao
    try:
        async with cliente() as http:
            monkeypatch.setattr(main.settings, "ollama_host", "http://127.0.0.1:1")  # porta fechada
            monkeypatch.setattr(main.settings, "searxng_url", "http://127.0.0.1:1")
            fora = await http.get("/health")

            async def ok(nome: str, coro: object) -> tuple[str, str]:
                coro.close()  # type: ignore[attr-defined]
                return nome, "ok"

            monkeypatch.setattr(main, "_checar", ok)
            tudo_ok = await http.get("/health")
    finally:
        main.app.dependency_overrides.clear()

    assert fora.status_code == 503
    assert fora.json()["postgres"] == "ok" and fora.json()["ollama"].startswith("fora") and fora.json()["searxng"].startswith("fora")
    assert (tudo_ok.status_code, tudo_ok.json()["status"]) == (200, "ok")
