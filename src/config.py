from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str = "postgresql+asyncpg://roadmap:roadmap@localhost:5433/roadmap"
    ollama_host: str = "http://localhost:11435"
    searxng_url: str = "http://localhost:8888"


class Extracao(BaseModel):
    watermark_min_size: float
    linhas_sem_pagina_fim: int
    livro_completo_fracao: float
    ruido_min_paginas: int
    ruido_linhas_borda: int
    paragrafo_min_chars: int
    paragrafos_min: int


class Calculo(BaseModel):
    min_pagina: dict[Literal["leve", "media", "densa"], float]
    f_senior: dict[Literal["Junior", "Pleno", "Senior"], float]
    ratio_codigo: dict[Literal["teorico", "hibrido", "pratico"], float]
    ratio_escrita: float
    fator_gap: float
    fator_baixo_nivel: float
    baixo_nivel: set[str]
    horas_por_sessao: float
    fracao_conteudo_pesquisa: float


class LLM(BaseModel):
    modelo_classificador: str | None
    modelo_visao: str | None
    modelo_embedding: str | None
    temperature: float
    num_ctx: int
    num_predict: int
    timeout_s: float


class PesquisaWeb(BaseModel):
    resultados_web: int
    paginas_baixadas: int
    timeout_s: float
    trechos_consulta: int
    janela_antes: int
    janela_depois: int
    prefixo_documento: str
    prefixo_consulta: str


class Config(BaseModel):
    extracao: Extracao
    calculo: Calculo
    llm: LLM
    pesquisa: PesquisaWeb


settings = Settings()
config = Config.model_validate(yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text()))
