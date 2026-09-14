"""Pipeline de extração determinística do sumário (spec 2.1–2.5). Sem LLM."""

import io
import re
from collections import Counter
from typing import Literal

import pdfplumber
from pdfplumber.page import Page
from pydantic import BaseModel

from src.config import config

cfg = config.extracao


class ExtracaoAmbigua(Exception):
    """Vira 422: preferimos falhar a devolver número errado."""


class Capitulo(BaseModel):
    num: int
    titulo: str
    pag_inicio: int
    paginas: int
    subtopicos: list[str] = []


class Extraido(BaseModel):
    titulo: str
    origem: Literal["sumario", "capa"]
    paginas_conteudo: int | None
    paginas_fisicas: int | None
    capitulos: list[Capitulo]


Entrada = dict[str, str | int | None]

ANCORA = re.compile(r"^(Sum[áa]rio|Conte[úu]do|[ÍI]ndice|Table of Contents)$", re.I)
ROMANO = r"[ivxlcdm]+"
ENTRADA = re.compile(rf"^(.+?)\s+(\d+|{ROMANO})$")
# Novatec usa "Capítulo N:", "Capítulo N ■" e "Capítulo N" sem separador; Alta Books usa "N." ou "N .".
CAPITULO = [
    re.compile(r"^Cap[ií]tulo\s+(\d+)\s*[:.■]?\s*(.+?)\s+(\d+)$", re.I),
    re.compile(r"^(\d+)\s?\.\s+(.+?)\s+(\d+)$"),
]
MARCADOR = re.compile(r"^(Parte\b|Pref[áa]cio\b|Ap[êe]ndice|[A-Z]\.\s|[ÍI]ndice\b)", re.I)
CIP_PAGINAS = re.compile(r"\b(\d+)\s*p\.")
CIP_AUTOR = re.compile(r"[A-ZÀ-Ý][\wÀ-ÿ'-]+, [A-ZÀ-Ý][\wÀ-ÿ. ]*$")


def extrair(dados: bytes) -> Extraido:
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        paginas = [sem_marca_dagua(p) for p in pdf.pages]
        n_paginas = len(paginas)
        linhas_brutas = [[normalizar(l) for l in (p.extract_text() or "").splitlines()] for p in paginas]
        titulo_fonte = maior_fonte(paginas[0]) if paginas else None
    linhas = [l for pag in limpar_ruido([[l for l in pag if l] for pag in linhas_brutas]) for l in pag]

    loc = localizar_sumario(linhas)
    if loc is None:
        corridas = sum(1 for l in linhas if len(l) >= cfg.paragrafo_min_chars and not ENTRADA.match(l))
        if corridas >= cfg.paragrafos_min:
            raise ExtracaoAmbigua("Documento sem sumário e com texto corrido: parece corpo de livro, não sumário nem capa.")
        titulo = titulo_cip(linhas) or titulo_fonte
        if not titulo:
            raise ExtracaoAmbigua("Capa sem título legível na camada de texto do PDF.")
        return Extraido(titulo=titulo, origem="capa", paginas_conteudo=None, paginas_fisicas=None, capitulos=[])

    inicio, janela = loc
    capitulos, conteudo = fechar_paginas(classificar_linhas(janela))
    antes = linhas[:inicio]
    fisicas = next((int(m[1]) for l in antes if (m := CIP_PAGINAS.search(l))), None)
    if fisicas is not None and conteudo > fisicas:
        raise ExtracaoAmbigua(f"Sumário soma {conteudo} páginas de conteúdo, mas a ficha CIP diz {fisicas}.")
    if n_paginas >= cfg.livro_completo_fracao * conteudo:
        raise ExtracaoAmbigua(
            f"PDF com {n_paginas} páginas para um livro de {conteudo}: parece o livro completo. Envie só o sumário."
        )
    titulo = titulo_cip(antes) or titulo_fonte
    if not titulo:
        raise ExtracaoAmbigua("Sumário sem título legível (sem ficha CIP e sem texto na capa).")
    return Extraido(
        titulo=titulo, origem="sumario", paginas_conteudo=conteudo, paginas_fisicas=fisicas, capitulos=capitulos
    )


def sem_marca_dagua(pagina: Page) -> Page:
    return pagina.filter(lambda o: o.get("object_type") != "char" or o["size"] < cfg.watermark_min_size)


def normalizar(linha: str) -> str:
    # Fontes sem mapa unicode viram U+FFFD: tanto o ponto de "1." quanto o pontilhado.
    linha = re.sub(r"(\s*\.){2,}\s*", " ", linha.replace("�", "."))
    return re.sub(r"\s+", " ", linha).strip()


def maior_fonte(pagina: Page) -> str | None:
    linhas = pagina.extract_text_lines(return_chars=True)
    if not linhas:
        return None
    tamanho = lambda ln: round(max(c["size"] for c in ln["chars"]))  # noqa: E731
    maior = max(map(tamanho, linhas))
    return normalizar(" ".join(ln["text"] for ln in linhas if tamanho(ln) == maior)) or None


def titulo_cip(linhas: list[str]) -> str | None:
    """Ficha CIP: título fica entre a linha 'Sobrenome, Nome' e o ' / ' que antecede a autoria."""
    ini = next((i for i, l in enumerate(linhas) if "Catalogação na Publicação" in l), None)
    if ini is None:
        return None
    bloco = linhas[ini : ini + 15]
    fim = next((i for i, l in enumerate(bloco) if " / " in l), None)
    autor = max((i for i in range(fim) if CIP_AUTOR.search(bloco[i])), default=None) if fim else None
    if autor is None:
        return None
    texto = re.sub(r"(\w)- (\w)", r"\1-\2", " ".join(bloco[autor + 1 : fim + 1]))  # type: ignore[operator]
    return texto.split(" / ")[0].split(" : ")[0].strip() or None


def limpar_ruido(paginas: list[list[str]]) -> list[list[str]]:
    """Cabeçalho/rodapé: linha de borda repetida em várias páginas, variando só números."""
    chave = lambda l: re.sub(rf"\d+|\b{ROMANO}\b", "#", l)  # noqa: E731
    borda = lambda pag: pag[: cfg.ruido_linhas_borda] + pag[-cfg.ruido_linhas_borda :]  # noqa: E731
    contagem = Counter(k for pag in paginas for k in {chave(l) for l in borda(pag)})
    ruido = {k for k, n in contagem.items() if n >= cfg.ruido_min_paginas}
    return [[l for l in pag if not (l in borda(pag) and chave(l) in ruido)] for pag in paginas]


def localizar_sumario(linhas: list[str]) -> tuple[int, list[str]] | None:
    """Índice da âncora e linhas da janela; None se não há âncora."""
    inicio = next((i for i, l in enumerate(linhas) if ANCORA.match(l)), None)
    if inicio is None:
        return None
    janela: list[str] = []
    sem_pagina = 0
    for linha in linhas[inicio + 1 :]:
        if ENTRADA.match(linha):
            janela.append(linha)
            sem_pagina = 0
        elif (sem_pagina := sem_pagina + 1) >= cfg.linhas_sem_pagina_fim:
            break
    return inicio, janela


def classificar_linhas(janela: list[str]) -> list[Entrada]:
    """Cada entrada: tipo (capitulo|marcador|subtopico), num, titulo, pag (None se romana)."""
    entradas: list[Entrada] = []
    for linha in janela:
        texto, pag = ENTRADA.match(linha).groups()  # type: ignore[union-attr]
        if not pag.isdigit():
            entradas.append({"tipo": "marcador", "num": None, "titulo": texto, "pag": None})
        elif cap := next((m for rx in CAPITULO if (m := rx.match(linha))), None):
            entradas.append({"tipo": "capitulo", "num": int(cap[1]), "titulo": cap[2], "pag": int(cap[3])})
        elif MARCADOR.match(texto):
            entradas.append({"tipo": "marcador", "num": None, "titulo": texto, "pag": int(pag)})
        else:
            entradas.append({"tipo": "subtopico", "num": None, "titulo": texto, "pag": int(pag)})
    return entradas


def fechar_paginas(entradas: list[Entrada]) -> tuple[list[Capitulo], int]:
    """paginas[n] = pag_inicio(próxima fronteira) - pag_inicio[n]; fronteira = capítulo ou marcador."""
    fronteiras = [e for e in entradas if e["tipo"] != "subtopico" and e["pag"] is not None]
    pags = [int(e["pag"]) for e in fronteiras]  # type: ignore[arg-type]
    if any(b < a for a, b in zip(pags, pags[1:])):
        raise ExtracaoAmbigua("Sumário com páginas em sequência não crescente.")

    capitulos: list[Capitulo] = []
    fim = 0
    for i, e in enumerate(fronteiras):
        if e["tipo"] != "capitulo":
            continue
        if i + 1 == len(fronteiras):
            raise ExtracaoAmbigua(f"Capítulo {e['num']} sem fronteira final (falta apêndice ou índice após ele).")
        if (paginas := pags[i + 1] - pags[i]) <= 0:
            raise ExtracaoAmbigua(f"Capítulo {e['num']} com {paginas} páginas.")
        capitulos.append(Capitulo(num=int(e["num"]), titulo=str(e["titulo"]), pag_inicio=pags[i], paginas=paginas))  # type: ignore[arg-type]
        fim = pags[i + 1]

    if not capitulos:
        raise ExtracaoAmbigua("Sumário encontrado, mas nenhum capítulo reconhecido.")
    if [c.num for c in capitulos] != list(range(capitulos[0].num, capitulos[0].num + len(capitulos))):
        raise ExtracaoAmbigua("Numeração de capítulos com lacunas ou repetições.")

    # Subtópico anexa ao capítulo corrente; antes do 1º capítulo ou após marcador é descartado.
    por_num = {c.num: c for c in capitulos}
    corrente: Capitulo | None = None
    for e in entradas:
        if e["tipo"] == "capitulo":
            corrente = por_num[int(e["num"])]  # type: ignore[arg-type]
        elif e["tipo"] == "marcador":
            corrente = None
        elif corrente:
            corrente.subtopicos.append(str(e["titulo"]))

    return capitulos, fim - capitulos[0].pag_inicio
