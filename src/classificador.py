"""LLM Classificador (spec 2.6): único ponto onde o modelo é chamado. Só julga, nunca calcula."""

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


class ClassificacaoInvalida(Exception):
    """Vira 502: o modelo respondeu, mas fora do contrato."""


class LLMIndisponivel(Exception):
    """Vira 503: modelo não configurado ou Ollama inacessível."""


PROMPT = """Você classifica livros técnicos. Dado o título e o sumário abaixo, identifique tipo, \
densidade, nível natural do conteúdo e linguagens de programação abordadas. Para cada capítulo, \
julgue o nível do conteúdo (iniciante = fundamentos que um profissional pleno já domina; \
intermediario = exige base prévia; avancado = aproveitado mesmo por quem já é sênior) e um peso \
relativo de esforço (1.0 = médio, entre 0.5 e 2.0). Não estime tempo."""


async def classificar(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
    if not config.llm.modelo_classificador:
        raise LLMIndisponivel("Modelo do classificador não configurado (config.yaml: llm.modelo_classificador).")

    # Só títulos e subtópicos: sem páginas no prompt, para não convidar o modelo a fazer conta.
    sumario = "\n".join(f"{c.num}. {c.titulo}" + "".join(f"\n   - {s}" for s in c.subtopicos) for c in capitulos)
    cliente = AsyncClient(host=settings.ollama_host, timeout=config.llm.timeout_s)
    try:
        resposta = await cliente.chat(
            model=config.llm.modelo_classificador,
            messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": f"Título: {titulo}\n\n{sumario}"}],
            format=Classificacao.model_json_schema(),
            think=False,
            options={
                "temperature": config.llm.temperature,
                "num_ctx": config.llm.num_ctx,
                "num_predict": config.llm.num_predict,
            },
        )
    except (ResponseError, httpx.HTTPError, ConnectionError) as exc:
        raise LLMIndisponivel(f"Falha ao chamar o Ollama: {exc}") from exc

    try:
        cls = Classificacao.model_validate_json(resposta.message.content or "")
    except ValidationError as exc:
        raise ClassificacaoInvalida(f"Resposta do classificador fora do schema ({exc.error_count()} erro(s)).") from exc
    if sorted(c.num for c in cls.capitulos) != sorted(c.num for c in capitulos):
        raise ClassificacaoInvalida("Classificador não devolveu exatamente um julgamento por capítulo.")
    return cls
