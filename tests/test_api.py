"""Fluxo HTTP e base RAG da pesquisa contra o Postgres do compose, com LLM, web e embedding mockados.
Skip se o banco estiver fora."""

import hashlib
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

from src import main, pesquisa
from src.classificador import Capa, CapituloClassificado, Classificacao, Explicacao
from src.db import Session, engine
from src.extracao import Capitulo
from src.models import Book, Chunk, Roadmap
from src.pesquisa import PaginasEncontradas, Pesquisa, PesquisaIndisponivel, pesquisar

CAPS = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=i * 20, paginas=20).model_dump() for i in (1, 2, 3)]
CAPS_SEM_PAGINAS = [
    Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=None, paginas=None, subtopicos=["s"] * i).model_dump() for i in (1, 2, 3)
]
CLS = Classificacao(
    tipo_livro="hibrido",
    densidade="media",
    nivel_natural="intermediario",
    linguagens=["Python"],
    capitulos=[CapituloClassificado(num=i, nivel=n, peso=1.0) for i, n in [(1, "iniciante"), (2, "intermediario"), (3, "avancado")]],
)
PEDIDO = {"senioridade": "Pleno", "disponibilidade_horas": 5}
FIXTURES = Path(__file__).parent.parent / "test_files"
E1 = [1.0] + [0.0] * 767
EDITORA = "https://editora.exemplo/livro"


async def banco_no_ar() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        return True
    except OSError:
        return False


def novo_book(titulo: str, capitulos: list[dict], **kw: object) -> Book:
    origem = "sumario" if capitulos else "capa"
    return Book(hash_fonte=f"teste-{uuid.uuid4().hex}", titulo=titulo, origem=origem, capitulos=capitulos, **kw)


async def criar(*books: Book) -> None:
    async with Session() as s:
        s.add_all(books)
        await s.commit()


async def apagar(ids: list[int]) -> None:
    async with Session() as s:
        await s.execute(delete(Roadmap).where(Roadmap.book_id.in_(ids)))
        await s.execute(delete(Chunk).where(Chunk.book_id.in_(ids)))
        await s.execute(delete(Book).where(Book.id.in_(ids)))
        await s.commit()


def cliente() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app), base_url="http://teste")


@pytest.mark.asyncio
async def test_fluxo_roadmap(monkeypatch: pytest.MonkeyPatch):
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    chamadas = 0
    explicados: list[dict[str, object]] = []

    async def classificar_fake(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        nonlocal chamadas
        chamadas += 1
        return CLS

    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        return Pesquisa(paginas_totais=None, url_fonte=None, justificativa="sem fonte", fontes_consultadas=[])

    async def explicar_fake(capitulo: str, dados: dict[str, object]) -> Explicacao:
        explicados.append(dados)
        return Explicacao(explicacao="ok")

    monkeypatch.setattr(main, "classificar", classificar_fake)
    monkeypatch.setattr(main, "pesquisar", pesquisar_fake)
    monkeypatch.setattr(main, "explicar", explicar_fake)
    book, capa = novo_book("Livro", CAPS, paginas_conteudo=60), novo_book("Só capa", [])
    await criar(book, capa)

    try:
        async with cliente() as http:
            planos = {}
            for sen in ("Junior", "Pleno", "Senior"):
                r = await http.post(f"/books/{book.id}/roadmaps", json={**PEDIDO, "senioridade": sen})
                assert r.status_code == 201, r.text
                planos[sen] = r.json()

            assert chamadas == 1  # classificação persistida no book e reaproveitada
            assert [len(planos[s]["capitulos"]) for s in ("Junior", "Pleno", "Senior")] == [3, 2, 1]
            assert planos["Junior"]["total_horas"] > planos["Pleno"]["total_horas"] > planos["Senior"]["total_horas"]
            assert (planos["Pleno"]["paginas"]["origem"], planos["Pleno"]["avisos"]) == ("sumario", [])
            assert (await http.post(f"/books/{capa.id}/roadmaps", json=PEDIDO)).status_code == 422
            assert (await http.post("/books/999999999/roadmaps", json=PEDIDO)).status_code == 404
            assert (await http.post(f"/books/{book.id}/roadmaps", json={**PEDIDO, "disponibilidade_horas": 0})).status_code == 422

            senior = planos["Senior"]["id"]
            assert (await http.get(f"/roadmaps/{senior}/explicar", params={"capitulo": 3})).status_code == 200
            assert (await http.get(f"/roadmaps/{senior}/explicar", params={"capitulo": 1})).status_code == 200
            assert "fatores" in explicados[0] and "fora_do_cronograma" in explicados[1]
            assert (await http.get(f"/roadmaps/{senior}/explicar", params={"capitulo": 9})).status_code == 404
    finally:
        await apagar([book.id, capa.id])
        await engine.dispose()


@pytest.mark.asyncio
async def test_roadmap_de_sumario_sem_paginas(monkeypatch: pytest.MonkeyPatch):
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    async def classificar_fake(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        return CLS

    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        if "indisponível" in book.titulo:
            raise PesquisaIndisponivel("SearXNG fora do ar")
        if "sem fonte" in book.titulo:
            return Pesquisa(paginas_totais=None, url_fonte=None, justificativa="Nenhum trecho encontrado.", fontes_consultadas=[])
        return Pesquisa(
            paginas_totais=120,
            url_fonte=EDITORA,
            justificativa="Ficha da editora.",
            fontes_consultadas=[EDITORA],
            outras_contagens=["488 páginas (rede.exemplo)"],
        )

    monkeypatch.setattr(main, "classificar", classificar_fake)
    monkeypatch.setattr(main, "pesquisar", pesquisar_fake)
    com_fonte = novo_book("Livro com fonte", CAPS_SEM_PAGINAS)
    sem_fonte = novo_book("Livro sem fonte", CAPS_SEM_PAGINAS)
    sem_paginas_indisponivel = novo_book("Livro sem páginas, pesquisa indisponível", CAPS_SEM_PAGINAS)
    paginado_indisponivel = novo_book("Livro paginado, pesquisa indisponível", CAPS, paginas_conteudo=60)
    books = [com_fonte, sem_fonte, sem_paginas_indisponivel, paginado_indisponivel]
    await criar(*books)

    try:
        async with cliente() as http:
            r = await http.post(f"/books/{com_fonte.id}/roadmaps", json={**PEDIDO, "senioridade": "Junior"})
            assert r.status_code == 201, r.text
            p = r.json()
            assert (p["paginas"]["origem"], p["paginas"]["paginas_totais"], p["paginas"]["url_fonte"]) == ("pesquisa", 120, EDITORA)
            assert [c["paginas"] for c in p["capitulos"]] == [27, 40, 53]  # 1 + subtópicos: pesos 2, 3, 4
            assert any("488 páginas" in a for a in p["avisos"])  # divergência na web não some

            r = await http.post(f"/books/{sem_fonte.id}/roadmaps", json=PEDIDO)
            assert r.status_code == 422 and "Sumário sem números de página" in r.json()["detail"]

            assert (await http.post(f"/books/{sem_paginas_indisponivel.id}/roadmaps", json=PEDIDO)).status_code == 503

            r = await http.post(f"/books/{paginado_indisponivel.id}/roadmaps", json=PEDIDO)
            assert r.status_code == 201 and any("indisponível" in a for a in r.json()["avisos"])
    finally:
        await apagar([b.id for b in books])
        await engine.dispose()


@pytest.mark.asyncio
async def test_pesquisa_guarda_trechos_na_base_e_reaproveita(monkeypatch: pytest.MonkeyPatch):
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    buscas = 0
    prompts: list[str] = []

    async def buscar_web_fake(titulo: str) -> list[dict[str, str]]:
        nonlocal buscas
        buscas += 1
        return [
            {"url": EDITORA, "content": "Ano: 2026 Páginas: 344 Preço: R$ 139"},
            {"url": "https://loja.exemplo/outro", "content": "Outro livro de título parecido. Número de páginas: 448"},
        ]

    async def baixar_trechos_fake(http: object, url: str) -> list:
        return []

    async def embeddar_fake(textos: list[str], prefixo: str) -> list[list[float]]:
        return [E1 for _ in textos]

    async def chat_fake(modelo: object, papel: str, messages: list[dict[str, object]], schema: object) -> PaginasEncontradas:
        prompts.append(str(messages[1]["content"]))
        return PaginasEncontradas(mesmo_livro=True, paginas_totais=344, url_fonte=EDITORA, justificativa="Ficha técnica da editora.")

    monkeypatch.setattr(pesquisa, "buscar_web", buscar_web_fake)
    monkeypatch.setattr(pesquisa, "baixar_trechos", baixar_trechos_fake)
    monkeypatch.setattr(pesquisa, "embeddar", embeddar_fake)
    monkeypatch.setattr(pesquisa, "chat", chat_fake)
    caps = [Capitulo.model_validate(c) for c in CAPS_SEM_PAGINAS]

    try:
        async with Session() as s:
            book = novo_book("Livro pesquisado", CAPS_SEM_PAGINAS)
            s.add(book)
            await s.flush()
            primeira = await pesquisar(s, book, caps)
            segunda = await pesquisar(s, book, caps)
            fontes = sorted((await s.scalars(select(Chunk.fonte).where(Chunk.book_id == book.id))).all())
            await s.rollback()  # nada persiste
    finally:
        await engine.dispose()

    assert (primeira.paginas_totais, primeira.url_fonte) == (344, EDITORA)
    assert primeira.outras_contagens == ["448 páginas (loja.exemplo)"]
    assert buscas == 1 and segunda.paginas_totais == 344  # 2ª vez usa a base RAG, sem voltar à web
    assert fontes == [EDITORA, "https://loja.exemplo/outro"]
    assert "Páginas: 344" in prompts[0]


@pytest.mark.asyncio
async def test_upload_texto_capa_cache_e_erros(monkeypatch: pytest.MonkeyPatch):
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    async def ler_capa_fake(imagem: bytes) -> Capa:
        return Capa(titulo="JavaScript", subtitulo="O guia definitivo", autor=None, edicao=None)

    monkeypatch.setattr(main, "ler_capa", ler_capa_fake)
    marca = uuid.uuid4().hex  # conteúdo único por rodada: não colide com livros reais do banco
    paginado = f"Guia {marca}\n\nSumário\nCapítulo 1: Intro 1\nCapítulo 2: Meio 10\nÍndice 20\n".encode()
    sem_paginas = f"# Guia sem páginas {marca}\n\n## Sumário\n1. Intro\n   - Tópico\n2. Fim\nÍndice remissivo\n".encode()
    imagem = b"\xff\xd8" + marca.encode()
    ambiguo = f"Livro {marca}\n\nSumário\nCapítulo 1: A 10\nCapítulo 2: B 5\nÍndice 20\n".encode()
    hashes = [hashlib.sha256(d).hexdigest() for d in (paginado, sem_paginas, imagem, ambiguo)]

    try:
        async with cliente() as http:
            envio = lambda nome, dados, tipo: http.post("/books", files={"arquivo": (nome, dados, tipo)})  # noqa: E731

            criado = await envio("sumario.txt", paginado, "text/plain")
            assert criado.status_code == 201, criado.text
            assert [c["paginas"] for c in criado.json()["capitulos"]] == [9, 10]

            repetido = await envio("outro-nome.txt", paginado, "text/plain")
            assert (repetido.status_code, repetido.json()["id"]) == (200, criado.json()["id"])  # cache por hash

            # .md com content-type genérico (comum em navegadores): extensão desempata. Sem paginação: páginas nulas.
            md = await envio("sumario.md", sem_paginas, "application/octet-stream")
            assert md.status_code == 201, md.text
            assert [(c["titulo"], c["paginas"]) for c in md.json()["capitulos"]] == [("Intro", None), ("Fim", None)]

            capa = await envio("capa.jpg", imagem, "image/jpeg")
            assert capa.status_code == 201, capa.text
            assert (capa.json()["titulo"], capa.json()["origem"], capa.json()["falta_sumario"]) == ("JavaScript: O guia definitivo", "capa", True)

            assert (await envio("notas.csv", b"a,b,c", "text/csv")).status_code == 415
            ambiguo_r = await envio("ambiguo.txt", ambiguo, "text/plain")
            assert ambiguo_r.status_code == 422 and "não crescente" in ambiguo_r.json()["detail"]
    finally:
        async with Session() as s:
            await s.execute(delete(Book).where(Book.hash_fonte.in_(hashes)))
            await s.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_upload_pdf_cache_e_erros(monkeypatch: pytest.MonkeyPatch):
    sumario, sem_fronteira = FIXTURES / "sumario-9788575229682.pdf", FIXTURES / "sumario-9788575228173.pdf"
    if not (sumario.exists() and sem_fronteira.exists()):
        pytest.skip("fixtures PDF ausentes em test_files/")
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    # Bytes após o %%EOF mudam o hash sem afetar a leitura: não colide com livros reais do banco.
    marca = f"\n%{uuid.uuid4().hex}\n".encode()
    pdf, pdf_ambiguo = sumario.read_bytes() + marca, sem_fronteira.read_bytes() + marca
    hashes = [hashlib.sha256(pdf).hexdigest()]

    try:
        async with cliente() as http:
            envio = lambda nome, dados: http.post("/books", files={"arquivo": (nome, dados, "application/pdf")})  # noqa: E731
            criado = await envio("sumario.pdf", pdf)
            assert criado.status_code == 201, criado.text
            assert (await envio("outro-nome.pdf", pdf)).status_code == 200
            ambiguo = await envio("sumario.pdf", pdf_ambiguo)
            assert ambiguo.status_code == 422 and "sem fronteira final" in ambiguo.json()["detail"]
    finally:
        async with Session() as s:
            await s.execute(delete(Book).where(Book.hash_fonte.in_(hashes)))
            await s.commit()
        await engine.dispose()
