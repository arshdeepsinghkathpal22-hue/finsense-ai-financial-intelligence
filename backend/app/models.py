"""SQLAlchemy ORM models.

User-owned resources use UUID primary keys so identifiers cannot be guessed
by counting. Time-series observations use composite natural keys
(fund, date) which also makes repeated imports idempotent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

EMBEDDING_DIM = get_settings().embedding_dim


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- users


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),)


class UserSession(TimestampMixin, Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))

    user: Mapped[User] = relationship()


class PasswordResetToken(TimestampMixin, Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


# --------------------------------------------------------------------------- market data


class DataSource(TimestampMixin, Base):
    """Where a set of observations came from (provenance)."""

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        CheckConstraint("kind IN ('synthetic', 'csv_import', 'amfi')", name="ck_data_sources_kind"),
    )


class Benchmark(TimestampMixin, Base):
    __tablename__ = "benchmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # "total_return" indices include reinvested dividends; "price" ones do not.
    return_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)

    source: Mapped[DataSource] = relationship()

    __table_args__ = (
        CheckConstraint("return_basis IN ('price', 'total_return')", name="ck_benchmarks_basis"),
    )


class Fund(TimestampMixin, Base):
    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amc: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(24), nullable=False, default="equity")
    plan: Mapped[str | None] = mapped_column(String(16))
    option: Mapped[str | None] = mapped_column(String(16))
    benchmark_id: Mapped[int | None] = mapped_column(ForeignKey("benchmarks.id"))
    launch_date: Mapped[date | None] = mapped_column(Date)
    expense_ratio_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    risk_label: Mapped[str | None] = mapped_column(String(40))
    # NAV of a growth option already reflects reinvested income (net of
    # expenses); an IDCW option's NAV drops on payout, so its NAV return is a
    # price return only.
    return_basis: Mapped[str] = mapped_column(String(24), nullable=False, default="nav_growth")
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    benchmark: Mapped[Benchmark | None] = relationship()
    source: Mapped[DataSource] = relationship()

    __table_args__ = (
        CheckConstraint("return_basis IN ('nav_growth', 'nav_price')", name="ck_funds_basis"),
        Index("ix_funds_name_lower", func.lower(name)),
    )


class NavObservation(Base):
    __tablename__ = "nav_observations"

    fund_id: Mapped[int] = mapped_column(
        ForeignKey("funds.id", ondelete="CASCADE"), primary_key=True
    )
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )

    __table_args__ = (CheckConstraint("nav > 0", name="ck_nav_positive"),)


class BenchmarkObservation(Base):
    __tablename__ = "benchmark_observations"

    benchmark_id: Mapped[int] = mapped_column(
        ForeignKey("benchmarks.id", ondelete="CASCADE"), primary_key=True
    )
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )

    __table_args__ = (CheckConstraint("value > 0", name="ck_benchmark_value_positive"),)


class FundAum(Base):
    __tablename__ = "fund_aum"

    fund_id: Mapped[int] = mapped_column(
        ForeignKey("funds.id", ondelete="CASCADE"), primary_key=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    aum_crore: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)

    __table_args__ = (CheckConstraint("aum_crore >= 0", name="ck_aum_nonneg"),)


class FundSipFlow(Base):
    __tablename__ = "fund_sip_flows"

    fund_id: Mapped[int] = mapped_column(
        ForeignKey("funds.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    sip_inflow_crore: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sip_accounts: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)


class FundHolding(Base):
    __tablename__ = "fund_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        ForeignKey("funds.id", ondelete="CASCADE"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    holding_name: Mapped[str] = mapped_column(String(160), nullable=False)
    sector: Mapped[str] = mapped_column(String(80), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False, default="equity")
    weight_pct: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("fund_id", "as_of_date", "holding_name", name="uq_holding"),
        CheckConstraint("weight_pct >= 0 AND weight_pct <= 100", name="ck_holding_weight"),
        Index("ix_holdings_fund_date", "fund_id", "as_of_date"),
    )


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    rejected_rows: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- portfolios


class Portfolio(TimestampMixin, Base):
    __tablename__ = "portfolios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    initial_value: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    benchmark_id: Mapped[int | None] = mapped_column(ForeignKey("benchmarks.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    assets: Mapped[list[PortfolioAsset]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_portfolio_user_name"),
        CheckConstraint("initial_value > 0", name="ck_portfolio_value"),
    )


class PortfolioAsset(Base):
    __tablename__ = "portfolio_assets"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True
    )
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id"), primary_key=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)

    portfolio: Mapped[Portfolio] = relationship(back_populates="assets")
    fund: Mapped[Fund] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("weight > 0 AND weight <= 1", name="ck_asset_weight"),)


# --------------------------------------------------------------------------- documents / RAG


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # NULL owner + visibility 'shared' = library document visible to all users.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    visibility: Mapped[str] = mapped_column(String(8), nullable=False, default="private")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(24), nullable=False, default="other")
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id", ondelete="SET NULL"))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fund: Mapped[Fund | None] = relationship()

    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'shared')", name="ck_documents_visibility"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'indexed', 'failed', 'needs_ocr')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "doc_type IN ('factsheet', 'annual_report', 'sid', 'research', 'other')",
            name="ck_documents_type",
        ),
        # Same file uploaded twice by the same owner is rejected, not re-indexed.
        Index(
            "uq_documents_owner_sha",
            "owner_id",
            "sha256",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # "<title> | <fund name> | <scheme code>", copied from the document so the
    # lexical index matches fund names and codes that appear only on page 1.
    context_label: Mapped[str] = mapped_column(String(400), nullable=False)
    section_heading: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_table: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(80), nullable=False)
    # Generated lexical index: section heading (A) > body text (B) > context label (C).
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(section_heading, '')), 'A') || "
            "setweight(to_tsvector('english', content), 'B') || "
            "setweight(to_tsvector('english', context_label), 'C')",
            persisted=True,
        ),
    )

    document: Mapped[Document] = relationship()

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_position"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),)


# --------------------------------------------------------------------------- ML / evaluation


class MlModel(TimestampMixin, Base):
    __tablename__ = "ml_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    model_type: Mapped[str] = mapped_column(String(24), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    data_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    test_end: Mapped[date] = mapped_column(Date, nullable=False)
    feature_names: Mapped[list] = mapped_column(JSONB, nullable=False)
    hyperparameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(80), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint(
            "fund_id", "model_type", "horizon_days", "data_fingerprint", name="uq_ml_model_version"
        ),
    )


class RagEvaluationRun(TimestampMixin, Base):
    __tablename__ = "rag_evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    per_query: Mapped[list] = mapped_column(JSONB, nullable=False)
