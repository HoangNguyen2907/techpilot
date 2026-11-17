"""Track monthly total provenance and group trend months in UTC.

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


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW trend_monthly")
    _create_trend_view("DATE_TRUNC('month', s.created_at)::date")
    op.drop_constraint(
        "ck_hn_totals_monthly_source", "hn_totals_monthly", type_="check"
    )
    op.drop_column("hn_totals_monthly", "source")
