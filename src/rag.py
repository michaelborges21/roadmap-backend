"""RAG (spec 4): 1 chunk por capítulo (título + subtópicos), embedding local via Ollama.

Uso: referências de calibração para o classificador — capítulos parecidos de OUTROS livros
já classificados, com o nível e o peso que receberam.
"""

import httpx
from ollama import AsyncClient, ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classificador import LLMIndisponivel, Referencia, RespostaLLMInvalida
from src.config import config, settings
from src.extracao import Capitulo
from src.models import Book, Chunk

DIMENSOES = 768  # Chunk.embedding = Vector(768)


def texto_chunk(cap: Capitulo) -> str:
    return "\n".join([cap.titulo, *cap.subtopicos])


async def embeddar(textos: list[str]) -> list[list[float]]:
    if not config.llm.modelo_embedding:
        raise LLMIndisponivel("Modelo de embedding não configurado (config.yaml: llm.modelo_embedding).")
    cliente = AsyncClient(host=settings.ollama_host, timeout=config.llm.timeout_s)
    try:
        resposta = await cliente.embed(model=config.llm.modelo_embedding, input=[config.rag.prefixo + t for t in textos])
    except (ResponseError, httpx.HTTPError, ConnectionError) as exc:
        raise LLMIndisponivel(f"Falha ao gerar embeddings no Ollama: {exc}") from exc
    vetores = [list(v) for v in resposta.embeddings]
    if any(len(v) != DIMENSOES for v in vetores):
        raise RespostaLLMInvalida(f"Embedding com {len(vetores[0])} dimensões; o schema espera {DIMENSOES}.")
    return vetores


async def indexar(session: AsyncSession, book: Book, capitulos: list[Capitulo]) -> None:
    """Idempotente: cria os chunks do livro se ainda não existem. Não faz commit."""
    if await session.scalar(select(Chunk.id).where(Chunk.book_id == book.id).limit(1)):
        return
    textos = [texto_chunk(c) for c in capitulos]
    for texto, vetor in zip(textos, await embeddar(textos)):
        session.add(Chunk(book_id=book.id, texto=texto, embedding=vetor))


async def referencias(session: AsyncSession, book_id: int, capitulos: list[Capitulo]) -> dict[int, list[Referencia]]:
    """Para cada capítulo: vizinhos mais próximos em outros livros já classificados."""
    meus = {c.texto: c.embedding for c in await session.scalars(select(Chunk).where(Chunk.book_id == book_id))}
    resultado: dict[int, list[Referencia]] = {}
    for cap in capitulos:
        vetor = meus.get(texto_chunk(cap))
        if vetor is None:
            continue
        distancia = Chunk.embedding.cosine_distance(vetor)
        linhas = await session.execute(
            select(Chunk.texto, Book.titulo, Book.capitulos, Book.classificacao, distancia)
            .join(Book, Book.id == Chunk.book_id)
            .where(Chunk.book_id != book_id, Book.classificacao.is_not(None), distancia <= 1 - config.rag.similaridade_min)
            .order_by(distancia)
            .limit(config.rag.referencias_por_capitulo)
        )
        refs = []
        for texto, livro, caps_ref, cls_ref, dist in linhas:
            # Chunk não guarda o número do capítulo: o título (1ª linha) liga ao JSONB do livro.
            titulo = texto.split("\n", 1)[0]
            num = next((c["num"] for c in caps_ref if c["titulo"] == titulo), None)
            julgado = next((j for j in cls_ref["capitulos"] if j["num"] == num), None)
            if julgado:
                refs.append(
                    Referencia(livro=livro, capitulo=titulo, nivel=julgado["nivel"], peso=julgado["peso"], similaridade=1 - dist)
                )
        if refs:
            resultado[cap.num] = refs
    return resultado
