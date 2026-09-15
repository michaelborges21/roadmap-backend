"""Chamadas ao LLM com Ollama mockado: contrato de entrada/saída, sem modelo real."""

import json
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from src import classificador
from src.classificador import (
    Capa,
    Classificacao,
    Explicacao,
    LLMIndisponivel,
    RespostaLLMInvalida,
    classificar,
    explicar,
    ler_capa,
)
from src.extracao import Capitulo

CAPS = [
    Capitulo(num=1, titulo="Introdução", pag_inicio=1, paginas=10, subtopicos=["O que é memória"]),
    Capitulo(num=2, titulo="Ponteiros", pag_inicio=11, paginas=30),
]
VALIDA = {
    "tipo_livro": "pratico",
    "densidade": "media",
    "nivel_natural": "intermediario",
    "linguagens": ["C"],
    "capitulos": [{"num": 1, "nivel": "iniciante", "peso": 0.8}, {"num": 2, "nivel": "avancado", "peso": 1.5}],
}


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict], list[dict]]:
    def configurar(conteudo: dict) -> list[dict]:
        chamadas: list[dict] = []

        async def chat(self: object, **kwargs: object) -> SimpleNamespace:
            chamadas.append(kwargs)
            return SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(conteudo)), done_reason="stop", prompt_eval_count=100
            )

        monkeypatch.setattr(classificador.AsyncClient, "chat", chat)
        monkeypatch.setattr(classificador.config.llm, "modelo_classificador", "modelo-fake")
        monkeypatch.setattr(classificador.config.llm, "modelo_visao", "modelo-fake")
        return chamadas

    return configurar


@pytest.mark.asyncio
async def test_resposta_valida_e_contrato_da_chamada(llm: Callable[[dict], list[dict]]):
    chamadas = llm(VALIDA)
    cls = await classificar("Programando em C", CAPS)
    assert cls.capitulos[1].peso == 1.5

    kwargs = chamadas[0]
    assert kwargs["format"] == Classificacao.model_json_schema()  # structured output, não parse de string
    assert kwargs["think"] is False
    assert {"num_ctx", "num_predict"} <= kwargs["options"].keys()  # type: ignore[union-attr]
    usuario = kwargs["messages"][1]["content"]  # type: ignore[index]
    assert "O que é memória" in usuario  # subtópicos vão no prompt
    assert "30" not in usuario  # páginas não vão: o LLM não faz conta


@pytest.mark.asyncio
async def test_capitulo_faltando_e_invalido(llm: Callable[[dict], list[dict]]):
    llm({**VALIDA, "capitulos": VALIDA["capitulos"][:1]})
    with pytest.raises(RespostaLLMInvalida):
        await classificar("Programando em C", CAPS)


@pytest.mark.asyncio
async def test_peso_fora_da_faixa_e_invalido(llm: Callable[[dict], list[dict]]):
    llm({**VALIDA, "capitulos": [{"num": 1, "nivel": "iniciante", "peso": 3.0}, VALIDA["capitulos"][1]]})
    with pytest.raises(RespostaLLMInvalida):
        await classificar("Programando em C", CAPS)


@pytest.mark.asyncio
async def test_sem_modelo_configurado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(classificador.config.llm, "modelo_classificador", None)
    with pytest.raises(LLMIndisponivel, match="não configurado"):
        await classificar("Programando em C", CAPS)


@pytest.mark.asyncio
async def test_explicar_recebe_dados_calculados(llm: Callable[[dict], list[dict]]):
    chamadas = llm({"explicacao": "O capítulo é avançado e pesa 1.5."})
    resposta = await explicar("Ponteiros\nAritmética", {"fatores": {"peso": 1.5}})
    assert resposta.explicacao
    assert chamadas[0]["format"] == Explicacao.model_json_schema()
    assert '"peso": 1.5' in chamadas[0]["messages"][1]["content"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_capa_em_imagem(llm: Callable[[dict], list[dict]]):
    chamadas = llm({"titulo": "JavaScript", "subtitulo": "O guia definitivo", "autor": "David Flanagan", "edicao": "7ª"})
    capa = await ler_capa(b"\xff\xd8bytes-jpeg")
    assert capa.titulo == "JavaScript"
    assert chamadas[0]["format"] == Capa.model_json_schema()
    assert chamadas[0]["messages"][0]["images"] == [b"\xff\xd8bytes-jpeg"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_capa_sem_titulo_e_invalida(llm: Callable[[dict], list[dict]]):
    llm({"titulo": "", "subtitulo": None, "autor": None, "edicao": None})
    with pytest.raises(RespostaLLMInvalida):
        await ler_capa(b"img")
