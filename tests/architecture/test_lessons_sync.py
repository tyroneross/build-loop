"""Tests for scripts/sync_navgator_lessons.py (Chunk 7).

Coverage:
  - template entries are skipped (only real lessons synced)
  - SQLite upsert is idempotent: running twice leaves a single row per (subject, project)
  - global lessons get ``project IS NULL`` in the local row
  - Postgres mirror unreachable keeps SQLite counts and adds errors=['postgres_unavailable']
  - confidence_source maps to write_decision's closed taxonomy
    (auto-confirmed | auto-inferred), Chunk 6 alignment
  - ``--dry-run`` does not open SQLite or Postgres

Postgres and embedding calls are mocked. CI does not need live Postgres.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_navgator_lessons as sync_mod  # type: ignore  # noqa: E402


# ---------- shared fixtures ----------


def _real_lesson(idx: int = 1, *, promoted: bool = False) -> dict:
    return {
        "id": f"lesson-test-{idx}",
        "category": "api-contract",
        "pattern": f"Real lesson pattern body #{idx}",
        "signature": [r"foo\.bar"],
        "severity": "important",
        "promoted": promoted,
        "context": {
            "first_seen": "2026-04-01",
            "last_seen": "2026-04-30",
            "occurrences": 3,
            "files_affected": ["src/foo.py", "src/bar.py"],
            "resolution": "Use the canonical adapter",
        },
    }


def _template_lesson() -> dict:
    return {
        "id": "_template",
        "category": "api-contract",
        "pattern": "TEMPLATE — should never be synced",
    }


def _write_project_lessons(workdir: Path, lessons: list[dict]) -> Path:
    path = workdir / ".navgator" / "lessons" / "lessons.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "1.0.0", "project": "test", "lessons": lessons}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_global_lessons(home_dir: Path, lessons: list[dict]) -> Path:
    path = home_dir / ".navgator" / "lessons" / "global-lessons.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "1.0.0", "lessons": lessons}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeCursor:
    """Minimal cursor recording every (sql, params) call."""

    def __init__(self, store: list[tuple[str, tuple]]):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self._store.append((sql, tuple(params) if params else ()))


class FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.committed = False
        self.rolled_back = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.executed)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


@pytest.fixture
def mock_embed(monkeypatch):
    """Force embed_backend.embed to return a deterministic small vector."""

    def fake_embed(text: str) -> list[float]:
        # Normalised tiny vector, length-1024 padded with zeros so the
        # `::vector` cast layer downstream is always shape-correct.
        head = [hash(text) % 7 / 10.0, 0.1, 0.2]
        return head + [0.0] * (1024 - len(head))

    fake_module = MagicMock()
    fake_module.embed = fake_embed
    monkeypatch.setitem(sys.modules, "embed_backend", fake_module)
    return fake_embed


@pytest.fixture
def mock_psycopg(monkeypatch):
    """Replace `psycopg.connect` with a FakeConn factory.

    Returned object exposes the most recent FakeConn for assertions.
    """
    holder = {"conn": None, "calls": 0}

    def fake_connect(dsn=None, autocommit=False):  # noqa: ARG001
        holder["calls"] += 1
        conn = FakeConn()
        holder["conn"] = conn
        return conn

    fake_psycopg = MagicMock()
    fake_psycopg.connect = fake_connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    return holder


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Force a known DSN and isolate HOME so global-lessons reads our tmp."""
    monkeypatch.setenv("BUILD_LOOP_DATABASE_URL", "postgres://test/test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _capture_stdout(monkeypatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    return buf


def _sqlite_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT subject, project, predicate, object, confidence_source FROM semantic_facts ORDER BY subject"
            ).fetchall()
        ]
    finally:
        conn.close()


# ---------- tests ----------


def test_skip_template_entries(monkeypatch, isolated_env):
    """A lessons.json with one _template + one real entry -> only real synced."""
    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    _write_project_lessons(
        workdir, [_template_lesson(), _real_lesson(1)]
    )
    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main(
        ["--workdir", str(workdir), "--project-only", "--sqlite-db", str(db_path)]
    )
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["synced"] == 1
    assert out["skipped_templates"] == 1
    assert out["global_synced"] == 0
    assert out["postgres_mirrored"] == 0
    rows = _sqlite_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["subject"] == "lesson:nav:lesson-test-1"


def test_upsert_idempotent(monkeypatch, isolated_env):
    """Run twice; second run still issues 1 DELETE + 1 INSERT per lesson and
    yields synced=1 each time. The DELETE makes the second write a no-op
    against any prior row → idempotent."""
    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    _write_project_lessons(workdir, [_real_lesson(1)])

    # First run.
    buf1 = _capture_stdout(monkeypatch)
    rc1 = sync_mod.main(["--workdir", str(workdir), "--project-only", "--sqlite-db", str(db_path)])
    assert rc1 == 0
    out1 = json.loads(buf1.getvalue())
    assert out1["synced"] == 1
    first_rows = _sqlite_rows(db_path)

    # Second run, same input.
    buf2 = _capture_stdout(monkeypatch)
    rc2 = sync_mod.main(["--workdir", str(workdir), "--project-only", "--sqlite-db", str(db_path)])
    assert rc2 == 0
    out2 = json.loads(buf2.getvalue())
    assert out2["synced"] == 1
    second_rows = _sqlite_rows(db_path)

    assert len(first_rows) == 1
    assert len(second_rows) == 1
    assert first_rows[0]["subject"] == second_rows[0]["subject"]


def test_global_lessons_have_null_project(
    monkeypatch, isolated_env
):
    """Sync a global lesson; assert project is NULL in SQLite."""
    home_dir = Path(os.environ["HOME"])
    home_dir.mkdir(parents=True, exist_ok=True)
    _write_global_lessons(home_dir, [_real_lesson(42, promoted=True)])

    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main(["--workdir", str(workdir), "--global-only", "--sqlite-db", str(db_path)])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["global_synced"] == 1
    assert out["synced"] == 0

    rows = _sqlite_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["project"] is None


def test_postgres_unavailable_graceful(monkeypatch, isolated_env, mock_embed):
    """psycopg.connect raises -> SQLite still syncs and mirror reports soft error."""

    # Build an OperationalError-ish exception via a stub module.
    class FakeOperationalError(Exception):
        pass

    def boom(*args, **kwargs):
        raise FakeOperationalError("connection refused")

    fake_psycopg = MagicMock()
    fake_psycopg.connect = boom
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    _write_project_lessons(workdir, [_real_lesson(1)])

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main([
        "--workdir", str(workdir),
        "--project-only",
        "--sqlite-db", str(db_path),
        "--postgres-mirror",
    ])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert "postgres_unavailable" in out["errors"]
    assert out["synced"] == 1
    assert out["postgres_mirrored"] == 0
    assert len(_sqlite_rows(db_path)) == 1
    # And the error log should now exist with one line.
    log_path = workdir / ".build-loop" / "sync_errors.log"
    assert log_path.exists()
    body = log_path.read_text(encoding="utf-8")
    assert "postgres_unavailable" in body


def test_taxonomy_mapping(monkeypatch):
    """confidence_source must always be one of {auto-confirmed, auto-inferred}."""
    # Promoted → auto-confirmed.
    assert sync_mod._confidence_source_for(True) == "auto-confirmed"
    # Un-promoted → auto-inferred.
    assert sync_mod._confidence_source_for(False) == "auto-inferred"
    # Defensive: nothing else can leak through (function takes a bool, but
    # the upsert path computes via `bool(lesson.get('promoted', False))`).
    for promoted_input in (True, False, None, 0, 1, "", "yes"):
        cs = sync_mod._confidence_source_for(bool(promoted_input))
        assert cs in {"auto-confirmed", "auto-inferred"}


def test_lessons_file_flag_overrides_discovery(
    monkeypatch, isolated_env
):
    """--lessons-file PATH treats the given file as the project-local source.

    Even when the canonical NavGator project-local path also exists, the
    override file wins and the canonical one is skipped (else we'd double-sync).
    """
    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    # Canonical NavGator project-local path — should be SKIPPED.
    _write_project_lessons(workdir, [_real_lesson(99)])
    # Override file — should be the SOLE source.
    override_path = workdir / ".build-loop" / "architecture" / "lessons.json"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "lessons": [_real_lesson(1, promoted=True), _real_lesson(2)],
    }
    override_path.write_text(json.dumps(payload), encoding="utf-8")

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main(
        [
            "--workdir", str(workdir),
            "--lessons-file", str(override_path),
            "--sqlite-db", str(db_path),
        ]
    )
    assert rc == 0
    out = json.loads(buf.getvalue())
    # 2 lessons from the override file; the canonical lesson-99 is skipped
    # entirely because --lessons-file flips us into override mode.
    assert out["synced"] == 2
    assert out["global_synced"] == 0
    rows = _sqlite_rows(db_path)
    assert len(rows) == 2
    assert not any(row["subject"].endswith("lesson-test-99") for row in rows)


def test_source_prefix_lesson_bl(
    monkeypatch, isolated_env
):
    """--source-prefix lesson:bl: must land in SQLite subjects."""
    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    override = workdir / ".build-loop" / "architecture" / "lessons.json"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(
        json.dumps({"schema_version": "1.0.0", "lessons": [_real_lesson(1)]}),
        encoding="utf-8",
    )

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main(
        [
            "--workdir", str(workdir),
            "--lessons-file", str(override),
            "--source-prefix", "lesson:bl:",
            "--sqlite-db", str(db_path),
        ]
    )
    assert rc == 0

    rows = _sqlite_rows(db_path)
    subject = rows[0]["subject"]
    assert subject.startswith("lesson:bl:"), f"subject={subject!r}"


def test_default_source_prefix_lesson_nav(
    monkeypatch, isolated_env
):
    """Backward-compat: default flow (no flags) still uses 'lesson:nav:'."""
    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    _write_project_lessons(workdir, [_real_lesson(1)])

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main([
        "--workdir", str(workdir),
        "--project-only",
        "--sqlite-db", str(db_path),
    ])
    assert rc == 0

    subject = _sqlite_rows(db_path)[0]["subject"]
    assert subject.startswith("lesson:nav:")


def test_postgres_mirror_writes_when_requested(
    monkeypatch, isolated_env, mock_embed, mock_psycopg
):
    """--postgres-mirror preserves the legacy Postgres write path explicitly."""
    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    _write_project_lessons(workdir, [_real_lesson(1)])

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main([
        "--workdir", str(workdir),
        "--project-only",
        "--sqlite-db", str(db_path),
        "--postgres-mirror",
    ])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["synced"] == 1
    assert out["postgres_mirrored"] == 1

    conn = mock_psycopg["conn"]
    sql_text = " | ".join(s for s, _ in conn.executed)
    assert sql_text.count("DELETE FROM") == 1
    assert sql_text.count("INSERT INTO") == 1


def test_dry_run_no_writes(monkeypatch, isolated_env, mock_embed):
    """--dry-run never touches SQLite or psycopg.connect."""
    connect_called = {"count": 0}

    def boom(*args, **kwargs):
        connect_called["count"] += 1
        raise AssertionError("psycopg.connect called during --dry-run")

    fake_psycopg = MagicMock()
    fake_psycopg.connect = boom
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    workdir = isolated_env / "proj"
    workdir.mkdir()
    db_path = workdir / "semantic.sqlite"
    _write_project_lessons(workdir, [_real_lesson(1), _template_lesson()])

    buf = _capture_stdout(monkeypatch)
    rc = sync_mod.main([
        "--workdir", str(workdir),
        "--project-only",
        "--sqlite-db", str(db_path),
        "--dry-run",
        "--postgres-mirror",
    ])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["dry_run"] is True
    assert out["synced"] == 1
    assert out["skipped_templates"] == 1
    assert connect_called["count"] == 0
    assert not db_path.exists()
