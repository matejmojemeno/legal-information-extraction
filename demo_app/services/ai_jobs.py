"""In-memory background jobs for SPP analysis and optional BRL review.

In the thesis terminology, the base extraction/linking result is produced by
the Structured Precision Pipeline (SPP), while the optional model-backed hard
case routes form the Bounded Review Layer (BRL). Function names keep the older
``ai_*`` wording because they are implementation plumbing for the local demo.
"""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from typing import Any
from uuid import uuid4

from demo_app.services.pdf_extract import extract_text_from_pdf_bytes
from demo_app.services.docref_llm import _apply_extraction_presence_consistency, enrich_document_reference_tasks
from demo_app.services.law_llm import enrich_law_anomalies
from demo_app.services.pipeline_runner import build_demo_result, prepare_demo_state


_RESULTS: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


def create_result_bundle(
    *,
    base_state: dict[str, Any],
    initial_result: dict[str, Any],
    ai_enabled: bool,
) -> str:
    result_id = uuid4().hex
    ai_job = None
    if ai_enabled:
        anomalies = list(base_state.get("law_anomalies") or [])
        document_tasks = list(base_state.get("document_ai_tasks") or [])
        ai_job = {
            "status": "pending",
            "phase": "law",
            "processed": 0,
            "total": len(anomalies) + len(document_tasks),
            "error": None,
            "started_at_unix": int(time.time()),
            "finished_at_unix": None,
            "model": None,
            "law": {
                "status": "pending",
                "processed": 0,
                "total": len(anomalies),
            },
            "document": {
                "status": "pending",
                "processed": 0,
                "total": len(document_tasks),
            },
        }
    with _LOCK:
        _RESULTS[result_id] = {
            "result_id": result_id,
            "base_state": base_state,
            "current_result": initial_result,
            "ai_rows": [],
            "document_ai_rows": [],
            "ai_job": ai_job,
        }
    return result_id


def create_pending_result_bundle(*, document_name: str, ai_enabled: bool) -> str:
    result_id = uuid4().hex
    with _LOCK:
        _RESULTS[result_id] = {
            "result_id": result_id,
            "base_state": None,
            "current_result": None,
            "document_name": document_name,
            "ai_rows": [],
            "document_ai_rows": [],
            "ai_job": {
                "status": "pending" if ai_enabled else "disabled",
                "phase": "queued" if ai_enabled else "disabled",
                "processed": 0,
                "total": 0,
                "error": None,
                "started_at_unix": None,
                "finished_at_unix": None,
                "model": None,
                "law": {"status": "pending" if ai_enabled else "disabled", "processed": 0, "total": 0},
                "document": {"status": "pending" if ai_enabled else "disabled", "processed": 0, "total": 0},
            } if ai_enabled else None,
            "deterministic_job": {
                "status": "pending",
                "phase": "queued",
                "progress": 5,
                "error": None,
                "started_at_unix": int(time.time()),
                "finished_at_unix": None,
            },
        }
    return result_id


def get_result_bundle(result_id: str) -> dict[str, Any] | None:
    with _LOCK:
        bundle = _RESULTS.get(result_id)
        return deepcopy(bundle) if bundle is not None else None


def start_deterministic_analysis(
    result_id: str,
    *,
    pdf_bytes: bytes,
    document_name: str,
    ai_enabled: bool,
    model: str | None = None,
) -> None:
    thread = threading.Thread(
        target=_run_deterministic_analysis,
        kwargs={
            "result_id": result_id,
            "pdf_bytes": pdf_bytes,
            "document_name": document_name,
            "ai_enabled": ai_enabled,
            "model": model,
        },
        daemon=True,
        name=f"demo-det-{result_id[:8]}",
    )
    thread.start()


def start_ai_enrichment(result_id: str, *, model: str | None = None) -> None:
    thread = threading.Thread(
        target=_run_ai_enrichment,
        kwargs={"result_id": result_id, "model": model},
        daemon=True,
        name=f"demo-ai-{result_id[:8]}",
    )
    thread.start()


def _run_deterministic_analysis(
    *,
    result_id: str,
    pdf_bytes: bytes,
    document_name: str,
    ai_enabled: bool,
    model: str | None = None,
) -> None:
    def update_progress(phase: str, progress: int) -> None:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None:
                return
            current["deterministic_job"]["phase"] = phase
            current["deterministic_job"]["progress"] = progress

    try:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None:
                return
            current["deterministic_job"]["status"] = "running"
            current["deterministic_job"]["phase"] = "extract_text"
            current["deterministic_job"]["progress"] = 18

        text = extract_text_from_pdf_bytes(pdf_bytes)

        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None:
                return
            current["deterministic_job"]["phase"] = "prepare_state"
            current["deterministic_job"]["progress"] = 46

        base_state = prepare_demo_state(
            text=text,
            document_name=document_name,
            progress_callback=update_progress,
        )

        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None:
                return
            current["deterministic_job"]["phase"] = "build_result"
            current["deterministic_job"]["progress"] = 96

        initial_result = build_demo_result(base_state)

        ai_job = None
        if ai_enabled:
            anomalies = list(base_state.get("law_anomalies") or [])
            document_tasks = list(base_state.get("document_ai_tasks") or [])
            ai_job = {
                "status": "pending",
                "phase": "law",
                "processed": 0,
                "total": len(anomalies) + len(document_tasks),
                "error": None,
                "started_at_unix": int(time.time()),
                "finished_at_unix": None,
                "model": None,
                "law": {
                    "status": "pending",
                    "processed": 0,
                    "total": len(anomalies),
                },
                "document": {
                    "status": "pending",
                    "processed": 0,
                    "total": len(document_tasks),
                },
            }

        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None:
                return
            current["base_state"] = base_state
            current["current_result"] = initial_result
            current["deterministic_job"]["status"] = "completed"
            current["deterministic_job"]["phase"] = "done"
            current["deterministic_job"]["progress"] = 100
            current["deterministic_job"]["finished_at_unix"] = int(time.time())
            current["ai_job"] = ai_job if ai_enabled else None

        if ai_enabled:
            start_ai_enrichment(result_id, model=model)
    except Exception as exc:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None:
                return
            current["deterministic_job"]["status"] = "failed"
            current["deterministic_job"]["error"] = str(exc)
            current["deterministic_job"]["finished_at_unix"] = int(time.time())


def _run_ai_enrichment(*, result_id: str, model: str | None = None) -> None:
    bundle = get_result_bundle(result_id)
    if bundle is None:
        return

    anomalies = list(bundle["base_state"].get("law_anomalies") or [])
    document_tasks = list(bundle["base_state"].get("document_ai_tasks") or [])
    with _LOCK:
        current = _RESULTS.get(result_id)
        if current is None or current.get("ai_job") is None:
            return
        current["ai_job"]["status"] = "running"
        current["ai_job"]["phase"] = "law" if anomalies else "document"
        current["ai_job"]["model"] = model
        if not anomalies:
            current["ai_job"]["law"]["status"] = "completed"
        else:
            current["ai_job"]["law"]["status"] = "running"
        if not document_tasks:
            current["ai_job"]["document"]["status"] = "completed"

    if not anomalies and not document_tasks:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current and current.get("ai_job") is not None:
                current["ai_job"]["status"] = "completed"
                current["ai_job"]["finished_at_unix"] = int(time.time())
        return

    def on_law_progress(row: dict[str, Any]) -> None:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None or current.get("ai_job") is None:
                return
            current["ai_rows"].append(row)
            current["ai_job"]["law"]["processed"] = len(current["ai_rows"])
            current["ai_job"]["processed"] = len(current["ai_rows"]) + len(current["document_ai_rows"])
            current["current_result"] = build_demo_result(
                current["base_state"],
                ai_rows=current["ai_rows"],
                document_ai_rows=current["document_ai_rows"],
            )

    def on_document_progress(row: dict[str, Any]) -> None:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None or current.get("ai_job") is None:
                return
            current["document_ai_rows"].append(row)
            consistent_rows = _apply_extraction_presence_consistency(list(current["document_ai_rows"]))
            current["document_ai_rows"] = consistent_rows
            current["ai_job"]["document"]["processed"] = len(consistent_rows)
            current["ai_job"]["processed"] = len(current["ai_rows"]) + len(consistent_rows)
            current["current_result"] = build_demo_result(
                current["base_state"],
                ai_rows=current["ai_rows"],
                document_ai_rows=consistent_rows,
            )

    try:
        rows: list[dict[str, Any]] = []
        if anomalies:
            rows = enrich_law_anomalies(
                anomalies,
                model=model,
                progress_callback=on_law_progress,
            )
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None or current.get("ai_job") is None:
                return
            current["ai_rows"] = rows
            current["ai_job"]["law"]["processed"] = len(rows)
            current["ai_job"]["law"]["status"] = "completed"
            current["ai_job"]["processed"] = len(rows) + len(current["document_ai_rows"])
            current["current_result"] = build_demo_result(
                current["base_state"],
                ai_rows=rows,
                document_ai_rows=current["document_ai_rows"],
            )

        document_rows: list[dict[str, Any]] = []
        if document_tasks:
            with _LOCK:
                current = _RESULTS.get(result_id)
                if current is None or current.get("ai_job") is None:
                    return
                current["ai_job"]["phase"] = "document"
                current["ai_job"]["document"]["status"] = "running"
            document_rows = enrich_document_reference_tasks(
                document_tasks,
                model=model,
                progress_callback=on_document_progress,
            )
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None or current.get("ai_job") is None:
                return
            current["document_ai_rows"] = document_rows
            current["document_ai_rows"] = _apply_extraction_presence_consistency(list(document_rows))
            current["ai_job"]["document"]["processed"] = len(current["document_ai_rows"])
            current["ai_job"]["document"]["status"] = "completed"
            current["ai_job"]["processed"] = len(current["ai_rows"]) + len(current["document_ai_rows"])
            current["current_result"] = build_demo_result(
                current["base_state"],
                ai_rows=current["ai_rows"],
                document_ai_rows=current["document_ai_rows"],
            )
            current["ai_job"]["status"] = "completed"
            current["ai_job"]["phase"] = "done"
            current["ai_job"]["finished_at_unix"] = int(time.time())
    except Exception as exc:
        with _LOCK:
            current = _RESULTS.get(result_id)
            if current is None or current.get("ai_job") is None:
                return
            current["ai_job"]["status"] = "failed"
            current["ai_job"]["error"] = str(exc)
            current["ai_job"]["finished_at_unix"] = int(time.time())
