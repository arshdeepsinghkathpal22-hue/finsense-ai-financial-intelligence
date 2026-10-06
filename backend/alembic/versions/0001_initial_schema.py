"""Initial schema: users, market data, portfolios, documents/RAG, ML registry.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# Vector column width is fixed at migration time. Changing the embedding
# model to one with a different dimension requires a new migration (and a
# re-index of every document).
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "256"))

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def _ensure_pgvector() -> None:
    bind = op.get_bind()
    installed = bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()
    if installed:
        return
    try:
        op.execute("CREATE EXTENSION vector")
    except sa.exc.ProgrammingError as exc:  # pragma: no cover - depends on server setup
        raise RuntimeError(
            "The pgvector extension is not enabled in this database and the migration role "
            "cannot create it. Run `CREATE EXTENSION vector;` as a superuser "
            "(see db/manual-setup.sql) and retry."
        ) from exc


def upgrade() -> None:
    _ensure_pgvector()
    op.create_table('data_sources',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('url', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('synthetic', 'csv_import', 'amfi')", name='ck_data_sources_kind'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('rag_evaluation_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('per_query', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=80), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('preferences', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'admin')", name='ck_users_role'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('audit_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_event_type'), 'audit_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_audit_events_user_id'), 'audit_events', ['user_id'], unique=False)
    op.create_table('benchmarks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('return_basis', sa.String(length=16), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("return_basis IN ('price', 'total_return')", name='ck_benchmarks_basis'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_table('import_batches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('uploaded_by', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rows_total', sa.Integer(), nullable=False),
    sa.Column('rows_inserted', sa.Integer(), nullable=False),
    sa.Column('rows_updated', sa.Integer(), nullable=False),
    sa.Column('rows_unchanged', sa.Integer(), nullable=False),
    sa.Column('rows_rejected', sa.Integer(), nullable=False),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rejected_rows', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('password_reset_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_password_reset_tokens_user_id'), 'password_reset_tokens', ['user_id'], unique=False)
    op.create_table('user_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=256), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    op.create_table('benchmark_observations',
    sa.Column('benchmark_id', sa.Integer(), nullable=False),
    sa.Column('obs_date', sa.Date(), nullable=False),
    sa.Column('value', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('import_batch_id', sa.UUID(), nullable=True),
    sa.CheckConstraint('value > 0', name='ck_benchmark_value_positive'),
    sa.ForeignKeyConstraint(['benchmark_id'], ['benchmarks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('benchmark_id', 'obs_date')
    )
    op.create_table('funds',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('scheme_code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('amc', sa.String(length=120), nullable=True),
    sa.Column('category', sa.String(length=80), nullable=False),
    sa.Column('asset_class', sa.String(length=24), nullable=False),
    sa.Column('plan', sa.String(length=16), nullable=True),
    sa.Column('option', sa.String(length=16), nullable=True),
    sa.Column('benchmark_id', sa.Integer(), nullable=True),
    sa.Column('launch_date', sa.Date(), nullable=True),
    sa.Column('expense_ratio_pct', sa.Numeric(precision=6, scale=3), nullable=True),
    sa.Column('risk_label', sa.String(length=40), nullable=True),
    sa.Column('return_basis', sa.String(length=24), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("return_basis IN ('nav_growth', 'nav_price')", name='ck_funds_basis'),
    sa.ForeignKeyConstraint(['benchmark_id'], ['benchmarks.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scheme_code')
    )
    op.create_index(op.f('ix_funds_category'), 'funds', ['category'], unique=False)
    op.create_index('ix_funds_name_lower', 'funds', [sa.literal_column('lower(name)')], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=12), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant')", name='ck_messages_role'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)
    op.create_table('portfolios',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('initial_value', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('benchmark_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('initial_value > 0', name='ck_portfolio_value'),
    sa.ForeignKeyConstraint(['benchmark_id'], ['benchmarks.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name', name='uq_portfolio_user_name')
    )
    op.create_index(op.f('ix_portfolios_user_id'), 'portfolios', ['user_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('visibility', sa.String(length=8), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('storage_key', sa.String(length=80), nullable=False),
    sa.Column('mime_type', sa.String(length=40), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('doc_type', sa.String(length=24), nullable=False),
    sa.Column('fund_id', sa.Integer(), nullable=True),
    sa.Column('as_of_date', sa.Date(), nullable=True),
    sa.Column('is_synthetic', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('index_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("doc_type IN ('factsheet', 'annual_report', 'sid', 'research', 'other')", name='ck_documents_type'),
    sa.CheckConstraint("status IN ('pending', 'processing', 'indexed', 'failed', 'needs_ocr')", name='ck_documents_status'),
    sa.CheckConstraint("visibility IN ('private', 'shared')", name='ck_documents_visibility'),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key')
    )
    op.create_index(op.f('ix_documents_owner_id'), 'documents', ['owner_id'], unique=False)
    op.create_index('uq_documents_owner_sha', 'documents', ['owner_id', 'sha256'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('fund_aum',
    sa.Column('fund_id', sa.Integer(), nullable=False),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('aum_crore', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('aum_crore >= 0', name='ck_aum_nonneg'),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('fund_id', 'as_of_date')
    )
    op.create_table('fund_holdings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('fund_id', sa.Integer(), nullable=False),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('holding_name', sa.String(length=160), nullable=False),
    sa.Column('sector', sa.String(length=80), nullable=False),
    sa.Column('asset_type', sa.String(length=40), nullable=False),
    sa.Column('weight_pct', sa.Numeric(precision=7, scale=3), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('weight_pct >= 0 AND weight_pct <= 100', name='ck_holding_weight'),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fund_id', 'as_of_date', 'holding_name', name='uq_holding')
    )
    op.create_index('ix_holdings_fund_date', 'fund_holdings', ['fund_id', 'as_of_date'], unique=False)
    op.create_table('fund_sip_flows',
    sa.Column('fund_id', sa.Integer(), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('sip_inflow_crore', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('sip_accounts', sa.Integer(), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('fund_id', 'month')
    )
    op.create_table('ml_models',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('fund_id', sa.Integer(), nullable=False),
    sa.Column('model_type', sa.String(length=24), nullable=False),
    sa.Column('horizon_days', sa.Integer(), nullable=False),
    sa.Column('data_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('train_start', sa.Date(), nullable=False),
    sa.Column('train_end', sa.Date(), nullable=False),
    sa.Column('test_end', sa.Date(), nullable=False),
    sa.Column('feature_names', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('hyperparameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('artifact_key', sa.String(length=80), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fund_id', 'model_type', 'horizon_days', 'data_fingerprint', name='uq_ml_model_version')
    )
    op.create_table('nav_observations',
    sa.Column('fund_id', sa.Integer(), nullable=False),
    sa.Column('obs_date', sa.Date(), nullable=False),
    sa.Column('nav', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('import_batch_id', sa.UUID(), nullable=True),
    sa.CheckConstraint('nav > 0', name='ck_nav_positive'),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('fund_id', 'obs_date')
    )
    op.create_table('portfolio_assets',
    sa.Column('portfolio_id', sa.UUID(), nullable=False),
    sa.Column('fund_id', sa.Integer(), nullable=False),
    sa.Column('weight', sa.Numeric(precision=8, scale=6), nullable=False),
    sa.CheckConstraint('weight > 0 AND weight <= 1', name='ck_asset_weight'),
    sa.ForeignKeyConstraint(['fund_id'], ['funds.id'], ),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('portfolio_id', 'fund_id')
    )
    op.create_table('document_chunks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('page_start', sa.Integer(), nullable=False),
    sa.Column('page_end', sa.Integer(), nullable=False),
    sa.Column('context_label', sa.String(length=400), nullable=False),
    sa.Column('section_heading', sa.String(length=200), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('is_table', sa.Boolean(), nullable=False),
    sa.Column('flags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('embedding', Vector(EMBEDDING_DIM), nullable=False),
    sa.Column('embedding_model', sa.String(length=80), nullable=False),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed(
        "setweight(to_tsvector('english', coalesce(section_heading, '')), 'A') || "
        "setweight(to_tsvector('english', content), 'B') || "
        "setweight(to_tsvector('english', context_label), 'C')",
        persisted=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'chunk_index', name='uq_chunk_position')
    )
    op.create_index('ix_chunks_embedding_hnsw', 'document_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('ix_chunks_search_vector', 'document_chunks', ['search_vector'], unique=False, postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('ix_chunks_search_vector', table_name='document_chunks', postgresql_using='gin')
    op.drop_index('ix_chunks_embedding_hnsw', table_name='document_chunks', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('document_chunks')
    op.drop_table('portfolio_assets')
    op.drop_table('nav_observations')
    op.drop_table('ml_models')
    op.drop_table('fund_sip_flows')
    op.drop_index('ix_holdings_fund_date', table_name='fund_holdings')
    op.drop_table('fund_holdings')
    op.drop_table('fund_aum')
    op.drop_index('uq_documents_owner_sha', table_name='documents', postgresql_nulls_not_distinct=True)
    op.drop_index(op.f('ix_documents_owner_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_portfolios_user_id'), table_name='portfolios')
    op.drop_table('portfolios')
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_funds_name_lower', table_name='funds')
    op.drop_index(op.f('ix_funds_category'), table_name='funds')
    op.drop_table('funds')
    op.drop_table('benchmark_observations')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index(op.f('ix_password_reset_tokens_user_id'), table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    op.drop_table('import_batches')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_table('benchmarks')
    op.drop_index(op.f('ix_audit_events_user_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_event_type'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('users')
    op.drop_table('rag_evaluation_runs')
    op.drop_table('data_sources')
