import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.calculo import Plano, Senioridade, planejar
from src.classificador import (
    Classificacao,
    Explicacao,
    LLMIndisponivel,
    RespostaLLMInvalida,
    classificar,
    explicar,
    ler_capa,
)
from src.config import config
from src.db import get_session
from src.extracao import Capitulo, Extraido, ExtracaoAmbigua, extrair
from src.models import Book, Roadmap
from src.rag import indexar, referencias, texto_chunk

app = FastAPI(title="roadmapAPI")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
IMAGENS = {"image/jpeg", "image/png"}
STATIC_DIR = Path(__file__).parent.parent / "static"


@app.get("/", include_in_schema=False)
async def interface_de_teste() -> FileResponse:
    """Página auxiliar sem estilo para exercitar a API sem o Swagger. Não é parte do contrato."""
    return FileResponse(STATIC_DIR / "index.html")


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


@app.exception_handler(RespostaLLMInvalida)
async def resposta_llm_invalida(request: Request, exc: RespostaLLMInvalida) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(LLMIndisponivel)
async def llm_indisponivel(request: Request, exc: LLMIndisponivel) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.post("/books", status_code=201)
async def criar_book(arquivo: UploadFile, response: Response, session: SessionDep) -> BookCriado:
    if arquivo.content_type not in IMAGENS | {"application/pdf"}:
        raise HTTPException(415, "Envie um PDF (sumário ou capa) ou a imagem da capa (JPEG/PNG).")

    dados = await arquivo.read()
    hash_fonte = hashlib.sha256(dados).hexdigest()
    book = await session.scalar(select(Book).where(Book.hash_fonte == hash_fonte))
    if book:
        response.status_code = 200
    else:
        if arquivo.content_type in IMAGENS:
            capa = await ler_capa(dados)
            titulo = f"{capa.titulo}: {capa.subtitulo}" if capa.subtitulo else capa.titulo
            ext = Extraido(titulo=titulo, origem="capa", paginas_conteudo=None, paginas_fisicas=None, capitulos=[])
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
        await indexar(session, book, capitulos)
        refs = await referencias(session, book.id, capitulos) if config.rag.ativo else None
        book.classificacao = (await classificar(book.titulo, capitulos, refs)).model_dump()
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


@app.get("/roadmaps/{roadmap_id}/explicar")
async def explicar_capitulo(roadmap_id: int, capitulo: int, session: SessionDep) -> Explicacao:
    """Responde "por que esse capítulo deu 6h?" com os fatores persistidos; o LLM só redige."""
    roadmap = await session.get(Roadmap, roadmap_id)
    if not roadmap:
        raise HTTPException(404, "Roadmap não encontrado.")
    plano = Plano.model_validate(roadmap.plano)
    book = await session.get(Book, roadmap.book_id)
    cap = next((Capitulo.model_validate(c) for c in book.capitulos if c["num"] == capitulo), None) if book else None
    horas = next((h for h in plano.capitulos if h.num == capitulo), None)
    excluido = next((e for e in plano.excluidos if e.num == capitulo), None)
    if cap is None or (horas is None and excluido is None):
        raise HTTPException(404, f"Capítulo {capitulo} não existe neste roadmap.")

    dados: dict[str, object] = {"senioridade_do_leitor": plano.senioridade}
    if horas:
        dados |= {
            "nivel_do_capitulo": horas.nivel,
            "horas": {k: round(getattr(horas, k), 1) for k in ("leitura", "codigo", "escrita", "total")},
            "fatores": horas.fatores.model_dump(),
        }
    else:
        dados |= {"nivel_do_capitulo": excluido.nivel, "fora_do_cronograma": "nível do capítulo abaixo da senioridade do leitor"}  # type: ignore[union-attr]
    return await explicar(texto_chunk(cap), dados)
