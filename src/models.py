from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "book"
    id: Mapped[int] = mapped_column(primary_key=True)
    hash_fonte: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    titulo: Mapped[str]
    origem: Mapped[str]  # "sumario" | "capa" | "ambos"
    paginas_conteudo: Mapped[int | None]  # 1º capítulo → 1º marcador final
    paginas_fisicas: Mapped[int | None]  # da ficha CIP, quando houver
    capitulos: Mapped[list] = mapped_column(JSONB, default=list)
    classificacao: Mapped[dict | None] = mapped_column(JSONB)


class Roadmap(Base):
    __tablename__ = "roadmap"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"))
    senioridade: Mapped[str]
    disponibilidade_horas: Mapped[float]
    plano: Mapped[dict] = mapped_column(JSONB)  # cronograma + fatores aplicados
    criado_em: Mapped[datetime] = mapped_column(server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"))
    texto: Mapped[str]  # título do cap + subtópicos
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
