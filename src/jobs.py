"""Gerenciador de jobs assíncronos em memória (ADR 0005).

Permite criar roadmaps sem prender a conexão HTTP por minutos.
Não cria tabelas adicionais no PostgreSQL, respeitando a regra de 3 tabelas do projeto.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

StatusJob = Literal["pendente", "pesquisando", "classificando", "calculando", "concluido", "falhou"]


class Job(BaseModel):
    id: str
    book_id: int
    status: StatusJob = "pendente"
    etapa: str = "Aguardando início do processamento"
    erro: str | None = None
    roadmap_id: int | None = None
    roadmap: dict[str, Any] | None = None
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    atualizado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GerenciadorJobs:
    """Gerencia jobs em memória com expiração automática (TTL de 1 hora)."""

    def __init__(self, ttl_segundos: int = 3600) -> None:
        self.ttl = timedelta(seconds=ttl_segundos)
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def _limpar_expirados(self) -> None:
        agora = datetime.now(timezone.utc)
        expirados = [
            jid
            for jid, job in self._jobs.items()
            if agora - job.atualizado_em > self.ttl and job.status in ("concluido", "falhou")
        ]
        for jid in expirados:
            self._jobs.pop(jid, None)
            self._tasks.pop(jid, None)

    def criar_job(self, book_id: int) -> Job:
        self._limpar_expirados()
        job_id = uuid4().hex
        job = Job(id=job_id, book_id=book_id)
        self._jobs[job_id] = job
        return job

    def obter_job(self, job_id: str) -> Job | None:
        self._limpar_expirados()
        return self._jobs.get(job_id)

    def atualizar_job(
        self,
        job_id: str,
        status: StatusJob,
        etapa: str,
        erro: str | None = None,
        roadmap_id: int | None = None,
        roadmap: dict[str, Any] | None = None,
    ) -> Job | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.status = status
        job.etapa = etapa
        if erro is not None:
            job.erro = erro
        if roadmap_id is not None:
            job.roadmap_id = roadmap_id
        if roadmap is not None:
            job.roadmap = roadmap
        job.atualizado_em = datetime.now(timezone.utc)
        return job

    def iniciar_tarefa(
        self,
        job_id: str,
        coro_func: Callable[[str], Coroutine[Any, Any, None]],
    ) -> asyncio.Task[None]:
        async def _wrapper() -> None:
            try:
                await coro_func(job_id)
            except asyncio.CancelledError:
                self.atualizar_job(job_id, "falhou", "Cancelado pelo usuário", erro="Job cancelado")
                raise
            except Exception as exc:
                self.atualizar_job(job_id, "falhou", f"Falha no processamento: {exc}", erro=str(exc))

        task = asyncio.create_task(_wrapper())
        self._tasks[job_id] = task
        return task

    def cancelar_job(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        if job_id in self._jobs:
            self._jobs.pop(job_id, None)
            self._tasks.pop(job_id, None)
            return True
        return False


gerenciador_jobs = GerenciadorJobs()
