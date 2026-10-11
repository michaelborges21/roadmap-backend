"""Retry com backoff: repete só falha transitória, e só até `retry.tentativas`."""

import httpx
import pytest
from ollama import ResponseError

from src import retry
from src.config import config


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    esperas: list[float] = []

    async def dorme(s: float) -> None:
        esperas.append(s)

    monkeypatch.setattr(retry.asyncio, "sleep", dorme)
    return esperas


def chamada_que_falha(erros: list[Exception]):
    chamadas = 0

    async def chamada() -> str:
        nonlocal chamadas
        chamadas += 1
        if erros:
            raise erros.pop(0)
        return "ok"

    return chamada, lambda: chamadas


@pytest.mark.asyncio
async def test_repete_conexao_recusada_com_backoff_e_acaba_passando(sem_espera: list[float]):
    chamada, chamadas = chamada_que_falha([httpx.ConnectError("recusada"), ConnectionError("recusada")])
    assert await retry.com_retry(chamada) == "ok"
    assert chamadas() == 3
    assert sem_espera == [config.retry.espera_s, config.retry.espera_s * 2]


@pytest.mark.asyncio
async def test_repete_5xx_do_ollama():
    chamada, chamadas = chamada_que_falha([ResponseError("carregando modelo", 503)])
    assert await retry.com_retry(chamada) == "ok" and chamadas() == 2


@pytest.mark.asyncio
async def test_desiste_apos_as_tentativas_configuradas_e_relanca_o_erro():
    chamada, chamadas = chamada_que_falha([httpx.ConnectError("x")] * 10)
    with pytest.raises(httpx.ConnectError):
        await retry.com_retry(chamada)
    assert chamadas() == config.retry.tentativas


@pytest.mark.asyncio
@pytest.mark.parametrize("erro", [httpx.ReadTimeout("lento"), ResponseError("modelo não existe", 404)])
async def test_nao_repete_timeout_de_leitura_nem_erro_do_cliente(erro: Exception):
    chamada, chamadas = chamada_que_falha([erro])
    with pytest.raises(type(erro)):
        await retry.com_retry(chamada)
    assert chamadas() == 1


@pytest.fixture
def servidor_searxng(monkeypatch: pytest.MonkeyPatch):
    """SearXNG falso em HTTP real: responde 503 nas primeiras `falhas` requisições e depois 200 com um resultado."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    estado = {"falhas": 0, "requisicoes": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            estado["requisicoes"] += 1
            if estado["requisicoes"] <= estado["falhas"]:
                self.send_response(503)
                self.end_headers()
                return
            corpo = json.dumps({"results": [{"url": "https://editora.exemplo/livro", "content": "344 páginas"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *_: object) -> None:
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    from src import pesquisa

    monkeypatch.setattr(pesquisa.settings, "searxng_url", f"http://127.0.0.1:{servidor.server_port}")
    yield estado
    servidor.shutdown()


@pytest.mark.asyncio
async def test_buscar_web_real_se_recupera_de_503_transitorio(servidor_searxng: dict[str, int]):
    from src.pesquisa import buscar_web

    servidor_searxng["falhas"] = 2
    resultados = await buscar_web("Livro")
    assert resultados == [{"url": "https://editora.exemplo/livro", "content": "344 páginas"}]
    assert servidor_searxng["requisicoes"] == 3


@pytest.mark.asyncio
async def test_buscar_web_real_vira_pesquisa_indisponivel_se_o_503_persiste(servidor_searxng: dict[str, int]):
    from src.pesquisa import PesquisaIndisponivel, buscar_web

    servidor_searxng["falhas"] = 99
    with pytest.raises(PesquisaIndisponivel):
        await buscar_web("Livro")
    assert servidor_searxng["requisicoes"] == config.retry.tentativas


@pytest.mark.asyncio
async def test_buscar_web_real_porta_fechada_tenta_todas_as_vezes_e_vira_indisponivel(monkeypatch: pytest.MonkeyPatch):
    from src import pesquisa

    chamadas = 0
    original = pesquisa.com_retry

    async def contando(chamada):  # type: ignore[no-untyped-def]
        async def c():  # type: ignore[no-untyped-def]
            nonlocal chamadas
            chamadas += 1
            return await chamada()

        return await original(c)

    monkeypatch.setattr(pesquisa.settings, "searxng_url", "http://127.0.0.1:1")
    monkeypatch.setattr(pesquisa, "com_retry", contando)
    with pytest.raises(pesquisa.PesquisaIndisponivel):
        await pesquisa.buscar_web("Livro")
    assert chamadas == config.retry.tentativas
