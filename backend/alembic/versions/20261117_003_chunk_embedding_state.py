"""Track chunk content and embedding state for safe incremental embedding.

Revision ID: 20261117_003
Revises: 20261117_002
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261117_003"
down_revision: str | None = "20261117_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column(
        "hn_chunks", sa.Column("embedding_version", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "hn_chunks", sa.Column("content_hash", sa.String(length=64), nullable=True)
    )
    op.add_column("hn_chunks", sa.Column("embedding_error", sa.Text(), nullable=True))
    op.add_column(
        "hn_chunks",
        sa.Column(
            "embedding_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE hn_chunks
        SET content_hash = encode(digest(convert_to(content, 'UTF8'), 'sha256'), 'hex'),
            embedding_version = CASE
                WHEN metadata_json #>> '{embedding,model}' IS NOT NULL
                 AND metadata_json #>> '{embedding,dimensions}' IS NOT NULL
                 AND metadata_json #>> '{embedding,input_version}' IS NOT NULL
                THEN encode(
                    digest(
                        convert_to(
                            (metadata_json #>> '{embedding,model}') || '|' ||
                            (metadata_json #>> '{embedding,dimensions}') || '|' ||
                            (metadata_json #>> '{embedding,input_version}'),
                            'UTF8'
                        ),
                        'sha256'
                    ),
                    'hex'
                )
                ELSE NULL
            END
        """
    )
    op.alter_column("hn_chunks", "content_hash", nullable=False)
    op.create_index(
        "ix_hn_chunks_embedding_version", "hn_chunks", ["embedding_version"]
    )
    op.create_index(
        "ix_hn_chunks_embedding_attempts", "hn_chunks", ["embedding_attempts"]
    )
    op.create_index(
        "ix_hn_chunks_pending_created_id",
        "hn_chunks",
        ["created_at", "id"],
        postgresql_where=sa.text("embedding IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_hn_chunks_pending_created_id", table_name="hn_chunks")
    op.drop_index("ix_hn_chunks_embedding_attempts", table_name="hn_chunks")
    op.drop_index("ix_hn_chunks_embedding_version", table_name="hn_chunks")
    op.drop_column("hn_chunks", "embedding_attempts")
    op.drop_column("hn_chunks", "embedding_error")
    op.drop_column("hn_chunks", "content_hash")
    op.drop_column("hn_chunks", "embedding_version")
