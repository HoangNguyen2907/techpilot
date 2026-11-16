import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.chat import MessageCitation


class HNStory(Base):
    """Cleaned Hacker News story."""

    __tablename__ = "hn_stories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # HN story ID
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    points: Mapped[int] = mapped_column(Integer, default=0, index=True, nullable=False)
    num_comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        String(32), default="story", nullable=False
    )  # story, ask_hn, show_hn

    comments: Mapped[list["HNComment"]] = relationship(
        "HNComment", back_populates="story", cascade="all, delete-orphan"
    )
    technologies: Mapped[list["StoryTech"]] = relationship(
        "StoryTech", back_populates="story", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["HNChunk"]] = relationship(
        "HNChunk", back_populates="story", cascade="all, delete-orphan"
    )


class HNComment(Base):
    """Flattened Hacker News comment with inferred quality metrics."""

    __tablename__ = "hn_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # HN comment ID
    story_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("hn_stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    n_children: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quality_score: Mapped[float] = mapped_column(
        Float, default=0.0, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    story: Mapped["HNStory"] = relationship("HNStory", back_populates="comments")
    chunks: Mapped[list["HNChunk"]] = relationship("HNChunk", back_populates="comment")


class StoryTech(Base):
    """Entity mapping linking a story to matched technologies from tech_dict.yaml."""

    __tablename__ = "story_tech"
    __table_args__ = (
        UniqueConstraint("story_id", "tech", name="uq_story_tech"),
        Index("ix_story_tech_tech_category", "tech", "category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    story_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("hn_stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tech: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    in_title: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    via: Mapped[str] = mapped_column(
        String(50), default="direct", nullable=False
    )  # direct, ambiguous, implied

    story: Mapped["HNStory"] = relationship("HNStory", back_populates="technologies")


class HNTotalsMonthly(Base):
    """Total Hacker News stories, points, and comments per month for normalization."""

    __tablename__ = "hn_totals_monthly"

    month: Mapped[date] = mapped_column(Date, primary_key=True)
    total_stories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class HNChunk(Base):
    """Retrievable text chunk (title, LLM summary, or high-value comment)."""

    __tablename__ = "hn_chunks"
    __table_args__ = (
        Index(
            "ix_hn_chunks_search_vector_gin", "search_vector", postgresql_using="gin"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    story_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("hn_stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    comment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("hn_comments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chunk_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # title, summary, comment
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    search_vector = Column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
    )
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    story: Mapped["HNStory"] = relationship("HNStory", back_populates="chunks")
    comment: Mapped["HNComment | None"] = relationship(
        "HNComment", back_populates="chunks"
    )
    citations: Mapped[list["MessageCitation"]] = relationship(
        "MessageCitation", back_populates="chunk"
    )
