"""Command-line tasks.

    python -m app.cli bootstrap          wait for the database, migrate, then load the demo data
                                         (skipped when SEED_DEMO_DATA=false); used by Docker Compose
    python -m app.cli migrate            apply database migrations (owner role)
    python -m app.cli seed               load the synthetic demo dataset (idempotent)
    python -m app.cli ingest-samples     index the sample documents as shared library docs (idempotent)
    python -m app.cli create-admin --email you@example.com [--name "Your Name"] [--promote]
    python -m app.cli reindex [--all]    re-index documents built with an older configuration
    python -m app.cli rag-eval           run the retrieval evaluation and write a report
    python -m app.cli amfi-sync          fetch latest NAVs from AMFI (requires AMFI_ENABLED=true)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.config import BACKEND_DIR, get_settings
from app.core.errors import AppError, ConflictError
from app.db import get_sessionmaker
from app.models import Document, Fund, User

SAMPLE_DIR = BACKEND_DIR / "sample_data"
SEED_ORDER = ["benchmarks", "benchmark_values", "funds", "nav", "aum", "sip_flows", "holdings"]


def cmd_migrate(_: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
    print("Database is at the latest migration.")
    return 0


def _wait_for_database(timeout_s: float = 90.0) -> None:
    """Retries a trivial query until the database accepts connections."""
    import time

    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    settings = get_settings()
    secret = settings.migration_database_url or settings.database_url
    engine = create_engine(secret.get_secret_value(), pool_pre_ping=True)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except OperationalError:
                if time.monotonic() > deadline:
                    raise
                print("Waiting for the database...", flush=True)
                time.sleep(2)
    finally:
        engine.dispose()


def cmd_bootstrap(args: argparse.Namespace) -> int:
    _wait_for_database()
    if cmd_migrate(args) != 0:
        return 1
    seed_demo = os.environ.get("SEED_DEMO_DATA", "true").strip().lower() not in ("0", "false", "no")
    if not seed_demo:
        print("SEED_DEMO_DATA=false: skipping the synthetic demo data and sample documents.")
        return 0
    if cmd_seed(args) != 0:
        return 1
    return cmd_ingest_samples(args)


def cmd_seed(_: argparse.Namespace) -> int:
    from app.services.ingestion import csv_import

    settings = get_settings()
    db = get_sessionmaker()()
    try:
        csv_import.ensure_default_sources(db)
        for kind in SEED_ORDER:
            path = SAMPLE_DIR / "csv" / f"{kind}.csv"
            batch = csv_import.import_csv(
                db, kind=kind, filename=path.name, content=path.read_bytes(), source_code="synthetic-demo",
                uploaded_by=None, max_bytes=settings.max_csv_mb * 1024 * 1024,
            )
            print(f"{kind:17s} {batch.status:9s} inserted={batch.rows_inserted:6d} "
                  f"updated={batch.rows_updated:5d} unchanged={batch.rows_unchanged:6d} "
                  f"rejected={batch.rows_rejected}")
            if batch.status != "completed":
                print(f"  failed: {batch.warnings}", file=sys.stderr)
                return 1
    finally:
        db.close()
    print("Synthetic demonstration data is loaded (all values are fictional).")
    return 0


def cmd_ingest_samples(_: argparse.Namespace) -> int:
    from app.services.rag import indexing

    settings = get_settings()
    manifest = json.loads((SAMPLE_DIR / "documents" / "manifest.json").read_text(encoding="utf-8"))
    db = get_sessionmaker()()
    failures = 0
    try:
        for entry in manifest["documents"]:
            path = SAMPLE_DIR / "documents" / entry["file"]
            fund = db.scalar(select(Fund).where(Fund.scheme_code == entry["scheme_code"]))
            try:
                document = indexing.create_document(
                    db, settings, content=path.read_bytes(), filename=path.name, owner_id=None,
                    visibility="shared", title=entry["title"], doc_type=entry["doc_type"],
                    fund_id=fund.id if fund else None, as_of_date=date.fromisoformat(entry["as_of_date"]),
                    is_synthetic=True,
                )
            except ConflictError:
                existing = db.scalar(select(Document).where(Document.original_filename == path.name,
                                                            Document.owner_id.is_(None)))
                if existing is not None and existing.status == "indexed" \
                        and not indexing.needs_reindex(settings, existing):
                    print(f"skip     {path.name} (already indexed)")
                    continue
                document = existing
            if document is None:
                continue
            indexing.index_document(db, settings, document)
            print(f"{document.status:8s} {path.name} chunks={document.chunk_count}"
                  + (f" error={document.error_message}" if document.error_message else ""))
            failures += document.status != "indexed"
    finally:
        db.close()
    return 1 if failures else 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    from email_validator import EmailNotValidError, validate_email

    from app.services import auth_service

    try:
        # Same rules as the API's login form, so the account can actually sign in.
        validate_email(args.email, check_deliverability=False)
    except EmailNotValidError as exc:
        print(f"Invalid e-mail address: {exc}", file=sys.stderr)
        return 1
    db = get_sessionmaker()()
    try:
        existing = db.scalar(select(User).where(User.email == auth_service.normalise_email(args.email)))
        if existing is not None:
            if existing.role == "admin":
                print("That user is already an administrator.")
                return 0
            if not args.promote:
                print("A user with that e-mail exists; re-run with --promote to make them an admin.",
                      file=sys.stderr)
                return 1
            existing.role = "admin"
            db.commit()
            print(f"Promoted {existing.email} to administrator.")
            return 0
        password = os.environ.get("FINSENSE_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Admin password: ")
            if password != getpass.getpass("Repeat password: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1
        user = auth_service.register_user(db, email=args.email, password=password,
                                          display_name=args.name, role="admin")
        print(f"Created administrator {user.email}.")
        return 0
    except AppError as exc:
        print(f"Error: {exc.message} {exc.details or ''}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_reindex(args: argparse.Namespace) -> int:
    from app.services.rag import indexing

    settings = get_settings()
    db = get_sessionmaker()()
    try:
        documents = db.scalars(select(Document)).all()
        for document in documents:
            if args.all or document.status in ("failed", "pending") or indexing.needs_reindex(settings, document):
                indexing.index_document(db, settings, document)
                print(f"{document.status:8s} {document.original_filename}")
    finally:
        db.close()
    return 0


def cmd_rag_eval(args: argparse.Namespace) -> int:
    from app.services.rag import evaluation

    db = get_sessionmaker()()
    try:
        report = evaluation.run_evaluation(db, get_settings(), persist=True)
    finally:
        db.close()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = out.with_suffix(".md")
    from datetime import UTC, datetime

    cfg = report["config"]
    header = "\n".join([
        "# RAG retrieval evaluation report",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `python -m app.cli rag-eval` "
        "(question set: `sample_data/eval/rag_eval_set.json`; corpus: the synthetic sample documents "
        "plus one private note owned by a temporary evaluation user).",
        "",
        f"Configuration: embeddings `{cfg['embedding']['embedding_model']}` "
        f"({cfg['embedding']['embedding_dim']} dims), "
        f"chunks ~{cfg['embedding']['chunk_target_words']} words with {cfg['embedding']['chunk_overlap_words']}-word "
        f"overlap, {cfg['retrieval_candidates']} candidates per retriever, RRF k={cfg['rrf_k']} "
        f"(vector weight {cfg['rrf_vector_weight']}, lexical weight {cfg['rrf_lexical_weight']}), "
        f"reranker `{cfg['reranker']}`, top_k {cfg['top_k']}.",
        "",
        "Metric definitions: recall@k = share of the expected evidence snippets found in the top k passages; "
        "precision@k = share of the top k passages containing an expected snippet; MRR = mean reciprocal rank "
        "of the first relevant passage; nDCG@5 uses binary relevance; evidence_recall = expected snippets that "
        "survived the evidence gate into the answer context; abstention accuracy = no-evidence questions that "
        "were correctly declined; false abstention rate = answerable questions for which nothing passed the "
        "gate. Questions with no expected evidence are excluded from ranking metrics.",
        "",
        "",
    ])
    md.write_text(header + evaluation.render_markdown(report) + "\n", encoding="utf-8")
    print(evaluation.render_markdown(report))
    print(f"\nWrote {out} and {md}")
    return 0 if report["summary"]["passed"] else 1


def cmd_amfi_sync(_: argparse.Namespace) -> int:
    from app.services.ingestion import amfi

    db = get_sessionmaker()()
    try:
        result = amfi.sync_latest_navs(db, get_settings())
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="FinSense AI management commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap").set_defaults(func=cmd_bootstrap)
    sub.add_parser("migrate").set_defaults(func=cmd_migrate)
    sub.add_parser("seed").set_defaults(func=cmd_seed)
    sub.add_parser("ingest-samples").set_defaults(func=cmd_ingest_samples)
    admin = sub.add_parser("create-admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="Administrator")
    admin.add_argument("--promote", action="store_true", help="promote an existing user")
    admin.set_defaults(func=cmd_create_admin)
    reindex = sub.add_parser("reindex")
    reindex.add_argument("--all", action="store_true")
    reindex.set_defaults(func=cmd_reindex)
    rag_eval = sub.add_parser("rag-eval")
    rag_eval.add_argument("--output", default=str(BACKEND_DIR / "reports" / "rag_evaluation.json"))
    rag_eval.set_defaults(func=cmd_rag_eval)
    sub.add_parser("amfi-sync").set_defaults(func=cmd_amfi_sync)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
