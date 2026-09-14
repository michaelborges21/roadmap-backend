"""Calculadora e cronograma (spec 2.7–2.8). Python puro e determinístico."""

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


class Fatores(BaseModel):
    senioridade: float
    gap: float
    baixo_nivel: float
    peso: float


class HorasCapitulo(BaseModel):
    num: int
    titulo: str
    nivel: Nivel
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


class Plano(BaseModel):
    senioridade: Senioridade
    disponibilidade_horas: float
    total_horas: float
    semanas: int
    dias_por_semana: int
    capitulos: list[HorasCapitulo]
    excluidos: list[Excluido]
    cronograma: list[Semana]


def calcular(cap: Capitulo, cls: Classificacao, senioridade: Senioridade) -> HorasCapitulo:
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
