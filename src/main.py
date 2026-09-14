import hashlib
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.calculo import Plano, Senioridade, planejar
from src.classificador import Classificacao, ClassificacaoInvalida, LLMIndisponivel, classificar
from src.db import get_session
from src.extracao import Capitulo, Extraido, ExtracaoAmbigua, extrair
from src.models import Book, Roadmap

app = FastAPI(title="roadmapAPI")
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class BookCriado(Extraido):
    id: int
    falta_sumario: bool


class PedidoRoadmap(BaseModel):
    senioridade: Senioridade
    disponibilidade_horas: float = Field(gt=0, le=168)


class RoadmapCriado(Plano):
    id: int
    book_id: int


@app.exception_handler(ExtracaoAmbigua)
async def extracao_ambigua(request: Request, exc: ExtracaoAmbigua) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ClassificacaoInvalida)
async def classificacao_invalida(request: Request, exc: ClassificacaoInvalida) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(LLMIndisponivel)
async def llm_indisponivel(request: Request, exc: LLMIndisponivel) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.post("/books", status_code=201)
async def criar_book(arquivo: UploadFile, response: Response, session: SessionDep) -> BookCriado:
    if arquivo.content_type in {"image/jpeg", "image/png"}:
        raise HTTPException(415, "Capa em imagem ainda não suportada: modelo de visão local pendente de escolha.")
    if arquivo.content_type != "application/pdf":
        raise HTTPException(415, "Envie um PDF com o sumário ou a capa do livro.")

    dados = await arquivo.read()
    hash_fonte = hashlib.sha256(dados).hexdigest()
    book = await session.scalar(select(Book).where(Book.hash_fonte == hash_fonte))
    if book:
        response.status_code = 200
    else:
        # pdfplumber é CPU-bound e síncrono; fora do event loop.
        ext = await run_in_threadpool(extrair, dados)
        book = Book(hash_fonte=hash_fonte, **ext.model_dump(mode="json"))
        session.add(book)
        await session.commit()

    return BookCriado.model_validate(
        {**Extraido.model_validate(book, from_attributes=True).model_dump(), "id": book.id, "falta_sumario": not book.capitulos}
    )


@app.post("/books/{book_id}/roadmaps", status_code=201)
async def criar_roadmap(book_id: int, pedido: PedidoRoadmap, session: SessionDep) -> RoadmapCriado:
    book = await session.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Livro não encontrado.")
    if not book.capitulos:
        raise HTTPException(422, "Livro sem sumário: envie o PDF do sumário para gerar o cronograma.")

    capitulos = [Capitulo.model_validate(c) for c in book.capitulos]
    if book.classificacao is None:  # cache entre usuários: classifica uma vez por livro
        book.classificacao = (await classificar(book.titulo, capitulos)).model_dump()
    plano = planejar(capitulos, Classificacao.model_validate(book.classificacao), pedido.senioridade, pedido.disponibilidade_horas)

    roadmap = Roadmap(
        book_id=book.id,
        senioridade=pedido.senioridade,
        disponibilidade_horas=pedido.disponibilidade_horas,
        plano=plano.model_dump(mode="json"),
    )
    session.add(roadmap)
    await session.commit()
    return RoadmapCriado(id=roadmap.id, book_id=book.id, **plano.model_dump())
