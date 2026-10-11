"""Retry com backoff para falhas transitórias de Ollama e SearXNG.

Só repete o que tem chance de passar sozinho: conexão recusada (serviço subindo) e 5xx. Timeout de leitura
NÃO repete: o LLM que passou de `llm.timeout_s` gerando levaria o triplo do tempo para falhar de novo.
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from ollama import ResponseError

from src.config import config

ERROS_DE_REDE = (ResponseError, httpx.HTTPError, ConnectionError)


def transitorio(exc: Exception) -> bool:
    if isinstance(exc, ResponseError):
        return exc.status_code >= 500
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError))


async def com_retry[T](chamada: Callable[[], Awaitable[T]]) -> T:
    """Chama até `retry.tentativas` vezes, esperando `retry.espera_s` × 2^n entre elas. Outros erros sobem direto."""
    tentativas = config.retry.tentativas
    for n in range(1, tentativas + 1):
        try:
            return await chamada()
        except ERROS_DE_REDE as exc:
            if n == tentativas or not transitorio(exc):
                raise
            await asyncio.sleep(config.retry.espera_s * 2 ** (n - 1))
    raise AssertionError("retry.tentativas deve ser >= 1")
