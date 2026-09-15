"""Pesquisa de fatos sobre o livro na web + base RAG (spec 2.9 e 4, ADR 0004).

O Python busca (SearXNG), baixa as páginas e guarda só os trechos em volta de "páginas" no pgvector.
O modelo escolhe qual trecho é deste livro e transcreve o número. O Python só aceita o número se ele
estiver escrito no trecho da fonte citada — o modelo nunca calcula nem estima.
"""

import asyncio
import re
from urllib.parse import urlparse

import httpx
import trafilatura
from ollama import AsyncClient, ResponseError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classificador import LLMIndisponivel, RespostaLLMInvalida, chat
from src.config import config, settings
from src.extracao import Capitulo
from src.models import Book, Chunk

cfg = config.pesquisa
DIMENSOES = 768  # Chunk.embedding = Vector(768)
PALAVRA_PAGINAS = re.compile(r"p[áa]ginas?|pages?", re.I)
# "344 páginas", "Páginas: 344", "Número de páginas ‏ : ‎ 344" (Amazon põe marcas de direção invisíveis).
# Os limites de dígito impedem ler "10" de "ISBN-10" ou pedaços de um ISBN. Número seguido de
# "Páginas:" é o campo anterior da ficha ("Ano: 2026 Páginas: 344"), não uma contagem.
CONTAGEM = re.compile(
    r"(?<!\d)(\d{2,4})\s*(?:p[áa]ginas|pages)\b(?!\s*[:\u200e\u200f])"
    r"|\b(?:p[áa]ginas|pages)[\s:\u200e\u200f]*(\d{2,4})(?!\d)",
    re.I,
)
NAVEGADOR = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


class PesquisaIndisponivel(Exception):
    """Vira 503 quando o roadmap depende da pesquisa (sumário sem páginas)."""


class TrechoWeb(BaseModel):
    url: str
    texto: str


class PaginasEncontradas(BaseModel):
    """Saída do modelo: escolha e transcrição, nunca conta."""

    mesmo_livro: bool
    paginas_totais: int | None = Field(default=None, gt=0)
    url_fonte: str | None = None
    justificativa: str = Field(min_length=1)


class Pesquisa(BaseModel):
    """Persistida em book.pesquisa."""

    paginas_totais: int | None
    url_fonte: str | None
    justificativa: str
    fontes_consultadas: list[str]
    outras_contagens: list[str] = []


PROMPT_PESQUISA = """Você confere fatos sobre livros a partir de trechos de páginas web. Recebe o \
título e o início do sumário de um livro, e trechos numerados, cada um com a URL de origem. Responda:
- mesmo_livro: true só se algum trecho descreve claramente ESTE livro. Cuidado com livros diferentes \
de título parecido, outras edições e o original em outro idioma.
- paginas_totais: o número de páginas deste livro exatamente como está escrito no trecho; null se \
nenhum trecho sobre este livro traz o número.
- url_fonte: a URL do trecho de onde o número saiu.
- justificativa: uma frase dizendo por que esse trecho é deste livro.
Prefira a página da editora e de livrarias. Não calcule nem estime: só transcreva."""


def trechos_de_texto(url: str, texto: str) -> list[TrechoWeb]:
    """Janelas em volta de "páginas" que trazem uma contagem. É tudo o que vai para a base: ficha técnica, nunca corpo."""
    texto = re.sub(r"\s+", " ", texto)
    janelas = []
    for m in PALAVRA_PAGINAS.finditer(texto):
        janela = texto[max(0, m.start() - cfg.janela_antes) : m.end() + cfg.janela_depois].strip()
        if CONTAGEM.search(janela) and janela not in janelas:
            janelas.append(janela)
    return [TrechoWeb(url=url, texto=j) for j in janelas]


def validar(achado: PaginasEncontradas, fontes: list[TrechoWeb], n_capitulos: int) -> Pesquisa:
    """Anti-alucinação: número só vale se está escrito no trecho da URL que o modelo citou."""
    consultadas = sorted({f.url for f in fontes})
    n = achado.paginas_totais
    if not achado.mesmo_livro or n is None:
        return Pesquisa(paginas_totais=None, url_fonte=None, justificativa=achado.justificativa, fontes_consultadas=consultadas)

    citados = [f for f in fontes if f.url == achado.url_fonte]
    if not any(re.search(rf"(?<!\d){n}(?!\d)", f.texto) for f in citados):
        return Pesquisa(
            paginas_totais=None,
            url_fonte=None,
            justificativa=f"O modelo citou {n} páginas, mas o número não está no trecho da fonte citada: descartado.",
            fontes_consultadas=consultadas,
        )
    if n < n_capitulos:
        return Pesquisa(
            paginas_totais=None,
            url_fonte=None,
            justificativa=f"{n} páginas para {n_capitulos} capítulos não é plausível: descartado.",
            fontes_consultadas=consultadas,
        )

    outras = sorted(
        {f"{v} páginas ({urlparse(f.url).netloc})" for f in fontes for m in CONTAGEM.finditer(f.texto) if (v := int(m[1] or m[2])) != n}
    )
    return Pesquisa(
        paginas_totais=n,
        url_fonte=achado.url_fonte,
        justificativa=achado.justificativa,
        fontes_consultadas=consultadas,
        outras_contagens=outras,
    )


async def buscar_web(titulo: str) -> list[dict[str, str]]:
    consulta = f"{re.sub(r'[^\w\s]', ' ', titulo)} livro número de páginas"
    try:
        async with httpx.AsyncClient(timeout=cfg.timeout_s) as http:
            resposta = await http.get(
                f"{settings.searxng_url}/search", params={"q": consulta, "format": "json", "language": "pt-BR"}
            )
            resposta.raise_for_status()
    except httpx.HTTPError as exc:
        raise PesquisaIndisponivel(f"SearXNG inacessível em {settings.searxng_url}: {exc}") from exc
    resultados = resposta.json().get("results", [])[: cfg.resultados_web]
    return [{"url": r["url"], "content": r.get("content") or ""} for r in resultados]


async def baixar_trechos(http: httpx.AsyncClient, url: str) -> list[TrechoWeb]:
    try:
        resposta = await http.get(url, headers=NAVEGADOR, follow_redirects=True)
        resposta.raise_for_status()
    except httpx.HTTPError:
        return []  # site fora do ar ou bloqueando robôs: segue com as outras fontes
    # html2txt e não extract(): o "conteúdo principal" descarta a ficha técnica da editora.
    return trechos_de_texto(url, trafilatura.html2txt(resposta.text) or "")


async def embeddar(textos: list[str], prefixo: str) -> list[list[float]]:
    if not config.llm.modelo_embedding:
        raise LLMIndisponivel("Modelo de embedding não configurado (config.yaml: llm.modelo_embedding).")
    cliente = AsyncClient(host=settings.ollama_host, timeout=config.llm.timeout_s)
    try:
        resposta = await cliente.embed(model=config.llm.modelo_embedding, input=[prefixo + t for t in textos])
    except (ResponseError, httpx.HTTPError, ConnectionError) as exc:
        raise LLMIndisponivel(f"Falha ao gerar embeddings no Ollama: {exc}") from exc
    vetores = [list(v) for v in resposta.embeddings]
    if any(len(v) != DIMENSOES for v in vetores):
        raise RespostaLLMInvalida(f"Embedding com {len(vetores[0])} dimensões; o schema espera {DIMENSOES}.")
    return vetores


async def pesquisar(session: AsyncSession, book: Book, capitulos: list[Capitulo]) -> Pesquisa:
    """Não faz commit. A base RAG do livro é reaproveitada: a web só é consultada na primeira vez."""
    if not await session.scalar(select(Chunk.id).where(Chunk.book_id == book.id).limit(1)):
        resultados = await buscar_web(book.titulo)
        trechos = [t for r in resultados for t in trechos_de_texto(r["url"], r["content"])]
        async with httpx.AsyncClient(timeout=cfg.timeout_s) as http:
            baixados = await asyncio.gather(*(baixar_trechos(http, r["url"]) for r in resultados[: cfg.paginas_baixadas]))
        trechos += [t for lista in baixados for t in lista]
        if trechos:
            vetores = await embeddar([t.texto for t in trechos], cfg.prefixo_documento)
            session.add_all(Chunk(book_id=book.id, texto=t.texto, fonte=t.url, embedding=v) for t, v in zip(trechos, vetores))
            await session.flush()

    [consulta] = await embeddar([f"número de páginas do livro {book.titulo}"], cfg.prefixo_consulta)
    linhas = await session.execute(
        select(Chunk.fonte, Chunk.texto)
        .where(Chunk.book_id == book.id)
        .order_by(Chunk.embedding.cosine_distance(consulta))
        .limit(cfg.trechos_consulta)
    )
    fontes = [TrechoWeb(url=url, texto=texto) for url, texto in linhas]
    if not fontes:
        return Pesquisa(
            paginas_totais=None,
            url_fonte=None,
            justificativa="Nenhum trecho com número de páginas encontrado na web.",
            fontes_consultadas=[],
        )

    inicio_sumario = "\n".join(f"{c.num}. {c.titulo}" for c in capitulos[:8])
    lista = "\n\n".join(f"[{i}] {f.url}\n{f.texto}" for i, f in enumerate(fontes, 1))
    messages: list[dict[str, object]] = [
        {"role": "system", "content": PROMPT_PESQUISA},
        {"role": "user", "content": f"Título: {book.titulo}\n\nInício do sumário:\n{inicio_sumario}\n\nTrechos:\n{lista}"},
    ]
    achado = await chat(config.llm.modelo_classificador, "de pesquisa", messages, PaginasEncontradas)
    return validar(achado, fontes, len(capitulos))
