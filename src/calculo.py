"""Calculadora e cronograma (spec 2.7–2.9). Python puro e determinístico."""

from math import ceil
from typing import Literal

from pydantic import BaseModel

from src.classificador import Classificacao, Nivel
from src.config import config
from src.extracao import Capitulo

Senioridade = Literal["Junior", "Pleno", "Senior"]
NIVEIS: list[Nivel] = ["iniciante", "intermediario", "avancado"]
SENIORIDADES: list[Senioridade] = ["Junior", "Pleno", "Senior"]
EPS = 1e-9

calc = config.calculo

REGRA_DISTRIBUICAO = (
    f"Sumário sem paginação: {calc.fracao_conteudo_pesquisa:.0%} do total de páginas pesquisado "
    "(o resto é prefácio, apêndice e índice) foram repartidos entre os capítulos na proporção de "
    "1 + nº de subtópicos. As páginas por capítulo são estimativa."
)


class Fatores(BaseModel):
    senioridade: float
    gap: float
    baixo_nivel: float
    peso: float


class HorasCapitulo(BaseModel):
    num: int
    titulo: str
    nivel: Nivel
    paginas: int | None = None  # None em roadmaps criados antes deste campo
    leitura: float
    codigo: float
    escrita: float
    total: float
    fatores: Fatores


class Excluido(BaseModel):
    num: int
    titulo: str
    nivel: Nivel


class Trecho(BaseModel):
    num: int
    horas: float


class Semana(BaseModel):
    semana: int
    horas: float = 0.0
    capitulos: list[Trecho] = []


class OrigemPaginas(BaseModel):
    origem: Literal["sumario", "pesquisa"]
    paginas_totais: int | None = None  # "pesquisa": total do livro achado na web
    paginas_conteudo: int | None = None  # "pesquisa": parte do total repartida entre os capítulos
    url_fonte: str | None = None
    regra: str | None = None


class Plano(BaseModel):
    senioridade: Senioridade
    disponibilidade_horas: float
    total_horas: float
    semanas: int
    dias_por_semana: int
    capitulos: list[HorasCapitulo]
    excluidos: list[Excluido]
    cronograma: list[Semana]
    paginas: OrigemPaginas | None = None  # None em roadmaps criados antes da pesquisa de fatos
    avisos: list[str] = []


def paginas_de_conteudo(paginas_totais: int) -> int:
    """O total achado na web inclui prefácio, apêndice e índice; conteúdo = fração calibrada dele."""
    return round(paginas_totais * calc.fracao_conteudo_pesquisa)


def distribuir_paginas(capitulos: list[Capitulo], paginas: int) -> list[Capitulo]:
    """Sumário sem paginação (spec 2.9): reparte as páginas de conteúdo na proporção de 1 + nº de subtópicos."""
    pesos = [1 + len(c.subtopicos) for c in capitulos]
    brutos = [paginas * p / sum(pesos) for p in pesos]
    inteiros = [int(b) for b in brutos]
    # Maior resto: a soma dos capítulos fecha exatamente no total.
    maiores_restos = sorted(range(len(brutos)), key=lambda i: brutos[i] - inteiros[i], reverse=True)
    for i in maiores_restos[: paginas - sum(inteiros)]:
        inteiros[i] += 1
    return [c.model_copy(update={"paginas": max(1, n)}) for c, n in zip(capitulos, inteiros)]


def calcular(cap: Capitulo, cls: Classificacao, senioridade: Senioridade) -> HorasCapitulo:
    if cap.paginas is None:
        raise ValueError(f"Capítulo {cap.num} sem páginas: distribua o total pesquisado antes de calcular.")
    julgado = next(j for j in cls.capitulos if j.num == cap.num)
    gap = calc.fator_gap if NIVEIS.index(cls.nivel_natural) > SENIORIDADES.index(senioridade) else 1.0
    baixo = calc.fator_baixo_nivel if calc.baixo_nivel & {l.lower() for l in cls.linguagens} else 1.0
    fator = calc.f_senior[senioridade] * gap * baixo * julgado.peso

    leitura = cap.paginas * calc.min_pagina[cls.densidade] / 60 * fator
    codigo = leitura * calc.ratio_codigo[cls.tipo_livro]
    escrita = leitura * calc.ratio_escrita
    return HorasCapitulo(
        num=cap.num,
        titulo=cap.titulo,
        nivel=julgado.nivel,
        paginas=cap.paginas,
        leitura=leitura,
        codigo=codigo,
        escrita=escrita,
        total=leitura + codigo + escrita,
        fatores=Fatores(senioridade=calc.f_senior[senioridade], gap=gap, baixo_nivel=baixo, peso=julgado.peso),
    )


def planejar(capitulos: list[Capitulo], cls: Classificacao, senioridade: Senioridade, disponibilidade: float) -> Plano:
    nivel = {j.num: j.nivel for j in cls.capitulos}
    # Senioridade filtra: capítulo com conteúdo abaixo do nível do leitor sai do cronograma.
    incluidos = [cap for cap in capitulos if NIVEIS.index(nivel[cap.num]) >= SENIORIDADES.index(senioridade)]
    horas = [calcular(cap, cls, senioridade) for cap in incluidos]

    # Enche as semanas em ordem; capítulo que não cabe continua na semana seguinte (sempre adjacente).
    cronograma: list[Semana] = []
    livre = 0.0
    for h in horas:
        resto = h.total
        while resto > EPS:
            if livre <= EPS:
                cronograma.append(Semana(semana=len(cronograma) + 1))
                livre = disponibilidade
            parte = min(resto, livre)
            cronograma[-1].capitulos.append(Trecho(num=h.num, horas=parte))
            cronograma[-1].horas += parte
            resto -= parte
            livre -= parte

    return Plano(
        senioridade=senioridade,
        disponibilidade_horas=disponibilidade,
        total_horas=sum(h.total for h in horas),
        semanas=len(cronograma),
        dias_por_semana=min(7, ceil(disponibilidade / calc.horas_por_sessao)),
        capitulos=horas,
        excluidos=[Excluido(num=c.num, titulo=c.titulo, nivel=nivel[c.num]) for c in capitulos if c not in incluidos],
        cronograma=cronograma,
    )
