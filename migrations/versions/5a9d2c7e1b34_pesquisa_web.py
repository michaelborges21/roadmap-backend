"""pesquisa na web: book.pesquisa e chunk.fonte

Revision ID: 5a9d2c7e1b34
Revises: 23e474bf439a
Create Date: 2026-09-15 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5a9d2c7e1b34'
down_revision: Union[str, Sequence[str], None] = '23e474bf439a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('book', sa.Column('pesquisa', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # Os chunks antigos eram capítulos de sumário do RAG de referências (não aceito, ADR 0003).
    # O RAG novo guarda trechos da web com a URL de origem (ADR 0004).
    op.execute("DELETE FROM chunk")
    op.add_column('chunk', sa.Column('fonte', sa.String(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('chunk', 'fonte')
    op.drop_column('book', 'pesquisa')
