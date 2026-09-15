"""Pesquisa de fatos: lógica pura (trechos, anti-alucinação, contagens divergentes). Sem web, sem LLM."""

from src.pesquisa import CONTAGEM, PaginasEncontradas, TrechoWeb, fonte_ignorada, trechos_de_texto, validar

EDITORA = "https://editora.exemplo/livros/llms"
LOJA = "https://loja.exemplo/produto/123"
OUTRO = "https://rede.exemplo/post/9"
FONTES = [
    TrechoWeb(url=EDITORA, texto="ISBN impresso: 978-65-83913-08-1 Ano: 2026 Páginas: 344 Preço impresso: R$ 139,00"),
    TrechoWeb(url=LOJA, texto="Número de páginas ‏ : ‎ 344 páginas ISBN-10 ‏ : ‎ 6583913089"),
    TrechoWeb(url=OUTRO, texto="Recomendado. Livro 488 páginas. LLMs: As Partes Difíceis e mais três lançamentos"),
]


def achado(**kw: object) -> PaginasEncontradas:
    base = {
        "mesmo_livro": True,
        "paginas_totais": 344,
        "url_fonte": EDITORA,
        "trechos_deste_livro": [1, 2, 3],
        "justificativa": "Ficha técnica da editora.",
    }
    return PaginasEncontradas.model_validate({**base, **kw})


def contagens(texto: str) -> list[int]:
    return [int(m[1] or m[2]) for m in CONTAGEM.finditer(texto)]


def test_trechos_guardam_so_a_ficha_em_volta_de_paginas():
    corpo = "Capítulo sobre avaliação. " * 40  # texto longo sem contagem de páginas: não entra na base
    html_txt = f"{corpo} Autor: Fulano ISBN: 978-65 Ano: 2026 Páginas: 344 Preço: R$ 139 {corpo} Página 1 de 1"
    trechos = trechos_de_texto(EDITORA, html_txt)
    assert len(trechos) == 1
    assert "Páginas: 344" in trechos[0].texto
    assert len(trechos[0].texto) < 300  # janela, não a página inteira


def test_contagem_le_formatos_reais_e_ignora_isbn():
    assert contagens(FONTES[1].texto) == [344]  # "páginas : 344 páginas" é uma contagem só; não lê "10" de ISBN-10
    assert contagens(FONTES[2].texto) == [488]
    assert not contagens("Página 1 de 1")


def test_ano_antes_do_rotulo_paginas_nao_e_contagem():
    # Formato real da ficha de editora: o ano vem colado antes do rótulo "Páginas:".
    assert contagens(FONTES[0].texto) == [344]


def test_aceita_numero_escrito_na_fonte_citada_e_lista_divergencias():
    p = validar(achado(), FONTES, n_capitulos=9)
    assert (p.paginas_totais, p.url_fonte) == (344, EDITORA)
    assert p.outras_contagens == ["488 páginas (rede.exemplo)"]
    assert p.fontes_consultadas == sorted([EDITORA, LOJA, OUTRO])


def test_contagem_de_outro_livro_nao_vira_aviso():
    # O modelo marcou só editora e loja como deste livro: o 488 do post (outro livro) não é divergência.
    p = validar(achado(trechos_deste_livro=[1, 2]), FONTES, n_capitulos=9)
    assert (p.paginas_totais, p.outras_contagens) == (344, [])


def test_rede_social_nunca_e_fonte():
    for url in ("https://www.instagram.com/reel/abc", "https://pt.linkedin.com/posts/x", "https://x.com/editora/status/1"):
        assert fonte_ignorada(url), url
    for url in ("https://novatec.com.br/livros/llms", "https://www.amazon.com.br/dp/123", "https://box.com/arquivo"):
        assert not fonte_ignorada(url), url


def test_pagina_de_ebook_nunca_e_fonte():
    # Caso real: a listagem Kindle do livro diz 561 páginas (de tela); a do impresso, 344.
    kindle = "https://www.amazon.com.br/LLMs-Partes-Dif%C3%ADceis-Solu%C3%A7%C3%B5es-armadilhas-ebook/dp/B0HCRGH8L2"
    impresso = "https://www.amazon.com.br/LLMs-Partes-Dif%C3%ADceis-Solu%C3%A7%C3%B5es-armadilhas/dp/6583913089"
    assert fonte_ignorada(kindle) and fonte_ignorada("https://loja.exemplo/kindle/livro-123")
    assert not fonte_ignorada(impresso)
    # A ficha da editora cita "ISBN ebook" ao lado das páginas do impresso: o filtro é na URL, não no texto.
    assert trechos_de_texto(EDITORA, "ISBN ebook: 978-65-83913-09-8 Ano: 2026 Páginas: 344")


def test_descarta_numero_que_nao_esta_na_fonte_citada():
    p = validar(achado(paginas_totais=350), FONTES, n_capitulos=9)  # modelo "inventou" 350
    assert p.paginas_totais is None and "não está no trecho" in p.justificativa


def test_descarta_numero_de_outra_fonte_que_nao_a_citada():
    p = validar(achado(paginas_totais=488, url_fonte=EDITORA), FONTES, n_capitulos=9)  # 488 existe, mas não na editora
    assert p.paginas_totais is None


def test_descarta_url_que_nao_foi_consultada():
    assert validar(achado(url_fonte="https://inventada.exemplo"), FONTES, n_capitulos=9).paginas_totais is None


def test_respeita_quando_o_modelo_diz_que_nao_e_o_mesmo_livro():
    p = validar(achado(mesmo_livro=False, justificativa="Os trechos são de outro livro."), FONTES, n_capitulos=9)
    assert p.paginas_totais is None and p.justificativa == "Os trechos são de outro livro."


def test_descarta_total_implausivel():
    fontes = [TrechoWeb(url=EDITORA, texto="Páginas: 12")]
    assert validar(achado(paginas_totais=12), fontes, n_capitulos=20).paginas_totais is None
