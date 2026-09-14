"""Fluxo HTTP de roadmap contra o Postgres do compose, com classificador mockado. Skip se o banco estiver fora."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text

from src import main
from src.classificador import CapituloClassificado, Classificacao
from src.db import Session, engine
from src.extracao import Capitulo
from src.models import Book, Roadmap

CAPS = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=i * 20, paginas=20).model_dump() for i in (1, 2, 3)]
CLS = Classificacao(
    tipo_livro="hibrido",
    densidade="media",
    nivel_natural="intermediario",
    linguagens=["Python"],
    capitulos=[CapituloClassificado(num=i, nivel=n, peso=1.0) for i, n in [(1, "iniciante"), (2, "intermediario"), (3, "avancado")]],
)
PEDIDO = {"senioridade": "Pleno", "disponibilidade_horas": 5}


@pytest.mark.asyncio
async def test_fluxo_roadmap(monkeypatch: pytest.MonkeyPatch):
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    except OSError:
        pytest.skip("Postgres fora do ar (docker compose up -d db)")

    chamadas = 0

    async def classificar_fake(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        nonlocal chamadas
        chamadas += 1
        return CLS

    monkeypatch.setattr(main, "classificar", classificar_fake)
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
    finally:
        async with Session() as s:
            await s.execute(delete(Roadmap).where(Roadmap.book_id.in_([book.id, capa.id])))
            await s.execute(delete(Book).where(Book.id.in_([book.id, capa.id])))
            await s.commit()
        await engine.dispose()
