"""Chamadas ao LLM (spec 2.5–2.6): o modelo só julga e transcreve, nunca calcula."""

from typing import Literal

import httpx
from ollama import AsyncClient, ResponseError
from pydantic import BaseModel, Field, ValidationError

from src.config import config, settings
from src.extracao import Capitulo

Nivel = Literal["iniciante", "intermediario", "avancado"]


class CapituloClassificado(BaseModel):
    num: int
    nivel: Nivel
    peso: float = Field(ge=0.5, le=2.0)


class Classificacao(BaseModel):
    tipo_livro: Literal["teorico", "pratico", "hibrido"]
    densidade: Literal["leve", "media", "densa"]
    nivel_natural: Nivel
    linguagens: list[str]
    capitulos: list[CapituloClassificado]


class Capa(BaseModel):
    titulo: str = Field(min_length=1)
    subtitulo: str | None
    autor: str | None
    edicao: str | None


class RespostaLLMInvalida(Exception):
    """Vira 502: o modelo respondeu, mas fora do contrato."""


class LLMIndisponivel(Exception):
    """Vira 503: modelo não configurado ou Ollama inacessível."""


PROMPT = """Você classifica livros técnicos. Dado o título e o sumário abaixo, identifique tipo, \
densidade, nível natural do conteúdo e linguagens de programação ensinadas (nomes curtos, como \
"Python" ou "C"; lista vazia se o livro não ensina nenhuma linguagem). Para cada capítulo, \
julgue o nível do conteúdo (iniciante = fundamentos que um profissional pleno já domina; \
intermediario = exige base prévia; avancado = aproveitado mesmo por quem já é sênior) e um peso \
relativo de esforço (1.0 = médio, entre 0.5 e 2.0). Não estime tempo."""

PROMPT_CAPA = """Você lê capas de livros técnicos. Transcreva exatamente o que aparece na imagem: \
titulo = o nome do livro em destaque, sem o subtítulo; subtitulo = a linha que completa o nome \
(ex.: "Uma abordagem moderna"); autor; edicao. Slogan ou chamada de marketing não é título nem \
subtítulo. Campo que não aparece na capa = null. Não invente."""


async def chat[T: BaseModel](modelo: str | None, papel: str, messages: list[dict[str, object]], schema: type[T]) -> T:
    if not modelo:
        raise LLMIndisponivel(f"Modelo {papel} não configurado (config.yaml: llm).")
    cliente = AsyncClient(host=settings.ollama_host, timeout=config.llm.timeout_s)
    try:
        resposta = await cliente.chat(
            model=modelo,
            messages=messages,
            format=schema.model_json_schema(),
            think=False,
            options={
                "temperature": config.llm.temperature,
                "num_ctx": config.llm.num_ctx,
                "num_predict": config.llm.num_predict,
            },
        )
    except (ResponseError, httpx.HTTPError, ConnectionError) as exc:
        raise LLMIndisponivel(f"Falha ao chamar o Ollama: {exc}") from exc
    if resposta.done_reason == "length":
        raise RespostaLLMInvalida(
            f"Resposta do modelo {papel} cortada em num_predict={config.llm.num_predict} tokens "
            f"(prompt: {resposta.prompt_eval_count} tokens)."
        )
    try:
        return schema.model_validate_json(resposta.message.content or "")
    except ValidationError as exc:
        erro = exc.errors()[0]
        raise RespostaLLMInvalida(
            f"Resposta do modelo {papel} fora do schema: {exc.error_count()} erro(s), "
            f"o primeiro em {'.'.join(map(str, erro['loc'])) or '<raiz>'}: {erro['msg']}."
        ) from exc


async def classificar(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
    # Só títulos e subtópicos: sem páginas no prompt, para não convidar o modelo a fazer conta.
    sumario = "\n".join(f"{c.num}. {c.titulo}" + "".join(f"\n   - {s}" for s in c.subtopicos) for c in capitulos)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"Título: {titulo}\n\n{sumario}"},
    ]
    cls = await chat(config.llm.modelo_classificador, "do classificador", messages, Classificacao)
    if sorted(c.num for c in cls.capitulos) != sorted(c.num for c in capitulos):
        raise RespostaLLMInvalida("Classificador não devolveu exatamente um julgamento por capítulo.")
    return cls


async def ler_capa(imagem: bytes) -> Capa:
    messages: list[dict[str, object]] = [{"role": "user", "content": PROMPT_CAPA, "images": [imagem]}]
    return await chat(config.llm.modelo_visao, "de visão", messages, Capa)
