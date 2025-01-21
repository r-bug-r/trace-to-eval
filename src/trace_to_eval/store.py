"""Local SQLite case store, run history, baselines, and live-call ledger."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redact import RedactionReport
from .schema import content_hash as compute_content_hash
from .util import utc_now

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    trace_id TEXT,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    source_path TEXT,
    trace_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    redaction_count INTEGER NOT NULL DEFAULT 0,
    redaction_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_cases_imported_at ON cases(imported_at);
CREATE INDEX IF NOT EXISTS idx_cases_trace_id ON cases(trace_id);

CREATE TABLE IF NOT EXISTS labels (
    label_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    category TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_labels_case_id ON labels(case_id, label_id DESC);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    code_revision TEXT NOT NULL,
    baseline_name TEXT,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    summary_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS results (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    output_text TEXT,
    output_json TEXT,
    assertions_json TEXT NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms REAL NOT NULL,
    error_class TEXT,
    error_message TEXT,
    human_label TEXT,
    label_agreement INTEGER,
    rubric_json TEXT,
    PRIMARY KEY (run_id, case_id)
);

CREATE TABLE IF NOT EXISTS baselines (
    name TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    project TEXT NOT NULL DEFAULT 'trace-to-eval',
    workflow TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_id TEXT,
    started_at TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,
    error_class TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_started_at ON ledger(started_at);
"""


@dataclass
class ResultRecord:
    case_id: str
    status: str
    output_text: str
    assertions: list[dict[str, Any]] = field(default_factory=list)
    output_json: dict[str, Any] | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error_class: str | None = None
    error_message: str | None = None
    human_label: str | None = None
    label_agreement: bool | None = None
    rubric: dict[str, Any] | None = None


def new_run_id() -> str:
    stamp = utc_now().replace("-", "").replace(":", "")
    return f"run_{stamp}_{uuid.uuid4().hex[:8]}"


class CaseStore:
    """A single-file SQLite store. All writes are parameterized and transactional."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CaseStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ cases
    def add_case(
        self,
        case_id: str,
        trace: dict[str, Any],
        *,
        source_path: str | None = None,
        redaction: RedactionReport | None = None,
    ) -> bool:
        report = redaction or RedactionReport()
        tags = trace.get("tags", [])
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO cases (
                    case_id, schema_version, trace_id, content_hash, imported_at,
                    source_path, trace_json, tags_json, redaction_count, redaction_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    trace.get("schema_version", ""),
                    trace.get("trace_id"),
                    compute_content_hash(trace),
                    utc_now(),
                    source_path,
                    json.dumps(trace, ensure_ascii=False, sort_keys=True),
                    json.dumps(tags, ensure_ascii=False),
                    report.total,
                    json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True),
                ),
            )
            return cursor.rowcount > 0

    def replace_trace(
        self,
        case_id: str,
        trace: dict[str, Any],
        *,
        tags: list[str] | None = None,
    ) -> bool:
        """Update the stored trace while preserving its stable case identity.

        The content hash is recomputed so the row still describes the current
        normalized payload. The case ID intentionally does not change: labels,
        assertions, and run history remain attached to the same case.
        """
        effective_tags = trace.get("tags", []) if tags is None else tags
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE cases
                SET schema_version = ?, trace_id = ?, content_hash = ?,
                    trace_json = ?, tags_json = ?
                WHERE case_id = ?
                """,
                (
                    trace.get("schema_version", ""),
                    trace.get("trace_id"),
                    compute_content_hash(trace),
                    json.dumps(trace, ensure_ascii=False, sort_keys=True),
                    json.dumps(effective_tags, ensure_ascii=False),
                    case_id,
                ),
            )
            return cursor.rowcount > 0

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        return self._case_from_row(row, self.latest_label(case_id))

    def list_cases(
        self,
        *,
        case_ids: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM cases"
        params: list[Any] = []
        if case_ids:
            placeholders = ",".join("?" for _ in case_ids)
            query += f" WHERE case_id IN ({placeholders})"
            params.extend(case_ids)
        query += " ORDER BY imported_at ASC, case_id ASC"
        rows = self._conn.execute(query, params).fetchall()
        labels = self.latest_labels([row["case_id"] for row in rows])
        cases = [self._case_from_row(row, labels.get(row["case_id"])) for row in rows]
        if tags:
            required = set(tags)
            cases = [case for case in cases if required.issubset(set(case["tags"]))]
        return cases

    def count_cases(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM cases").fetchone()
        assert row is not None
        count: int = row["count"]
        return count

    def delete_case(self, case_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
            return cursor.rowcount > 0

    def delete_all_cases(self) -> int:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM cases")
            return cursor.rowcount

    def _case_from_row(self, row: sqlite3.Row, label: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "case_id": row["case_id"],
            "schema_version": row["schema_version"],
            "trace_id": row["trace_id"],
            "content_hash": row["content_hash"],
            "imported_at": row["imported_at"],
            "source_path": row["source_path"],
            "trace": json.loads(row["trace_json"]),
            "tags": json.loads(row["tags_json"]),
            "redaction": json.loads(row["redaction_json"]),
            "label": label,
        }

    # ----------------------------------------------------------------- labels
    def add_label(
        self,
        case_id: str,
        label: str,
        *,
        category: str | None = None,
        notes: str | None = None,
    ) -> int:
        if self.get_case(case_id) is None:
            raise KeyError(f"unknown case: {case_id}")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO labels (case_id, label, category, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (case_id, label, category, notes, utc_now()),
            )
            return int(cursor.lastrowid or 0)

    def latest_label(self, case_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM labels WHERE case_id = ? ORDER BY label_id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def latest_labels(self, case_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not case_ids:
            return {}
        placeholders = ",".join("?" for _ in case_ids)
        # Only ``?`` placeholders are interpolated; case IDs are bound parameters.
        rows = self._conn.execute(
            f"SELECT * FROM labels WHERE case_id IN ({placeholders}) "  # noqa: S608
            "ORDER BY label_id DESC",
            case_ids,
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result.setdefault(row["case_id"], dict(row))
        return result

    def list_labels(self, case_id: str | None = None) -> list[dict[str, Any]]:
        if case_id is None:
            rows = self._conn.execute("SELECT * FROM labels ORDER BY label_id ASC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM labels WHERE case_id = ? ORDER BY label_id ASC", (case_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------- runs
    def create_run(
        self,
        run_id: str,
        *,
        provider: str,
        model: str,
        prompt_version: str,
        code_revision: str,
        baseline_name: str | None,
        config: dict[str, Any],
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO runs (
                    run_id, started_at, provider, model, prompt_version,
                    code_revision, baseline_name, status, config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_id,
                    utc_now(),
                    provider,
                    model,
                    prompt_version,
                    code_revision,
                    baseline_name,
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                ),
            )

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, summary_json = ? WHERE run_id = ?",
                (
                    utc_now(),
                    status,
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row is not None else None

    def delete_run(self, run_id: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            return cursor.rowcount > 0

    def latest_run(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def count_runs(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM runs").fetchone()
        assert row is not None
        count: int = row["count"]
        return count

    def _run_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        summary = result.pop("summary_json")
        result["summary"] = json.loads(summary) if summary else None
        return result

    # ---------------------------------------------------------------- results
    def add_result(self, run_id: str, record: ResultRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO results (
                    run_id, case_id, status, output_text, output_json, assertions_json,
                    cost_usd, latency_ms, error_class, error_message, human_label,
                    label_agreement, rubric_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record.case_id,
                    record.status,
                    record.output_text,
                    json.dumps(record.output_json, ensure_ascii=False, sort_keys=True)
                    if record.output_json is not None
                    else None,
                    json.dumps(record.assertions, ensure_ascii=False, sort_keys=True),
                    record.cost_usd,
                    record.latency_ms,
                    record.error_class,
                    record.error_message,
                    record.human_label,
                    None if record.label_agreement is None else int(record.label_agreement),
                    json.dumps(record.rubric, ensure_ascii=False, sort_keys=True)
                    if record.rubric is not None
                    else None,
                ),
            )

    def results_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM results WHERE run_id = ? ORDER BY case_id ASC", (run_id,)
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["assertions"] = json.loads(item.pop("assertions_json"))
            output_json = item.pop("output_json")
            item["output_json"] = json.loads(output_json) if output_json else None
            rubric_json = item.pop("rubric_json")
            item["rubric"] = json.loads(rubric_json) if rubric_json else None
            item["label_agreement"] = (
                None if item["label_agreement"] is None else bool(item["label_agreement"])
            )
            results.append(item)
        return results

    def results_for_case(self, case_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT r.*, runs.model AS run_model, runs.provider AS run_provider, "
            "runs.started_at AS run_started_at, runs.baseline_name AS run_baseline_name "
            "FROM results r JOIN runs ON runs.run_id = r.run_id "
            "WHERE r.case_id = ? ORDER BY runs.started_at DESC, runs.rowid DESC LIMIT ?",
            (case_id, limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["assertions"] = json.loads(item.pop("assertions_json"))
            output_json = item.pop("output_json")
            item["output_json"] = json.loads(output_json) if output_json else None
            rubric_json = item.pop("rubric_json")
            item["rubric"] = json.loads(rubric_json) if rubric_json else None
            item["label_agreement"] = (
                None if item["label_agreement"] is None else bool(item["label_agreement"])
            )
            results.append(item)
        return results

    # --------------------------------------------------------------- baseline
    def save_baseline(self, name: str, run_id: str, summary: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO baselines (name, run_id, created_at, summary_json) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET run_id=excluded.run_id, "
                "created_at=excluded.created_at, summary_json=excluded.summary_json",
                (
                    name,
                    run_id,
                    utc_now(),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get_baseline(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM baselines WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return {
            "name": row["name"],
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "summary": json.loads(row["summary_json"]),
        }

    def list_baselines(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM baselines ORDER BY created_at DESC, name ASC"
        ).fetchall()
        return [
            {
                "name": row["name"],
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "summary": json.loads(row["summary_json"]),
            }
            for row in rows
        ]

    # ----------------------------------------------------------------- ledger
    def add_ledger_entry(
        self,
        *,
        run_id: str,
        workflow: str,
        provider: str,
        model: str,
        request_id: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        cache_hit: bool,
        success: bool,
        error_class: str | None,
        started_at: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO ledger (
                    run_id, project, workflow, provider, model, request_id, started_at,
                    input_tokens, output_tokens, estimated_cost_usd, cache_hit, success,
                    error_class
                ) VALUES (?, 'trace-to-eval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workflow,
                    provider,
                    model,
                    request_id,
                    started_at or utc_now(),
                    input_tokens,
                    output_tokens,
                    estimated_cost_usd,
                    int(cache_hit),
                    int(success),
                    error_class,
                ),
            )

    def ledger_spend_since(self, started_at: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) AS spend "
            "FROM ledger WHERE started_at >= ?",
            (started_at,),
        ).fetchone()
        assert row is not None
        spend: float = float(row["spend"])
        return spend

    def ledger_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM ledger ORDER BY entry_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ maintenance
    def backup(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        destination_conn = sqlite3.connect(str(target))
        try:
            self._conn.backup(destination_conn)
        finally:
            destination_conn.close()
        return target

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")
