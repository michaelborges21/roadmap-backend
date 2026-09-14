"""Fluxo HTTP e RAG contra o Postgres do compose, com LLM e embedding mockados. Skip se o banco estiver fora."""

import hashlib
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text

from src import main, rag
from src.classificador import Capa, CapituloClassificado, Classificacao, Explicacao
from src.db import Session, engine
from src.extracao import Capitulo
from src.models import Book, Chunk, Roadmap
from src.rag import referencias

CAPS = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=i * 20, paginas=20).model_dump() for i in (1, 2, 3)]
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
E2 = [0.0, 1.0] + [0.0] * 766


async def banco_no_ar() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_fluxo_roadmap(monkeypatch: pytest.MonkeyPatch):
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    chamadas = 0
    explicados: list[dict[str, object]] = []

    async def classificar_fake(titulo: str, capitulos: list[Capitulo], refs: object = None) -> Classificacao:
        nonlocal chamadas
        chamadas += 1
        return CLS

    async def embeddar_fake(textos: list[str]) -> list[list[float]]:
        return [E1 for _ in textos]

    async def explicar_fake(capitulo: str, dados: dict[str, object]) -> Explicacao:
        explicados.append(dados)
        return Explicacao(explicacao="ok")

    monkeypatch.setattr(main, "classificar", classificar_fake)
    monkeypatch.setattr(rag, "embeddar", embeddar_fake)
    monkeypatch.setattr(main, "explicar", explicar_fake)
    async with Session() as s:
        book = Book(hash_fonte=f"teste-{uuid.uuid4().hex}", titulo="Livro", origem="sumario", paginas_conteudo=60, capitulos=CAPS)
        capa = Book(hash_fonte=f"teste-{uuid.uuid4().hex}", titulo="Só capa", origem="capa", capitulos=[])
        s.add_all([book, capa])
        await s.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://teste") as http:
            planos = {}
            for sen in ("Junior", "Pleno", "Senior"):
                r = await http.post(f"/books/{book.id}/roadmaps", json={**PEDIDO, "senioridade": sen})
                assert r.status_code == 201, r.text
                planos[sen] = r.json()

            assert chamadas == 1  # classificação persistida no book e reaproveitada
            assert [len(planos[s]["capitulos"]) for s in ("Junior", "Pleno", "Senior")] == [3, 2, 1]
            assert planos["Junior"]["total_horas"] > planos["Pleno"]["total_horas"] > planos["Senior"]["total_horas"]
            assert (await http.post(f"/books/{capa.id}/roadmaps", json=PEDIDO)).status_code == 422
            assert (await http.post("/books/999999999/roadmaps", json=PEDIDO)).status_code == 404
            assert (await http.post(f"/books/{book.id}/roadmaps", json={**PEDIDO, "disponibilidade_horas": 0})).status_code == 422

            async with Session() as s:
                assert await s.scalar(select(func.count()).where(Chunk.book_id == book.id)) == 3  # 1 chunk por capítulo

            senior = planos["Senior"]["id"]
            assert (await http.get(f"/roadmaps/{senior}/explicar", params={"capitulo": 3})).status_code == 200
            assert (await http.get(f"/roadmaps/{senior}/explicar", params={"capitulo": 1})).status_code == 200
            assert "fatores" in explicados[0] and "fora_do_cronograma" in explicados[1]
            assert (await http.get(f"/roadmaps/{senior}/explicar", params={"capitulo": 9})).status_code == 404
    finally:
        async with Session() as s:
            ids = [book.id, capa.id]
            await s.execute(delete(Roadmap).where(Roadmap.book_id.in_(ids)))
            await s.execute(delete(Chunk).where(Chunk.book_id.in_(ids)))
            await s.execute(delete(Book).where(Book.id.in_(ids)))
            await s.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_upload_book_cache_e_erros(monkeypatch: pytest.MonkeyPatch):
    sumario, sem_fronteira = FIXTURES / "sumario-9788575229682.pdf", FIXTURES / "sumario-9788575228173.pdf"
    if not (sumario.exists() and sem_fronteira.exists()):
        pytest.skip("fixtures ausentes em test_files/")
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    async def ler_capa_fake(imagem: bytes) -> Capa:
        return Capa(titulo="JavaScript", subtitulo="O guia definitivo", autor=None, edicao=None)

    monkeypatch.setattr(main, "ler_capa", ler_capa_fake)
    # Bytes após o %%EOF mudam o hash sem afetar a leitura: não colide com livros reais do banco.
    marca = f"\n%{uuid.uuid4().hex}\n".encode()
    pdf, pdf_ambiguo, imagem = sumario.read_bytes() + marca, sem_fronteira.read_bytes() + marca, b"\xff\xd8" + marca
    hashes = [hashlib.sha256(d).hexdigest() for d in (pdf, imagem)]

    try:
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://teste") as http:
            envio = lambda nome, dados, tipo: http.post("/books", files={"arquivo": (nome, dados, tipo)})  # noqa: E731

            criado = await envio("sumario.pdf", pdf, "application/pdf")
            assert criado.status_code == 201, criado.text
            assert (len(criado.json()["capitulos"]), criado.json()["falta_sumario"]) == (27, False)

            repetido = await envio("outro-nome.pdf", pdf, "application/pdf")
            assert (repetido.status_code, repetido.json()["id"]) == (200, criado.json()["id"])  # cache por hash

            capa = await envio("capa.jpg", imagem, "image/jpeg")
            assert capa.status_code == 201, capa.text
            assert (capa.json()["titulo"], capa.json()["origem"], capa.json()["falta_sumario"]) == ("JavaScript: O guia definitivo", "capa", True)

            assert (await envio("notas.txt", b"texto", "text/plain")).status_code == 415
            ambiguo = await envio("sumario.pdf", pdf_ambiguo, "application/pdf")
            assert ambiguo.status_code == 422 and "sem fronteira final" in ambiguo.json()["detail"]
    finally:
        async with Session() as s:
            await s.execute(delete(Book).where(Book.hash_fonte.in_(hashes)))
            await s.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_referencias_so_de_outros_livros_classificados():
    if not await banco_no_ar():
        pytest.skip("Postgres fora do ar (docker compose up -d db)")
    cls_b = CLS.model_copy(update={"capitulos": [CapituloClassificado(num=1, nivel="avancado", peso=1.5), CapituloClassificado(num=2, nivel="iniciante", peso=0.5)]})
    try:
        async with Session() as s:
            a = Book(hash_fonte=f"teste-{uuid.uuid4().hex}", titulo="Livro A", origem="sumario", capitulos=[{"num": 1, "titulo": "A1"}])
            b = Book(
                hash_fonte=f"teste-{uuid.uuid4().hex}",
                titulo="Livro B",
                origem="sumario",
                capitulos=[{"num": 1, "titulo": "B1"}, {"num": 2, "titulo": "B2"}],
                classificacao=cls_b.model_dump(),
            )
            s.add_all([a, b])
            await s.flush()
            s.add_all([Chunk(book_id=a.id, texto="A1", embedding=E1), Chunk(book_id=b.id, texto="B1", embedding=E1), Chunk(book_id=b.id, texto="B2", embedding=E2)])
            await s.flush()
            refs = await referencias(s, a.id, [Capitulo(num=1, titulo="A1", pag_inicio=1, paginas=10)])
            await s.rollback()  # nada persiste
    finally:
        await engine.dispose()

    primeira = refs[1][0]
    assert (primeira.livro, primeira.capitulo, primeira.nivel, primeira.peso) == ("Livro B", "B1", "avancado", 1.5)
    assert all(r.livro != "Livro A" and r.capitulo != "B2" for r in refs[1])  # nem o próprio livro, nem abaixo do limiar
