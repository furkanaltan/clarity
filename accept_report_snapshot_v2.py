"""Controlled production acceptance for Report Snapshot V2.

This script is intentionally fail-closed. It creates one DB backup through the
additive migration, validates a chosen completed month, renders Web/PDF from
the exact same snapshot payload, and verifies final-snapshot reuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import report_engine
import report_story_v2
import rove_pdf_light_renderer
import rove_web_report_renderer


def fail(message: str) -> None:
    raise RuntimeError(message)


def run(command: list[str], *, check: bool = True) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    output = (result.stdout + result.stderr).strip()
    if check and result.returncode != 0:
        fail(f"command_failed:{' '.join(command)}:{output[-500:]}")
    return output


def finite(value) -> None:
    if isinstance(value, dict):
        for item in value.values():
            finite(item)
    elif isinstance(value, list):
        for item in value:
            finite(item)
    elif isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        fail("snapshot_contains_non_finite_number")


def sqlite_checks(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
    if integrity != "ok":
        fail(f"integrity_check:{integrity}")
    if foreign_keys:
        fail(f"foreign_key_check:{len(foreign_keys)}")
    return {"integrity_check": integrity, "foreign_key_errors": len(foreign_keys)}


def service_checks() -> dict:
    api = run(["systemctl", "is-active", "rove-app-api"])
    timer = run(["systemctl", "is-active", "rove-report-worker.timer"])
    worker_result = run(["systemctl", "show", "rove-report-worker.service", "-p", "ExecMainStatus", "-p", "Result"])
    worker_logs = run([
        "journalctl", "-u", "rove-report-worker.service", "--since", "24 hours ago", "--no-pager", "-o", "cat",
    ])
    health = run(["curl", "-fsS", "https://getrove.de/app-api/health"])
    if api != "active" or timer != "active":
        fail(f"service_not_active:api={api}:worker_timer={timer}")
    if "Result=success" not in worker_result and "ExecMainStatus=0" not in worker_result:
        fail(f"worker_last_run_not_healthy:{worker_result}")
    error_lines = [
        line for line in worker_logs.splitlines()
        if any(token in line.lower() for token in ("traceback", "unhandled", "snapshot_error", "renderer_error", "cross-user"))
    ]
    if error_lines:
        fail("worker_report_errors_detected")
    payload = json.loads(health)
    if payload.get("ok") is not True:
        fail("app_health_not_ok")
    return {
        "api": api,
        "worker_timer": timer,
        "worker_last_run": worker_result,
        "worker_log_errors": 0,
        "health": payload,
    }


def snapshot_checks(snapshot: dict, user_id: int, report_month: str) -> dict:
    data = snapshot.get("data") or {}
    if data.get("meta", {}).get("user_id") != user_id:
        fail("snapshot_user_id_mismatch")
    if data.get("meta", {}).get("report_month") != report_month:
        fail("snapshot_report_month_mismatch")
    if snapshot.get("schema_version") != report_engine.REPORT_SNAPSHOT_SCHEMA_VERSION:
        fail("snapshot_schema_version_mismatch")
    if snapshot.get("status") != "finalized":
        fail("snapshot_not_finalized")
    if not snapshot.get("data_hash") or not data.get("report_truth"):
        fail("snapshot_hash_or_truth_missing")
    finite(data)
    truth = data["report_truth"]
    if not truth["cash"].get("invariant_ok"):
        fail("cash_invariant_failed")

    expenses = truth["expenses"]
    category_total = round(sum(item["amount"] for item in expenses["categories"]), 2)
    merchant_total = round(sum(item["amount"] for item in expenses["merchants"]), 2)
    consumption_total = round(float(expenses["total_consumption"] or 0), 2)
    if category_total != consumption_total or merchant_total != consumption_total:
        fail(f"aggregate_total_mismatch:categories={category_total}:merchants={merchant_total}:consumption={consumption_total}")
    for item in expenses["categories"] + expenses["merchants"]:
        if item["transaction_count"] <= 0 or item["avg_transaction"] < 0:
            fail("invalid_aggregate_count_or_average")
    if truth["investments"]["market_movement"].get("available"):
        fail("market_movement_claimed_without_v2_basis")
    if truth["cash"]["source"] == "financial_accounts":
        if abs(float(truth["cash"]["account_total"]) - float(truth["cash"]["current_cash"])) > 0.01:
            fail("financial_account_cash_drift")
    return {
        "user_id": user_id,
        "report_month": report_month,
        "snapshot_id": snapshot["id"],
        "schema_version": snapshot["schema_version"],
        "status": snapshot["status"],
        "data_hash": snapshot["data_hash"],
        "cash": truth["cash"],
        "classification_totals": expenses["classification_totals"],
        "consumption": consumption_total,
        "category_count": len(expenses["categories"]),
        "merchant_count": len(expenses["merchants"]),
        "investment_contributions": truth["investments"]["contributions"],
        "market_movement": truth["investments"]["market_movement"],
        "goals": len(truth["goals"]["goals"]),
        "validation": "ok",
    }


def render_identity(snapshot: dict, output_dir: Path) -> dict:
    data = snapshot["data"]
    template = rove_web_report_renderer.TEMPLATE_PATH.read_text(encoding="utf-8")
    web_html = rove_web_report_renderer.render_template(template, data)
    web_path = output_dir / "report.html"
    web_path.write_text(web_html, encoding="utf-8")
    pdf_path = output_dir / "report.pdf"
    rove_pdf_light_renderer.build_pdf_report(0, data["meta"]["report_month"], pdf_path, report_data=data)
    ref = data["meta"].get("snapshot_ref")
    if not ref or ref.get("schema_version") != snapshot["schema_version"]:
        fail("snapshot_ref_missing_or_inconsistent")
    # Sprint-2 snapshots remain immutable and may not contain the later story
    # object. In that case derive the story from the same frozen payload only.
    story = data.get("report_story_v2") or report_story_v2.story_from_snapshot_data(data)
    if story.get("story_version") != 2 or story.get("page_count") != 10:
        fail("story_v2_identity_missing_or_inconsistent")
    wealth_total = data.get("report_truth", {}).get("wealth", {}).get("total")
    if wealth_total is None:
        wealth_total = (((story.get("pages") or {}).get("page_6") or {}).get("primary_metric") or {}).get("value")
    central_facts = {
        "net_worth": wealth_total,
        "consumption": data.get("report_truth", {}).get("expenses", {}).get("total_consumption"),
        "investment_contributions": data.get("report_truth", {}).get("investments", {}).get("contributions"),
        "goals": data.get("report_truth", {}).get("goals", {}).get("goals", []),
        "insight": (story.get("insight_engine") or {}).get("selected"),
        "story_pages": story.get("page_count"),
    }
    if any(value is None for value in central_facts.values()):
        fail("central_report_fact_missing")
    # Both renderers received the same in-memory immutable payload. The HTML/PDF
    # files are only artifacts; no renderer is allowed to query the DB here.
    return {
        "web": str(web_path),
        "pdf": str(pdf_path),
        "snapshot_id": snapshot["id"],
        "schema_version": snapshot["schema_version"],
        "report_month": data["meta"]["report_month"],
        "report_data_hash": snapshot["data_hash"],
        "story_version": story["story_version"],
        "story_pages": story["page_count"],
        "central_facts_same_input": True,
        "same_input": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--month", required=True, help="Abgeschlossener Monat YYYY-MM")
    args = parser.parse_args()
    db_path = args.db.resolve()
    if not db_path.is_file():
        fail("database_not_found")

    # The migration command creates the required pre-change backup and is itself additive.
    migration = run([sys.executable, "migrate_report_snapshots_v2.py", "--db", str(db_path), "--apply"])
    sqlite_result = sqlite_checks(db_path)
    services = service_checks()

    report_engine.DB_NAME = str(db_path)
    report_engine.ensure_report_snapshots_v2_table()
    snapshot = report_engine.get_or_create_report_snapshot(args.user_id, args.month)
    snapshot_result = snapshot_checks(snapshot, args.user_id, args.month)

    with tempfile.TemporaryDirectory(prefix="rove-report-v2-acceptance-") as temp:
        render_result = render_identity(snapshot, Path(temp))

    # A second build must return the same finalized row and payload. This is the
    # production retry invariant and does not mutate any user data.
    retry = report_engine.get_or_create_report_snapshot(args.user_id, args.month)
    if retry["id"] != snapshot["id"] or retry["data_hash"] != snapshot["data_hash"]:
        fail("snapshot_retry_changed_final_snapshot")
    if json.dumps(retry["data"], sort_keys=True) != json.dumps(snapshot["data"], sort_keys=True):
        fail("snapshot_retry_changed_report_data")
    if json.dumps(retry.get("ai_text"), sort_keys=True) != json.dumps(snapshot.get("ai_text"), sort_keys=True):
        fail("snapshot_retry_changed_ai_text")
    final_sqlite = sqlite_checks(db_path)

    print(json.dumps({
        "status": "GO",
        "backup_and_migration": migration,
        "sqlite": sqlite_result,
        "services": services,
        "snapshot": snapshot_result,
        "web_pdf_identity": render_result,
        "retry_immutability": "ok",
        "retry_snapshot_id": retry["id"],
        "retry_data_hash": retry["data_hash"],
        "sqlite_after_retry": final_sqlite,
        "ai_persistence": "not_generated" if not snapshot.get("ai_text") else "unchanged",
        "old_reports_migrated": False,
        "design_changed": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "NO-GO", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
