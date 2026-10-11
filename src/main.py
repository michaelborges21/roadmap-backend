import asyncio
import hashlib
from collections.abc import Awaitable
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.calculo import (
    REGRA_DISTRIBUICAO,
    Meta,
    OrigemPaginas,
    Plano,
    Senioridade,
    disponibilidade_para_prazo,
    distribuir_paginas,
    paginas_de_conteudo,
    planejar,
    semanas_ate,
)
from src.classificador import (
    Classificacao,
    Explicacao,
    LLMIndisponivel,
    RespostaLLMInvalida,
    classificar,
    explicar,
    ler_capa,
    versao_classificacao,
)
from src.config import config, settings
from src.db import get_session
from src.extracao import Capitulo, ExtracaoAmbigua, Extraido, extrair, extrair_texto
from src.models import Book, Roadmap
from src.pesquisa import Pesquisa, PesquisaIndisponivel, pesquisar, versao_pesquisa

app = FastAPI(title="RoadmapAPI")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
IMAGENS = {"image/jpeg", "image/png"}
# .md muitas vezes chega como text/plain ou application/octet-stream: navegador não conhece o tipo.
# A extensão do arquivo desempata; sem extensão reconhecida, cai no content_type mesmo.
MARKDOWN = {"text/markdown", "text/x-markdown"}
TEXTOS = {"text/plain", *MARKDOWN}
STATIC_DIR = Path(__file__).parent.parent / "static"
LOCK_LIVRO = 1  # espaço de nomes do pg_advisory_xact_lock(ns, book_id): outros locks futuros não colidem
STATUS_DE_ERRO: dict[type[Exception], int] = {
    ExtracaoAmbigua: 422,
    RespostaLLMInvalida: 502,
    LLMIndisponivel: 503,
    PesquisaIndisponivel: 503,
}


async def erro_de_dominio(request: Request, exc: Exception) -> JSONResponse:
    # isinstance (e não type(exc)): subclasse de um erro de domínio herda o status do pai.
    status = next(s for tipo, s in STATUS_DE_ERRO.items() if isinstance(exc, tipo))
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for erro in STATUS_DE_ERRO:
    app.add_exception_handler(erro, erro_de_dominio)


@app.get("/", include_in_schema=False)
async def interface_de_teste() -> FileResponse:
    """Página auxiliar para exercitar a API sem o Swagger. Não é parte do contrato."""
    return FileResponse(STATIC_DIR / "index.html")


async def _checar(nome: str, coro: Awaitable[object]) -> tuple[str, str]:
    try:
        await asyncio.wait_for(coro, config.api.health_timeout_s)
    except Exception as exc:  # health check: qualquer falha da dependência vira "fora", nunca 500
        return nome, f"fora: {type(exc).__name__}"
    return nome, "ok"


@app.get("/health")
async def health(session: SessionDep) -> JSONResponse:
    """200 se Postgres, Ollama e SearXNG respondem; 503 com o estado de cada um caso contrário."""
    async with httpx.AsyncClient() as http:
        resultados = await asyncio.gather(
            _checar("postgres", session.execute(text("select 1"))),
            _checar("ollama", http.get(f"{settings.ollama_host}/api/tags")),
            _checar("searxng", http.get(settings.searxng_url)),
        )
    dependencias = dict(resultados)
    ok = all(v == "ok" for v in dependencias.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degradado", **dependencias})


class BookCriado(Extraido):
    id: int
    falta_sumario: bool


class PedidoRoadmap(BaseModel):
    senioridade: Senioridade
    disponibilidade_horas: float | None = Field(default=None, gt=0, le=168)
    data_limite: date | None = None

    @model_validator(mode="after")
    def um_dos_dois(self) -> "PedidoRoadmap":
        if (self.disponibilidade_horas is None) == (self.data_limite is None):
            raise ValueError("Informe disponibilidade_horas ou data_limite, e só um dos dois.")
        return self


class RoadmapCriado(Plano):
    id: int
    book_id: int


@app.post("/books", status_code=201)
async def criar_book(arquivo: UploadFile, response: Response, session: SessionDep) -> BookCriado:
    nome = (arquivo.filename or "").lower()
    markdown = arquivo.content_type in MARKDOWN or nome.endswith((".md", ".markdown"))
    e_texto = markdown or arquivo.content_type in TEXTOS or nome.endswith(".txt")
    e_pdf = arquivo.content_type == "application/pdf" or nome.endswith(".pdf")
    e_imagem = arquivo.content_type in IMAGENS or nome.endswith((".jpg", ".jpeg", ".png"))
    if not (e_texto or e_pdf or e_imagem):
        raise HTTPException(415, "Envie um PDF, uma imagem de capa (JPEG/PNG) ou um arquivo de texto (.txt/.md).")

    limite = int(config.api.upload_max_mb * 1024 * 1024)
    if (arquivo.size or 0) > limite:
        raise HTTPException(413, f"Arquivo acima do limite de {config.api.upload_max_mb:g} MB.")
    dados = await arquivo.read(limite + 1)
    if len(dados) > limite:
        raise HTTPException(413, f"Arquivo acima do limite de {config.api.upload_max_mb:g} MB.")
    hash_fonte = hashlib.sha256(dados).hexdigest()
    book = await session.scalar(select(Book).where(Book.hash_fonte == hash_fonte))
    if book:
        response.status_code = 200
    else:
        if e_imagem:
            capa = await ler_capa(dados)
            titulo = f"{capa.titulo}: {capa.subtitulo}" if capa.subtitulo else capa.titulo
            ext = Extraido(titulo=titulo, origem="capa")
        elif e_texto:
            ext = extrair_texto(dados, markdown)
        else:
            # pdfplumber é CPU-bound e síncrono; fora do event loop.
            ext = await run_in_threadpool(extrair, dados)
        book = Book(hash_fonte=hash_fonte, **ext.model_dump(mode="json"))
        session.add(book)
        try:
            await session.commit()
        except IntegrityError:
            # Upload simultâneo do mesmo arquivo: o outro pedido gravou primeiro; reaproveita o livro dele.
            await session.rollback()
            book = await session.scalar(select(Book).where(Book.hash_fonte == hash_fonte))
            response.status_code = 200

    extraido = Extraido.model_validate(book, from_attributes=True).model_dump()
    return BookCriado(**extraido, id=book.id, falta_sumario=not book.capitulos)


def hoje() -> date:
    return date.today()


def resolver_paginas(
    capitulos: list[Capitulo], paginas_sumario: int | None, pesquisa: Pesquisa | None
) -> tuple[list[Capitulo], OrigemPaginas, list[str]]:
    """De onde vêm as páginas do cálculo, os capítulos com páginas preenchidas e os avisos que isso gera.

    Pré-condição: sumário sem páginas exige `pesquisa.paginas_totais` (o chamador responde 422 antes).
    """
    avisos: list[str] = []
    if any(c.paginas is None for c in capitulos):
        assert pesquisa and pesquisa.paginas_totais
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
        origem = OrigemPaginas(origem="sumario", paginas_totais=paginas_sumario)
        if pesquisa and pesquisa.paginas_totais and paginas_sumario and paginas_sumario > pesquisa.paginas_totais:
            avisos.append(
                f"O sumário soma {paginas_sumario} páginas de conteúdo, mas a fonte pesquisada diz "
                f"{pesquisa.paginas_totais} ({pesquisa.url_fonte}): edição diferente?"
            )
    if pesquisa and pesquisa.outras_contagens:
        avisos.append(
            "Outras contagens de páginas vistas na web (podem ser de outro livro ou edição): " + ", ".join(pesquisa.outras_contagens)
        )
    return capitulos, origem, avisos


@app.post("/books/{book_id}/roadmaps", status_code=201)
async def criar_roadmap(book_id: int, pedido: PedidoRoadmap, session: SessionDep) -> RoadmapCriado:
    semanas_prazo = semanas_ate(pedido.data_limite, hoje()) if pedido.data_limite else None
    if semanas_prazo == 0:  # antes de qualquer trabalho caro (pesquisa, LLM)
        raise HTTPException(422, "A data_limite precisa ser futura.")
    # Um roadmap por vez para o mesmo livro: o segundo pedido espera o primeiro gravar pesquisa e classificação
    # e as reaproveita, em vez de repetir a chamada ao LLM. Preso à transação: o commit (ou o rollback, ou a
    # queda da conexão) libera. Vem antes do get e com populate_existing, para ler o que o outro pedido gravou.
    await session.execute(text("select pg_advisory_xact_lock(:ns, :id)"), {"ns": LOCK_LIVRO, "id": book_id})
    book = await session.get(Book, book_id, populate_existing=True)
    if not book:
        raise HTTPException(404, "Livro não encontrado.")
    if not book.capitulos:
        raise HTTPException(422, "Livro sem sumário: envie o PDF do sumário para gerar o cronograma.")

    capitulos = [Capitulo.model_validate(c) for c in book.capitulos]
    sem_paginas = any(c.paginas is None for c in capitulos)
    avisos: list[str] = []

    # Uma vez por livro e por versão (prompt + modelos): mudou o prompt ou o modelo, o cache antigo é refeito.
    # Os trechos da web ficam na base RAG, então refazer não volta à web.
    if book.pesquisa is None or book.pesquisa.get("versao") != versao_pesquisa():
        try:
            achado = await pesquisar(session, book, capitulos)
            book.pesquisa = {**achado.model_dump(mode="json"), "versao": versao_pesquisa()}
        except PesquisaIndisponivel as exc:
            if sem_paginas and book.pesquisa is None:
                raise  # sem página no sumário e sem pesquisa, não há número honesto para calcular
            avisos.append(f"Pesquisa na web indisponível; páginas do sumário usadas sem conferência ({exc}).")
    pesquisa = Pesquisa.model_validate(book.pesquisa) if book.pesquisa else None

    if sem_paginas and (pesquisa is None or pesquisa.paginas_totais is None):
        await session.commit()  # guarda a pesquisa e a base RAG: a próxima tentativa não refaz a busca à toa
        motivo = pesquisa.justificativa if pesquisa else ""
        raise HTTPException(
            422, f"Sumário sem números de página e nenhuma fonte confiável com o total de páginas. {motivo} Envie um sumário paginado."
        )
    capitulos, origem, avisos_paginas = resolver_paginas(capitulos, book.paginas_conteudo, pesquisa)
    avisos += avisos_paginas

    if book.classificacao is None or book.classificacao.get("versao") != versao_classificacao():
        # Cache entre usuários: classifica uma vez por livro e por versão (prompt + modelo).
        book.classificacao = {**(await classificar(book.titulo, capitulos)).model_dump(), "versao": versao_classificacao()}
    cls = Classificacao.model_validate(book.classificacao)
    disponibilidade = pedido.disponibilidade_horas
    meta = None
    if disponibilidade is None:
        assert pedido.data_limite and semanas_prazo  # garantido pelo validador e pelo 422 acima
        # O total de horas não depende da disponibilidade: calcula com qualquer valor e inverte pelo prazo.
        total = planejar(capitulos, cls, pedido.senioridade, 1.0).total_horas
        disponibilidade = disponibilidade_para_prazo(total, semanas_prazo) if total else 0.0
        if disponibilidade > 168:
            raise HTTPException(
                422, f"Prazo inviável: exigiria {disponibilidade:g} h/semana ({total:.1f} h em {semanas_prazo} semana(s)); o máximo é 168."
            )
        meta = Meta(data_limite=pedido.data_limite, semanas_ate_o_prazo=semanas_prazo)
    plano = planejar(capitulos, cls, pedido.senioridade, disponibilidade)
    plano = plano.model_copy(update={"paginas": origem, "avisos": avisos, "meta": meta})

    roadmap = Roadmap(
        book_id=book.id,
        senioridade=pedido.senioridade,
        disponibilidade_horas=disponibilidade,
        plano=plano.model_dump(mode="json"),
    )
    session.add(roadmap)
    await session.commit()
    return RoadmapCriado(id=roadmap.id, book_id=book.id, **plano.model_dump())


class RoadmapResumo(BaseModel):
    id: int
    senioridade: Senioridade
    disponibilidade_horas: float
    total_horas: float
    semanas: int
    criado_em: datetime


@app.get("/books/{book_id}/roadmaps")
async def listar_roadmaps(book_id: int, session: SessionDep) -> list[RoadmapResumo]:
    """Roadmaps do livro, do mais recente ao mais antigo. Só lê o que já foi persistido."""
    if not await session.get(Book, book_id):
        raise HTTPException(404, "Livro não encontrado.")
    roadmaps = await session.scalars(select(Roadmap).where(Roadmap.book_id == book_id).order_by(Roadmap.id.desc()))
    return [
        RoadmapResumo(
            id=r.id,
            senioridade=r.senioridade,  # type: ignore[arg-type]
            disponibilidade_horas=r.disponibilidade_horas,
            total_horas=r.plano["total_horas"],
            semanas=r.plano["semanas"],
            criado_em=r.criado_em,
        )
        for r in roadmaps
    ]


@app.get("/roadmaps/{roadmap_id}")
async def obter_roadmap(roadmap_id: int, session: SessionDep) -> RoadmapCriado:
    roadmap = await session.get(Roadmap, roadmap_id)
    if not roadmap:
        raise HTTPException(404, "Roadmap não encontrado.")
    return RoadmapCriado(id=roadmap.id, book_id=roadmap.book_id, **Plano.model_validate(roadmap.plano).model_dump())


@app.delete("/roadmaps/{roadmap_id}", status_code=204)
async def apagar_roadmap(roadmap_id: int, session: SessionDep) -> None:
    roadmap = await session.get(Roadmap, roadmap_id)
    if not roadmap:
        raise HTTPException(404, "Roadmap não encontrado.")
    await session.delete(roadmap)
    await session.commit()


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
