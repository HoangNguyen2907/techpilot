"""Track totals provenance, group trend months in UTC, track chunk embedding
state, and track the dictionary version used to tag each story.

Merged from former revisions 002, 003 and 004.

Revision ID: 20261117_002
Revises: 20261116_001
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261117_002"
down_revision: str | None = "20261116_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_trend_view(date_expression: str) -> None:
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW trend_monthly AS
        SELECT
            st.tech,
            st.category,
            {date_expression} AS month,
            COUNT(DISTINCT s.id)::integer AS story_count,
            COALESCE(SUM(s.points), 0)::integer AS total_points,
            COALESCE(SUM(s.num_comments), 0)::integer AS total_comments,
            ROUND(
                (COUNT(DISTINCT s.id)::numeric / NULLIF(COALESCE(tm.total_stories, 0), 0)::numeric) * 100,
                4
            ) AS pct_of_monthly_stories
        FROM story_tech st
        JOIN hn_stories s ON s.id = st.story_id
        LEFT JOIN hn_totals_monthly tm ON tm.month = {date_expression}
        GROUP BY st.tech, st.category, {date_expression}, tm.total_stories;

        CREATE UNIQUE INDEX uq_trend_monthly_tech_category_month
            ON trend_monthly (tech, category, month);
        CREATE INDEX ix_trend_monthly_month ON trend_monthly (month);
        CREATE INDEX ix_trend_monthly_category ON trend_monthly (category);
        """
    )
    op.execute("GRANT SELECT ON trend_monthly TO anon, authenticated")
    op.execute("GRANT ALL ON trend_monthly TO service_role")


def upgrade() -> None:
    # --- hn_totals_monthly provenance + UTC trend view (former 002) ---
    op.add_column(
        "hn_totals_monthly",
        sa.Column(
            "source",
            sa.String(length=20),
            server_default="bigquery",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_hn_totals_monthly_source",
        "hn_totals_monthly",
        "source IN ('bigquery', 'corpus')",
    )

    op.execute("DROP MATERIALIZED VIEW trend_monthly")
    _create_trend_view("DATE_TRUNC('month', timezone('UTC', s.created_at))::date")

    # --- hn_chunks embedding state (former 003) ---
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
        "ix_hn_chunks_pending_created_id",
        "hn_chunks",
        ["created_at", "id"],
        postgresql_where=sa.text("embedding IS NULL"),
    )

    # --- hn_stories dictionary version (former 004) ---
    op.add_column("hn_stories", sa.Column("tagged_dict_version", sa.String(64)))

    # Keep this LAST: autocommit_block commits the transaction above first,
    # then runs CREATE INDEX CONCURRENTLY outside of any transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_hn_stories_untagged "
            "ON hn_stories (id) WHERE tagged_dict_version IS NULL"
        )


def downgrade() -> None:
    # Reverse order: former 004 -> 003 -> 002.
    # Keep the CONCURRENTLY drop FIRST, for the same autocommit reason.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_hn_stories_untagged")
    op.drop_column("hn_stories", "tagged_dict_version")

    op.drop_index("ix_hn_chunks_pending_created_id", table_name="hn_chunks")
    op.drop_index("ix_hn_chunks_embedding_attempts", table_name="hn_chunks")
    op.drop_index("ix_hn_chunks_embedding_version", table_name="hn_chunks")
    op.drop_column("hn_chunks", "embedding_attempts")
    op.drop_column("hn_chunks", "embedding_error")
    op.drop_column("hn_chunks", "content_hash")
    op.drop_column("hn_chunks", "embedding_version")

    op.execute("DROP MATERIALIZED VIEW trend_monthly")
    _create_trend_view("DATE_TRUNC('month', s.created_at)::date")
    op.drop_constraint(
        "ck_hn_totals_monthly_source", "hn_totals_monthly", type_="check"
    )
    op.drop_column("hn_totals_monthly", "source")
