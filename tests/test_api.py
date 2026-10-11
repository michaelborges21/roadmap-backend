"""Fluxo HTTP e base RAG da pesquisa contra o Postgres do compose, com LLM, web e embedding mockados.
Skip se o banco estiver fora."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
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


@pytest_asyncio.fixture
async def banco() -> AsyncIterator[None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    except OSError:
        pytest.skip("Postgres fora do ar (docker compose up -d db)")
    yield
    await engine.dispose()


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


async def apagar_por_hash(hashes: list[str]) -> None:
    async with Session() as s:
        await s.execute(delete(Book).where(Book.hash_fonte.in_(hashes)))
        await s.commit()


def cliente() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app), base_url="http://teste")


async def classificar_fake(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
    return CLS


@pytest.mark.asyncio
async def test_fluxo_roadmap(monkeypatch: pytest.MonkeyPatch, banco: None):
    chamadas = 0
    explicados: list[dict[str, object]] = []

    async def classificar_contando(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        nonlocal chamadas
        chamadas += 1
        return CLS

    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        return Pesquisa(justificativa="sem fonte")

    async def explicar_fake(capitulo: str, dados: dict[str, object]) -> Explicacao:
        explicados.append(dados)
        return Explicacao(explicacao="ok")

    monkeypatch.setattr(main, "classificar", classificar_contando)
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


@pytest.mark.asyncio
async def test_roadmap_de_sumario_sem_paginas(monkeypatch: pytest.MonkeyPatch, banco: None):
    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        if "indisponível" in book.titulo:
            raise PesquisaIndisponivel("SearXNG fora do ar")
        if "sem fonte" in book.titulo:
            return Pesquisa(justificativa="Nenhum trecho encontrado.")
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
            conteudo = round(120 * pesquisa.config.calculo.fracao_conteudo_pesquisa)  # total da web → conteúdo
            origem = p["paginas"]
            assert (origem["origem"], origem["paginas_totais"], origem["paginas_conteudo"], origem["url_fonte"]) == ("pesquisa", 120, conteudo, EDITORA)
            paginas = [c["paginas"] for c in p["capitulos"]]
            assert sum(paginas) == conteudo and paginas[0] < paginas[1] < paginas[2]  # 1 + subtópicos: pesos 2, 3, 4
            assert any("488 páginas" in a for a in p["avisos"])  # divergência na web não some

            r = await http.post(f"/books/{sem_fonte.id}/roadmaps", json=PEDIDO)
            assert r.status_code == 422 and "Sumário sem números de página" in r.json()["detail"]

            assert (await http.post(f"/books/{sem_paginas_indisponivel.id}/roadmaps", json=PEDIDO)).status_code == 503

            r = await http.post(f"/books/{paginado_indisponivel.id}/roadmaps", json=PEDIDO)
            assert r.status_code == 201 and any("indisponível" in a for a in r.json()["avisos"])
    finally:
        await apagar([b.id for b in books])


@pytest.mark.asyncio
async def test_pesquisa_guarda_trechos_na_base_e_reaproveita(monkeypatch: pytest.MonkeyPatch, banco: None):
    buscas = 0
    prompts: list[str] = []

    async def buscar_web_fake(titulo: str) -> list[dict[str, str]]:
        nonlocal buscas
        buscas += 1
        return [
            {"url": EDITORA, "content": "Ano: 2026 Páginas: 344 Preço: R$ 139"},
            {"url": "https://loja.exemplo/outro", "content": "Outro livro de título parecido. Número de páginas: 448"},
            {"url": "https://pt.linkedin.com/posts/lancamentos", "content": "Três lançamentos: livro de 401 páginas"},
        ]

    async def baixar_trechos_fake(http: object, url: str) -> list:
        return []

    async def embeddar_fake(textos: list[str], prefixo: str) -> list[list[float]]:
        return [E1 for _ in textos]

    async def chat_fake(modelo: object, papel: str, messages: list[dict[str, object]], schema: object) -> PaginasEncontradas:
        conteudo = str(messages[1]["content"])
        prompts.append(conteudo)
        todos = list(range(1, conteudo.count("\n[") + 1))  # o fake diz que todos os trechos são deste livro
        return PaginasEncontradas(
            mesmo_livro=True, paginas_totais=344, url_fonte=EDITORA, trechos_deste_livro=todos, justificativa="Ficha técnica da editora."
        )

    monkeypatch.setattr(pesquisa, "buscar_web", buscar_web_fake)
    monkeypatch.setattr(pesquisa, "baixar_trechos", baixar_trechos_fake)
    monkeypatch.setattr(pesquisa, "embeddar", embeddar_fake)
    monkeypatch.setattr(pesquisa, "chat", chat_fake)
    caps = [Capitulo.model_validate(c) for c in CAPS_SEM_PAGINAS]

    async with Session() as s:
        book = novo_book("Livro pesquisado", CAPS_SEM_PAGINAS)
        s.add(book)
        await s.flush()
        primeira = await pesquisar(s, book, caps)
        segunda = await pesquisar(s, book, caps)
        fontes = sorted((await s.scalars(select(Chunk.fonte).where(Chunk.book_id == book.id))).all())
        await s.rollback()  # nada persiste

    assert (primeira.paginas_totais, primeira.url_fonte) == (344, EDITORA)
    assert primeira.outras_contagens == ["448 páginas (loja.exemplo)"]
    assert buscas == 1 and segunda.paginas_totais == 344  # 2ª vez usa a base RAG, sem voltar à web
    assert fontes == [EDITORA, "https://loja.exemplo/outro"]
    assert "Páginas: 344" in prompts[0]


@pytest.mark.asyncio
async def test_upload_texto_capa_cache_e_erros(monkeypatch: pytest.MonkeyPatch, banco: None):
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
        await apagar_por_hash(hashes)


@pytest.mark.asyncio
async def test_upload_pdf_cache_e_erros(banco: None):
    sumario, sem_titulo = FIXTURES / "sumario-9788575229682.pdf", FIXTURES / "sumario-9788575229293.pdf"
    if not (sumario.exists() and sem_titulo.exists()):
        pytest.skip("fixtures PDF ausentes em test_files/")

    # Bytes após o %%EOF mudam o hash sem afetar a leitura: não colide com livros reais do banco.
    marca = f"\n%{uuid.uuid4().hex}\n".encode()
    pdf, pdf_ambiguo = sumario.read_bytes() + marca, sem_titulo.read_bytes() + marca

    try:
        async with cliente() as http:
            envio = lambda nome, dados: http.post("/books", files={"arquivo": (nome, dados, "application/pdf")})  # noqa: E731
            criado = await envio("sumario.pdf", pdf)
            assert criado.status_code == 201, criado.text
            assert (await envio("outro-nome.pdf", pdf)).status_code == 200
            ambiguo = await envio("sumario.pdf", pdf_ambiguo)
            assert ambiguo.status_code == 422 and "sem título legível" in ambiguo.json()["detail"]
    finally:
        await apagar_por_hash([hashlib.sha256(pdf).hexdigest()])


@pytest.mark.asyncio
async def test_listar_obter_e_apagar_roadmap(monkeypatch: pytest.MonkeyPatch, banco: None):
    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        return Pesquisa(justificativa="sem fonte")

    monkeypatch.setattr(main, "classificar", classificar_fake)
    monkeypatch.setattr(main, "pesquisar", pesquisar_fake)
    book = novo_book("Livro", CAPS, paginas_conteudo=60)
    await criar(book)

    try:
        async with cliente() as http:
            junior = (await http.post(f"/books/{book.id}/roadmaps", json={**PEDIDO, "senioridade": "Junior"})).json()
            senior = (await http.post(f"/books/{book.id}/roadmaps", json={**PEDIDO, "senioridade": "Senior"})).json()

            lista = await http.get(f"/books/{book.id}/roadmaps")
            assert lista.status_code == 200
            assert [r["id"] for r in lista.json()] == [senior["id"], junior["id"]]  # mais recente primeiro
            assert lista.json()[1]["total_horas"] == junior["total_horas"] and lista.json()[1]["senioridade"] == "Junior"

            lido = await http.get(f"/roadmaps/{junior['id']}")
            assert (lido.status_code, lido.json()) == (200, junior)  # o mesmo plano que o POST devolveu

            assert (await http.delete(f"/roadmaps/{junior['id']}")).status_code == 204
            assert (await http.get(f"/roadmaps/{junior['id']}")).status_code == 404
            assert (await http.delete(f"/roadmaps/{junior['id']}")).status_code == 404
            assert [r["id"] for r in (await http.get(f"/books/{book.id}/roadmaps")).json()] == [senior["id"]]
            assert (await http.get("/books/999999999/roadmaps")).status_code == 404
    finally:
        await apagar([book.id])


@pytest.mark.asyncio
async def test_cache_e_refeito_so_quando_prompt_ou_modelo_mudam(monkeypatch: pytest.MonkeyPatch, banco: None):
    classificacoes = pesquisas = 0

    async def classificar_contando(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        nonlocal classificacoes
        classificacoes += 1
        return CLS

    async def pesquisar_contando(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        nonlocal pesquisas
        pesquisas += 1
        return Pesquisa(justificativa="sem fonte")

    monkeypatch.setattr(main, "classificar", classificar_contando)
    monkeypatch.setattr(main, "pesquisar", pesquisar_contando)
    novo = novo_book("Livro novo", CAPS, paginas_conteudo=60)
    # Cache gravado antes do versionamento (sem chave "versao"): vale como desatualizado.
    legado = novo_book("Livro legado", CAPS, paginas_conteudo=60, classificacao=CLS.model_dump(), pesquisa=Pesquisa(justificativa="velha").model_dump())
    await criar(novo, legado)

    async def roadmap(http: AsyncClient, book: Book) -> None:
        assert (await http.post(f"/books/{book.id}/roadmaps", json=PEDIDO)).status_code == 201

    try:
        async with cliente() as http:
            await roadmap(http, novo)
            await roadmap(http, novo)
            assert (classificacoes, pesquisas) == (1, 1)  # mesma versão: reaproveita

            await roadmap(http, legado)
            assert (classificacoes, pesquisas) == (2, 2)  # sem versão: refeito uma vez
            await roadmap(http, legado)
            assert (classificacoes, pesquisas) == (2, 2)

            monkeypatch.setattr(main.config.llm, "modelo_classificador", "outro-modelo:1b")
            await roadmap(http, novo)
            assert (classificacoes, pesquisas) == (3, 3)  # modelo mudou: os dois caches são refeitos
            await roadmap(http, novo)
            assert (classificacoes, pesquisas) == (3, 3)
    finally:
        await apagar([novo.id, legado.id])


@pytest.mark.asyncio
async def test_roadmaps_simultaneos_do_mesmo_livro_classificam_e_pesquisam_uma_vez(monkeypatch: pytest.MonkeyPatch, banco: None):
    import asyncio

    classificacoes = pesquisas = 0

    async def classificar_lento(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        nonlocal classificacoes
        classificacoes += 1
        await asyncio.sleep(0.3)  # tempo para o outro pedido chegar enquanto este ainda trabalha
        return CLS

    async def pesquisar_lento(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        nonlocal pesquisas
        pesquisas += 1
        await asyncio.sleep(0.3)
        return Pesquisa(justificativa="sem fonte")

    monkeypatch.setattr(main, "classificar", classificar_lento)
    monkeypatch.setattr(main, "pesquisar", pesquisar_lento)
    mesmo, mesmo_2, outro = (novo_book(t, CAPS, paginas_conteudo=60) for t in ("Mesmo livro", "Livro paralelo A", "Livro paralelo B"))
    await criar(mesmo, mesmo_2, outro)

    try:
        async with cliente() as http:
            pedidos = [http.post(f"/books/{mesmo.id}/roadmaps", json={**PEDIDO, "senioridade": s}) for s in ("Junior", "Pleno", "Senior")]
            respostas = await asyncio.gather(*pedidos)
            assert [r.status_code for r in respostas] == [201, 201, 201]
            assert (classificacoes, pesquisas) == (1, 1)  # os outros dois esperaram e reaproveitaram

            # Livros diferentes não se bloqueiam: cada um leva ~0,6s (classificar + pesquisar); em série seria >= 1,2s.
            classificacoes = pesquisas = 0
            inicio = asyncio.get_running_loop().time()
            respostas = await asyncio.gather(*(http.post(f"/books/{b.id}/roadmaps", json=PEDIDO) for b in (mesmo_2, outro)))
            assert [r.status_code for r in respostas] == [201, 201]
            assert (classificacoes, pesquisas) == (2, 2)
            assert asyncio.get_running_loop().time() - inicio < 1.15
    finally:
        await apagar([mesmo.id, mesmo_2.id, outro.id])


@pytest.mark.asyncio
async def test_roadmap_por_data_limite_calcula_a_disponibilidade(monkeypatch: pytest.MonkeyPatch, banco: None):
    from datetime import date, timedelta

    hoje = date(2026, 10, 12)
    monkeypatch.setattr(main, "hoje", lambda: hoje)
    monkeypatch.setattr(main, "classificar", classificar_fake)

    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        return Pesquisa(justificativa="sem fonte")

    monkeypatch.setattr(main, "pesquisar", pesquisar_fake)
    grandes = [Capitulo(num=i, titulo=f"Cap {i}", pag_inicio=i, paginas=5000).model_dump() for i in (1, 2, 3)]
    book, enorme = novo_book("Livro", CAPS, paginas_conteudo=60), novo_book("Livro enorme", grandes, paginas_conteudo=15000)
    await criar(book, enorme)
    limite = lambda dias: (hoje + timedelta(days=dias)).isoformat()  # noqa: E731

    try:
        async with cliente() as http:
            r = await http.post(f"/books/{book.id}/roadmaps", json={"senioridade": "Junior", "data_limite": limite(28)})
            assert r.status_code == 201, r.text
            plano = r.json()
            assert plano["meta"] == {"data_limite": limite(28), "semanas_ate_o_prazo": 4}
            assert 0 < plano["semanas"] <= 4  # cabe no prazo
            assert plano["disponibilidade_horas"] == pytest.approx(plano["total_horas"] / 4, abs=0.01)
            assert plano["disponibilidade_horas"] >= plano["total_horas"] / 4  # arredondou para cima, nunca para baixo

            fixo = await http.post(f"/books/{book.id}/roadmaps", json={"senioridade": "Junior", "disponibilidade_horas": 5})
            assert fixo.json()["meta"] is None
            assert (await http.get(f"/roadmaps/{plano['id']}")).json() == plano  # persistido junto com o plano

            invalidos = [
                {"senioridade": "Junior"},  # nenhum dos dois
                {"senioridade": "Junior", "disponibilidade_horas": 5, "data_limite": limite(28)},  # os dois
                {"senioridade": "Junior", "data_limite": hoje.isoformat()},  # hoje não é futuro
                {"senioridade": "Junior", "data_limite": limite(-3)},  # passado
            ]
            for corpo in invalidos:
                assert (await http.post(f"/books/{book.id}/roadmaps", json=corpo)).status_code == 422, corpo

            inviavel = await http.post(f"/books/{enorme.id}/roadmaps", json={"senioridade": "Junior", "data_limite": limite(1)})
            assert inviavel.status_code == 422 and "inviável" in inviavel.json()["detail"]
    finally:
        await apagar([book.id, enorme.id])


@pytest.mark.asyncio
async def test_ajustar_roadmap_recalcula_plano_determiniscamente(monkeypatch: pytest.MonkeyPatch, banco: None):
    monkeypatch.setattr(main, "classificar", classificar_fake)

    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        return Pesquisa(justificativa="sem fonte")

    monkeypatch.setattr(main, "pesquisar", pesquisar_fake)
    book = novo_book("Livro", CAPS, paginas_conteudo=60)
    await criar(book)

    try:
        async with cliente() as http:
            # 1. Criação inicial: Pleno inclui cap 2 e 3; cap 1 (iniciante) fica excluído
            r = await http.post(f"/books/{book.id}/roadmaps", json={"senioridade": "Pleno", "disponibilidade_horas": 5})
            assert r.status_code == 201
            base = r.json()
            assert [c["num"] for c in base["capitulos"]] == [2, 3]
            assert [e["num"] for e in base["excluidos"]] == [1]
            assert base["ajustes"] == []

            # 2. PATCH ajusta nível do cap 1 para 'intermediario': cap 1 entra no cronograma!
            r_patch = await http.patch(
                f"/roadmaps/{base['id']}",
                json={"ajustes": [{"num": 1, "nivel": "intermediario"}]},
            )
            assert r_patch.status_code == 200
            ajustado = r_patch.json()
            assert [c["num"] for c in ajustado["capitulos"]] == [1, 2, 3]
            assert ajustado["excluidos"] == []
            assert ajustado["total_horas"] > base["total_horas"]
            assert len(ajustado["ajustes"]) == 1
            assert ajustado["ajustes"][0]["num"] == 1
            assert ajustado["ajustes"][0]["nivel"] == "intermediario"

            # 3. PATCH ajusta peso e páginas: horas recalculadas deterministicamente
            r_horas = await http.patch(
                f"/roadmaps/{base['id']}",
                json={"ajustes": [{"num": 2, "peso": 2.0}, {"num": 3, "paginas": 40}]},
            )
            assert r_horas.status_code == 200
            h_data = r_horas.json()
            cap2 = next(c for c in h_data["capitulos"] if c["num"] == 2)
            cap3 = next(c for c in h_data["capitulos"] if c["num"] == 3)
            base_cap2 = next(c for c in base["capitulos"] if c["num"] == 2)
            base_cap3 = next(c for c in base["capitulos"] if c["num"] == 3)
            # peso 2.0 vs 1.0 dobra o tempo do cap 2
            assert cap2["total"] == pytest.approx(base_cap2["total"] * 2.0, rel=1e-3)
            # páginas 40 vs 20 dobra o tempo do cap 3
            assert cap3["total"] == pytest.approx(base_cap3["total"] * 2.0, rel=1e-3)

            # 4. PATCH com ajustes: [] remove ajustes e volta ao original
            r_reset = await http.patch(f"/roadmaps/{base['id']}", json={"ajustes": []})
            assert r_reset.status_code == 200
            assert r_reset.json()["total_horas"] == pytest.approx(base["total_horas"], rel=1e-3)
            assert r_reset.json()["ajustes"] == []

            # 5. PATCH alterando disponibilidade_horas e senioridade
            r_mudanca = await http.patch(
                f"/roadmaps/{base['id']}", json={"senioridade": "Senior", "disponibilidade_horas": 10}
            )
            assert r_mudanca.status_code == 200
            assert r_mudanca.json()["senioridade"] == "Senior"
            assert r_mudanca.json()["disponibilidade_horas"] == 10
            # Senior só inclui cap 3 (avançado)
            assert [c["num"] for c in r_mudanca.json()["capitulos"]] == [3]

            # 6. POST direto com ajustes manuais já na criação
            r_post_ajuste = await http.post(
                f"/books/{book.id}/roadmaps",
                json={
                    "senioridade": "Pleno",
                    "disponibilidade_horas": 5,
                    "ajustes": [{"num": 1, "nivel": "intermediario", "paginas": 30}],
                },
            )
            assert r_post_ajuste.status_code == 201
            assert [c["num"] for c in r_post_ajuste.json()["capitulos"]] == [1, 2, 3]

            # 7. Validações e erros
            assert (await http.patch("/roadmaps/999999999", json={"ajustes": []})).status_code == 404
            # Capítulo inexistente
            r_inexistente = await http.patch(
                f"/roadmaps/{base['id']}", json={"ajustes": [{"num": 99, "peso": 1.5}]}
            )
            assert r_inexistente.status_code == 422 and "não existe" in r_inexistente.json()["detail"]
            # Capítulo duplicado na lista
            r_duplicado = await http.patch(
                f"/roadmaps/{base['id']}",
                json={"ajustes": [{"num": 1, "peso": 1.2}, {"num": 1, "peso": 1.5}]},
            )
            assert r_duplicado.status_code == 422
            # Corpo vazio sem nenhum campo informado
            assert (await http.patch(f"/roadmaps/{base['id']}", json={})).status_code == 422
            # Peso fora dos limites permitidos (0.5 a 2.0)
            assert (
                await http.patch(
                    f"/roadmaps/{base['id']}", json={"ajustes": [{"num": 1, "peso": 5.0}]}
                )
            ).status_code == 422
    finally:
        await apagar([book.id])


@pytest.mark.asyncio
async def test_fluxo_job_assincrono_com_polling_e_cancelamento(monkeypatch: pytest.MonkeyPatch, banco: None):
    async def classificar_fake(titulo: str, capitulos: list[Capitulo]) -> Classificacao:
        return CLS

    async def pesquisar_fake(session: object, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
        return Pesquisa(justificativa="sem fonte")

    monkeypatch.setattr(main, "classificar", classificar_fake)
    monkeypatch.setattr(main, "pesquisar", pesquisar_fake)

    book = novo_book("Livro Jobs", CAPS, paginas_conteudo=60)
    await criar(book)

    try:
        async with cliente() as http:
            # 1. Validações imediatas síncronas antes de criar job
            assert (await http.post("/books/999999999/roadmaps?modo=async", json=PEDIDO)).status_code == 404
            assert (
                await http.post(
                    f"/books/{book.id}/roadmaps?modo=async",
                    json={"senioridade": "Pleno", "data_limite": "2020-01-01"},
                )
            ).status_code == 422

            # 2. Criar roadmap assíncrono via query param (?modo=async) -> 202 Accepted
            r_async = await http.post(f"/books/{book.id}/roadmaps?modo=async", json=PEDIDO)
            assert r_async.status_code == 202
            loc = r_async.headers.get("Location")
            assert loc and loc.startswith("/jobs/")
            job_data = r_async.json()
            job_id = job_data["id"]
            assert job_data["book_id"] == book.id

            # 3. Polling em GET /jobs/{job_id} até conclusão
            concluido = False
            dados = {}
            for _ in range(30):
                r_job = await http.get(f"/jobs/{job_id}")
                assert r_job.status_code == 200
                dados = r_job.json()
                if dados["status"] == "concluido":
                    concluido = True
                    assert dados["roadmap_id"] is not None
                    assert dados["roadmap"]["total_horas"] > 0
                    break
                await asyncio.sleep(0.05)
            assert concluido, f"Job não concluiu a tempo: {dados}"

            # Verificar que o roadmap criado pelo job pode ser consultado normalmente
            r_rm = await http.get(f"/roadmaps/{dados['roadmap_id']}")
            assert r_rm.status_code == 200
            assert r_rm.json()["id"] == dados["roadmap_id"]

            # 4. Criar roadmap assíncrono via cabeçalho Prefer: respond-async (RFC 7240)
            r_pref = await http.post(
                f"/books/{book.id}/roadmaps",
                json=PEDIDO,
                headers={"Prefer": "respond-async"},
            )
            assert r_pref.status_code == 202
            job_pref_id = r_pref.json()["id"]

            concluido_pref = False
            for _ in range(30):
                r_job_pref = await http.get(f"/jobs/{job_pref_id}")
                if r_job_pref.json()["status"] == "concluido":
                    concluido_pref = True
                    break
                await asyncio.sleep(0.05)
            assert concluido_pref

            # 5. Cancelamento via DELETE /jobs/{job_id}
            from src.jobs import gerenciador_jobs
            job_cancel = gerenciador_jobs.criar_job(book.id)
            r_del = await http.delete(f"/jobs/{job_cancel.id}")
            assert r_del.status_code == 204
            assert (await http.get(f"/jobs/{job_cancel.id}")).status_code == 404

            # 6. Rotas de erro em jobs
            assert (await http.get("/jobs/nao-existe")).status_code == 404
            assert (await http.delete("/jobs/nao-existe")).status_code == 404
    finally:
        await apagar([book.id])
