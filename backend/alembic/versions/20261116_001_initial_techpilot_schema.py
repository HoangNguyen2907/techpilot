"""Initial TechPilot schema with vector, full-text, trends materialized view, and chat tables.

Revision ID: 20261116_001
Revises: None
Create Date: 2026-11-16 17:55:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261116_001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 2. Users table (mapped to auth.users.id)
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # 3. Chat threads table
    op.create_table(
        "chat_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "title", sa.String(length=255), nullable=False, server_default="New Chat"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_chat_threads_user_id", "chat_threads", ["user_id"])

    # 4. Chat messages table
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("parts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_chat_messages_thread_id", "chat_messages", ["thread_id"])

    # 5. HN Stories table
    op.create_table(
        "hn_stories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("num_comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="story"),
    )
    op.create_index("ix_hn_stories_points", "hn_stories", ["points"])
    op.create_index("ix_hn_stories_created_at", "hn_stories", ["created_at"])

    # 6. HN Comments table
    op.create_table(
        "hn_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "story_id",
            sa.BigInteger(),
            sa.ForeignKey("hn_stories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("n_children", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hn_comments_story_id", "hn_comments", ["story_id"])
    op.create_index("ix_hn_comments_parent_id", "hn_comments", ["parent_id"])
    op.create_index("ix_hn_comments_quality_score", "hn_comments", ["quality_score"])

    # 7. Story Tech mapping table
    op.create_table(
        "story_tech",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "story_id",
            sa.BigInteger(),
            sa.ForeignKey("hn_stories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tech", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("in_title", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("via", sa.String(length=50), nullable=False, server_default="direct"),
        sa.UniqueConstraint("story_id", "tech", name="uq_story_tech"),
    )
    op.create_index("ix_story_tech_story_id", "story_tech", ["story_id"])
    op.create_index("ix_story_tech_tech", "story_tech", ["tech"])
    op.create_index("ix_story_tech_category", "story_tech", ["category"])
    op.create_index("ix_story_tech_tech_category", "story_tech", ["tech", "category"])

    # 8. Monthly Totals table
    op.create_table(
        "hn_totals_monthly",
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("total_stories", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_comments", sa.Integer(), nullable=False, server_default="0"),
    )

    # 9. HN Chunks table (with 1536-dim vector & generated tsvector)
    op.execute(
        """
        CREATE TABLE hn_chunks (
            id UUID PRIMARY KEY,
            story_id BIGINT NOT NULL REFERENCES hn_stories(id) ON DELETE CASCADE,
            comment_id BIGINT REFERENCES hn_comments(id) ON DELETE SET NULL,
            chunk_type VARCHAR(32) NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1536),
            search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            quality_score FLOAT NOT NULL DEFAULT 0.0,
            metadata_json JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.create_index("ix_hn_chunks_story_id", "hn_chunks", ["story_id"])
    op.create_index("ix_hn_chunks_comment_id", "hn_chunks", ["comment_id"])
    op.create_index("ix_hn_chunks_chunk_type", "hn_chunks", ["chunk_type"])

    # Vector HNSW Index (Cosine distance) & Full-Text GIN Index
    op.execute(
        "CREATE INDEX ix_hn_chunks_embedding_hnsw ON hn_chunks USING hnsw (embedding vector_cosine_ops);"
    )
    op.execute(
        "CREATE INDEX ix_hn_chunks_search_vector_gin ON hn_chunks USING gin (search_vector);"
    )

    # 10. Message citations table
    op.create_table(
        "message_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hn_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("story_id", sa.BigInteger(), nullable=True),
        sa.Column("comment_id", sa.BigInteger(), nullable=True),
        sa.Column("hn_url", sa.Text(), nullable=False),
        sa.Column("citation_num", sa.Integer(), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_message_citations_message_id", "message_citations", ["message_id"]
    )
    op.create_index("ix_message_citations_chunk_id", "message_citations", ["chunk_id"])

    # 11. Materialized View for monthly trends
    op.execute(
        """
        CREATE MATERIALIZED VIEW trend_monthly AS
        SELECT
            st.tech,
            st.category,
            DATE_TRUNC('month', s.created_at)::date AS month,
            COUNT(DISTINCT s.id)::integer AS story_count,
            COALESCE(SUM(s.points), 0)::integer AS total_points,
            COALESCE(SUM(s.num_comments), 0)::integer AS total_comments,
            ROUND(
                (COUNT(DISTINCT s.id)::numeric / NULLIF(COALESCE(tm.total_stories, 0), 0)::numeric) * 100,
                4
            ) AS pct_of_monthly_stories
        FROM story_tech st
        JOIN hn_stories s ON s.id = st.story_id
        LEFT JOIN hn_totals_monthly tm ON tm.month = DATE_TRUNC('month', s.created_at)::date
        GROUP BY st.tech, st.category, DATE_TRUNC('month', s.created_at)::date, tm.total_stories;

        CREATE UNIQUE INDEX uq_trend_monthly_tech_category_month ON trend_monthly (tech, category, month);
        CREATE INDEX ix_trend_monthly_month ON trend_monthly (month);
        CREATE INDEX ix_trend_monthly_category ON trend_monthly (category);
        """
    )

    # 12. Row-level security and least-privilege Data API access
    # User-owned data is scoped to the authenticated Supabase user. The shared
    # Hacker News corpus and its aggregate trends are readable but not writable.
    op.execute(
        """
        ALTER TABLE users ENABLE ROW LEVEL SECURITY;
        ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;
        ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE message_citations ENABLE ROW LEVEL SECURITY;

        CREATE POLICY users_own_row ON users
            FOR ALL TO authenticated
            USING (id = (SELECT auth.uid()))
            WITH CHECK (id = (SELECT auth.uid()));

        CREATE POLICY chat_threads_own_rows ON chat_threads
            FOR ALL TO authenticated
            USING (user_id = (SELECT auth.uid()))
            WITH CHECK (user_id = (SELECT auth.uid()));

        CREATE POLICY chat_messages_own_rows ON chat_messages
            FOR ALL TO authenticated
            USING (EXISTS (
                SELECT 1 FROM chat_threads
                WHERE chat_threads.id = chat_messages.thread_id
                  AND chat_threads.user_id = (SELECT auth.uid())
            ))
            WITH CHECK (EXISTS (
                SELECT 1 FROM chat_threads
                WHERE chat_threads.id = chat_messages.thread_id
                  AND chat_threads.user_id = (SELECT auth.uid())
            ));

        CREATE POLICY message_citations_own_rows ON message_citations
            FOR ALL TO authenticated
            USING (EXISTS (
                SELECT 1
                FROM chat_messages
                JOIN chat_threads ON chat_threads.id = chat_messages.thread_id
                WHERE chat_messages.id = message_citations.message_id
                  AND chat_threads.user_id = (SELECT auth.uid())
            ))
            WITH CHECK (EXISTS (
                SELECT 1
                FROM chat_messages
                JOIN chat_threads ON chat_threads.id = chat_messages.thread_id
                WHERE chat_messages.id = message_citations.message_id
                  AND chat_threads.user_id = (SELECT auth.uid())
            ));

        ALTER TABLE hn_stories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE hn_comments ENABLE ROW LEVEL SECURITY;
        ALTER TABLE story_tech ENABLE ROW LEVEL SECURITY;
        ALTER TABLE hn_totals_monthly ENABLE ROW LEVEL SECURITY;
        ALTER TABLE hn_chunks ENABLE ROW LEVEL SECURITY;

        CREATE POLICY hn_stories_read ON hn_stories
            FOR SELECT TO anon, authenticated USING (true);
        CREATE POLICY hn_comments_read ON hn_comments
            FOR SELECT TO anon, authenticated USING (true);
        CREATE POLICY story_tech_read ON story_tech
            FOR SELECT TO anon, authenticated USING (true);
        CREATE POLICY hn_totals_monthly_read ON hn_totals_monthly
            FOR SELECT TO anon, authenticated USING (true);
        CREATE POLICY hn_chunks_read ON hn_chunks
            FOR SELECT TO anon, authenticated USING (true);

        REVOKE ALL ON TABLE
            users, chat_threads, chat_messages, message_citations,
            hn_stories, hn_comments, story_tech, hn_totals_monthly, hn_chunks,
            trend_monthly
        FROM PUBLIC, anon, authenticated;

        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            users, chat_threads, chat_messages, message_citations
        TO authenticated;

        GRANT SELECT ON TABLE
            hn_stories, hn_comments, story_tech, hn_totals_monthly, hn_chunks,
            trend_monthly
        TO anon, authenticated;

        GRANT ALL ON TABLE
            users, chat_threads, chat_messages, message_citations,
            hn_stories, hn_comments, story_tech, hn_totals_monthly, hn_chunks,
            trend_monthly
        TO service_role;
        """
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS trend_monthly;")
    op.drop_table("message_citations")
    op.drop_table("hn_chunks")
    op.drop_table("hn_totals_monthly")
    op.drop_table("story_tech")
    op.drop_table("hn_comments")
    op.drop_table("hn_stories")
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")
    op.drop_table("users")
