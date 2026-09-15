"""Golden set compartilhado pelos evals com LLM real."""

import os
import statistics
from pathlib import Path

from src.calculo import planejar
from src.classificador import Classificacao, RespostaLLMInvalida, classificar
from src.extracao import Extraido, extrair

RAIZ = Path(__file__).parents[2]
FIXTURES = RAIZ / "test_files"
# Arquivos fora do git (test_files/ é gitignored). Ausentes → os evals do golden set pulam.
GOLDEN = {
    "richards": "sumario-9788575229682.pdf",
    "geron": "AMOSTRA_MaosAObraAprendizadoDeMaquinaComScikit-LearnKerasTensorFlow.pdf",
    "huyen": "sumario-9788575229965.pdf",
    "projetando": "AMOSTRA_ProjetandoSistemasDeMachineLearning-1.pdf",
    "web_scraping": "sumario-9788575229231.pdf",
    "engenheiro": "sumario-9788575229989.pdf",
}
DISPONIBILIDADE = 6.0
N_VARIANCIA = int(os.environ.get("N_VARIANCIA", "10"))
LIVRO_VARIANCIA = "huyen"  # menor sumário do golden set (10 capítulos): as N chamadas custam menos
TOLERANCIA = 0.15

Resultado = tuple[Extraido, Classificacao | RespostaLLMInvalida]


def extrair_golden() -> dict[str, Extraido]:
    return {nome: extrair((FIXTURES / arq).read_bytes()) for nome, arq in GOLDEN.items() if (FIXTURES / arq).exists()}


async def classificar_golden(livros: dict[str, Extraido]) -> dict[str, Resultado]:
    resultados: dict[str, Resultado] = {}
    for nome, ext in livros.items():
        try:
            resultados[nome] = (ext, await classificar(ext.titulo, ext.capitulos))
        except RespostaLLMInvalida as exc:
            resultados[nome] = (ext, exc)
    return resultados


def validos(golden: dict[str, Resultado]) -> dict[str, tuple[Extraido, Classificacao]]:
    return {n: (e, c) for n, (e, c) in golden.items() if isinstance(c, Classificacao)}


def cv_horas(ext: Extraido, classificacoes: list[Classificacao]) -> float:
    """Coeficiente de variação das horas (Pleno) entre classificações repetidas do mesmo livro."""
    horas = [planejar(ext.capitulos, c, "Pleno", DISPONIBILIDADE).total_horas for c in classificacoes]
    return statistics.pstdev(horas) / statistics.mean(horas)
