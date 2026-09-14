"""Chamadas ao LLM (spec 2.5–2.6, 4): o modelo só julga, transcreve e redige, nunca calcula."""

import json
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


class Explicacao(BaseModel):
    explicacao: str = Field(min_length=1)


class Referencia(BaseModel):
    """Capítulo parecido de outro livro, com o julgamento que já recebeu (RAG)."""

    livro: str
    capitulo: str
    nivel: Nivel
    peso: float
    similaridade: float


class RespostaLLMInvalida(Exception):
    """Vira 502: o modelo respondeu, mas fora do contrato."""


class LLMIndisponivel(Exception):
    """Vira 503: modelo não configurado ou Ollama inacessível."""


PROMPT = """Você classifica livros técnicos. Dado o título e o sumário abaixo, identifique:
- tipo_livro: pratico = a maioria dos capítulos tem o leitor escrevendo ou executando código \
(tutoriais, projetos, exercícios de programação); teorico = conceitos, decisões e discussão, \
sem código para praticar; hibrido = as duas coisas em proporção parecida.
- densidade: leve = linguagem introdutória, poucos conceitos novos por capítulo; densa = \
matemática, formalismo ou muitos conceitos novos por capítulo; media = entre os dois.
- nivel_natural: para quem o livro foi escrito. iniciante = quem está começando no assunto; \
intermediario = quem já trabalha com o assunto e quer se aprofundar; avancado = especialistas. \
Julgue pelo público do livro inteiro, não pelo capítulo mais difícil.
- linguagens de programação ensinadas (nomes curtos, como "Python" ou "C"; lista vazia se o \
livro não ensina nenhuma linguagem).
Para cada capítulo, \
julgue o nível do conteúdo (iniciante = fundamentos que um profissional pleno já domina; \
intermediario = exige base prévia; avancado = aproveitado mesmo por quem já é sênior) e um peso \
relativo de esforço (1.0 = médio, entre 0.5 e 2.0). Não estime tempo."""

NOTA_REFERENCIAS = """Linhas com ≈ mostram como capítulos parecidos de outros livros já foram \
julgados. Use como referência de calibração, não como resposta: o capítulo deste livro pode ser \
diferente."""

PROMPT_CAPA = """Você lê capas de livros técnicos. Transcreva exatamente o que aparece na imagem: \
titulo = o nome do livro em destaque, sem o subtítulo; subtitulo = a linha que completa o nome \
(ex.: "Uma abordagem moderna"); autor; edicao. Slogan ou chamada de marketing não é título nem \
subtítulo. Campo que não aparece na capa = null. Não invente."""

PROMPT_EXPLICAR = """Você explica cronogramas de estudo. Usando só os dados fornecidos, diga ao \
leitor, em 2 a 4 frases em português, por que este capítulo tem essas horas ou por que ficou fora \
do cronograma. Significado dos fatores: senioridade = ajuste pela experiência do leitor; gap = livro \
acima do nível do leitor; baixo_nivel = linguagem de baixo nível (C, Rust...); peso = esforço \
relativo do capítulo, julgado pelos subtópicos. Fator 1.0 = sem efeito. Não recalcule nem invente \
números."""


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


async def classificar(
    titulo: str, capitulos: list[Capitulo], referencias: dict[int, list[Referencia]] | None = None
) -> Classificacao:
    refs = referencias or {}
    # Só títulos e subtópicos: sem páginas no prompt, para não convidar o modelo a fazer conta.
    sumario = "\n".join(
        f"{c.num}. {c.titulo}"
        + "".join(f"\n   - {s}" for s in c.subtopicos)
        + "".join(f'\n   ≈ parecido com "{r.capitulo}" ({r.livro}): {r.nivel}, peso {r.peso}' for r in refs.get(c.num, []))
        for c in capitulos
    )
    nota = f"{NOTA_REFERENCIAS}\n\n" if refs else ""
    messages: list[dict[str, object]] = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"{nota}Título: {titulo}\n\n{sumario}"},
    ]
    cls = await chat(config.llm.modelo_classificador, "do classificador", messages, Classificacao)
    if sorted(c.num for c in cls.capitulos) != sorted(c.num for c in capitulos):
        raise RespostaLLMInvalida("Classificador não devolveu exatamente um julgamento por capítulo.")
    return cls


async def ler_capa(imagem: bytes) -> Capa:
    messages: list[dict[str, object]] = [{"role": "user", "content": PROMPT_CAPA, "images": [imagem]}]
    return await chat(config.llm.modelo_visao, "de visão", messages, Capa)


async def explicar(capitulo: str, dados: dict[str, object]) -> Explicacao:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": PROMPT_EXPLICAR},
        {"role": "user", "content": f"Capítulo:\n{capitulo}\n\nDados:\n{json.dumps(dados, ensure_ascii=False, indent=2)}"},
    ]
    return await chat(config.llm.modelo_classificador, "de explicação", messages, Explicacao)
