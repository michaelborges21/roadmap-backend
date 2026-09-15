import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.calculo import (
    REGRA_DISTRIBUICAO,
    OrigemPaginas,
    Plano,
    Senioridade,
    distribuir_paginas,
    paginas_de_conteudo,
    planejar,
)
from src.classificador import (
    Classificacao,
    Explicacao,
    LLMIndisponivel,
    RespostaLLMInvalida,
    classificar,
    explicar,
    ler_capa,
)
from src.db import get_session
from src.extracao import Capitulo, Extraido, ExtracaoAmbigua, extrair, extrair_texto
from src.models import Book, Roadmap
from src.pesquisa import Pesquisa, PesquisaIndisponivel, pesquisar

app = FastAPI(title="roadmapAPI")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
IMAGENS = {"image/jpeg", "image/png"}
# .md muitas vezes chega como text/plain ou application/octet-stream: navegador não conhece o tipo.
# A extensão do arquivo desempata; sem extensão reconhecida, cai no content_type mesmo.
TEXTOS = {"text/plain", "text/markdown", "text/x-markdown"}
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


@app.exception_handler(PesquisaIndisponivel)
async def pesquisa_indisponivel(request: Request, exc: PesquisaIndisponivel) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.post("/books", status_code=201)
async def criar_book(arquivo: UploadFile, response: Response, session: SessionDep) -> BookCriado:
    nome = (arquivo.filename or "").lower()
    markdown = arquivo.content_type in {"text/markdown", "text/x-markdown"} or nome.endswith((".md", ".markdown"))
    e_texto = markdown or arquivo.content_type in TEXTOS or nome.endswith(".txt")
    e_pdf = arquivo.content_type == "application/pdf" or nome.endswith(".pdf")
    e_imagem = arquivo.content_type in IMAGENS or nome.endswith((".jpg", ".jpeg", ".png"))
    if not (e_texto or e_pdf or e_imagem):
        raise HTTPException(415, "Envie um PDF, uma imagem de capa (JPEG/PNG) ou um arquivo de texto (.txt/.md).")

    dados = await arquivo.read()
    hash_fonte = hashlib.sha256(dados).hexdigest()
    book = await session.scalar(select(Book).where(Book.hash_fonte == hash_fonte))
    if book:
        response.status_code = 200
    else:
        if e_imagem:
            capa = await ler_capa(dados)
            titulo = f"{capa.titulo}: {capa.subtitulo}" if capa.subtitulo else capa.titulo
            ext = Extraido(titulo=titulo, origem="capa", paginas_conteudo=None, paginas_fisicas=None, capitulos=[])
        elif e_texto:
            ext = extrair_texto(dados, markdown)
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
    sem_paginas = any(c.paginas is None for c in capitulos)
    avisos: list[str] = []

    if book.pesquisa is None:  # uma vez por livro, como a classificação
        try:
            book.pesquisa = (await pesquisar(session, book, capitulos)).model_dump(mode="json")
        except PesquisaIndisponivel as exc:
            if sem_paginas:
                raise  # sem página no sumário e sem pesquisa, não há número honesto para calcular
            avisos.append(f"Pesquisa na web indisponível; páginas do sumário usadas sem conferência ({exc}).")
    pesquisa = Pesquisa.model_validate(book.pesquisa) if book.pesquisa else None

    if sem_paginas:
        if pesquisa is None or pesquisa.paginas_totais is None:
            await session.commit()  # guarda a pesquisa e a base RAG: a próxima tentativa não refaz a busca à toa
            motivo = pesquisa.justificativa if pesquisa else ""
            raise HTTPException(
                422, f"Sumário sem números de página e nenhuma fonte confiável com o total de páginas. {motivo} Envie um sumário paginado."
            )
        conteudo = paginas_de_conteudo(pesquisa.paginas_totais)
        capitulos = distribuir_paginas(capitulos, conteudo)
        origem = OrigemPaginas(
            origem="pesquisa",
            paginas_totais=pesquisa.paginas_totais,
            paginas_conteudo=conteudo,
            url_fonte=pesquisa.url_fonte,
            regra=REGRA_DISTRIBUICAO,
        )
    else:
        origem = OrigemPaginas(origem="sumario", paginas_totais=book.paginas_conteudo)
        if pesquisa and pesquisa.paginas_totais and book.paginas_conteudo and book.paginas_conteudo > pesquisa.paginas_totais:
            avisos.append(
                f"O sumário soma {book.paginas_conteudo} páginas de conteúdo, mas a fonte pesquisada diz "
                f"{pesquisa.paginas_totais} ({pesquisa.url_fonte}): edição diferente?"
            )
    if pesquisa and pesquisa.outras_contagens:
        avisos.append(
            "Outras contagens de páginas vistas na web (podem ser de outro livro ou edição): " + ", ".join(pesquisa.outras_contagens)
        )

    if book.classificacao is None:  # cache entre usuários: classifica uma vez por livro
        book.classificacao = (await classificar(book.titulo, capitulos)).model_dump()
    plano = planejar(capitulos, Classificacao.model_validate(book.classificacao), pedido.senioridade, pedido.disponibilidade_horas)
    plano = plano.model_copy(update={"paginas": origem, "avisos": avisos})

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
            "paginas": horas.paginas,
            "origem_das_paginas": plano.paginas.origem if plano.paginas else "sumario",
            "horas": {k: round(getattr(horas, k), 1) for k in ("leitura", "codigo", "escrita", "total")},
            "fatores": horas.fatores.model_dump(),
        }
    else:
        dados |= {"nivel_do_capitulo": excluido.nivel, "fora_do_cronograma": "nível do capítulo abaixo da senioridade do leitor"}  # type: ignore[union-attr]
    return await explicar("\n".join([cap.titulo, *cap.subtopicos]), dados)
