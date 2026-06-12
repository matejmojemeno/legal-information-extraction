"""FastAPI demo app for showcasing legal reference extraction on uploaded PDFs."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from demo_app.config import (
    DEMO_CORPUS_ROOT,
    DEMO_MAX_TARGET_PREVIEW_CHARS,
    DEMO_METADATA_PATH,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from demo_app.services.pdf_extract import PdfExtractionError, extract_text_from_pdf_bytes
from demo_app.services.ai_jobs import (
    create_pending_result_bundle,
    get_result_bundle,
    start_deterministic_analysis,
)
from demo_app.services.view_model import source_label
from src.document_metadata import load_document_dates_index

app = FastAPI(title="Legal Reference Demo", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@lru_cache(maxsize=1)
def _document_metadata_index():
    return load_document_dates_index(str(DEMO_METADATA_PATH))


def _render_partial(template_name: str, **context: object) -> str:
    template = templates.env.get_template(template_name)
    return template.render(**context)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def upload_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "error": None,
            "result": None,
            "ai_job": None,
            "result_id": None,
        },
    )


@app.post("/analyze", response_class=HTMLResponse)
async def analyze_pdf(
    request: Request,
    file: UploadFile = File(...),
    use_ai: bool = Form(False),
) -> HTMLResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "error": "Please upload a PDF file.",
                "result": None,
                "ai_job": None,
                "result_id": None,
            },
            status_code=400,
        )

    pdf_bytes = await file.read()
    try:
        result_id = create_pending_result_bundle(document_name=file.filename, ai_enabled=use_ai)
        start_deterministic_analysis(
            result_id,
            pdf_bytes=pdf_bytes,
            document_name=file.filename,
            ai_enabled=use_ai,
        )
    except PdfExtractionError as exc:
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "error": str(exc),
                "result": None,
                "ai_job": None,
                "result_id": None,
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "analyzing.html",
        {
            "error": None,
            "result_id": result_id,
            "document_name": file.filename,
        },
    )


@app.get("/results/{result_id}", response_class=HTMLResponse)
def result_page(request: Request, result_id: str) -> HTMLResponse:
    bundle = get_result_bundle(result_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Result was not found.")

    deterministic_job = bundle.get("deterministic_job") or {}
    if deterministic_job.get("status") != "completed" or bundle.get("current_result") is None:
        return templates.TemplateResponse(
            request,
            "analyzing.html",
            {
                "error": deterministic_job.get("error"),
                "result_id": result_id,
                "document_name": bundle.get("document_name") or "Uploaded document",
            },
        )

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "error": None,
            "result": bundle["current_result"],
            "ai_job": bundle.get("ai_job"),
            "result_id": result_id,
        },
    )


@app.get("/api/results/{result_id}/ai-status")
def ai_status(result_id: str) -> JSONResponse:
    bundle = get_result_bundle(result_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Result was not found.")

    ai_job = bundle.get("ai_job")
    result = bundle["current_result"]
    payload = {
        "result_id": result_id,
        "ai_job": ai_job,
        "html": {
            "stats": _render_partial("partials/stats_row.html", result=result),
            "ai_status": _render_partial("partials/ai_status.html", ai_job=ai_job, result=result),
            "text_panel": _render_partial("partials/text_panel.html", result=result),
            "law_list": _render_partial("partials/law_reference_list.html", result=result),
            "document_list": _render_partial("partials/document_reference_list.html", result=result),
        },
    }
    return JSONResponse(payload)


@app.get("/api/results/{result_id}/status")
def result_status(result_id: str) -> JSONResponse:
    bundle = get_result_bundle(result_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Result was not found.")

    deterministic_job = bundle.get("deterministic_job") or {}
    payload = {
        "result_id": result_id,
        "document_name": bundle.get("document_name"),
        "deterministic_job": deterministic_job,
        "ready": deterministic_job.get("status") == "completed" and bundle.get("current_result") is not None,
        "redirect_url": f"/results/{result_id}",
    }
    return JSONResponse(payload)


@app.get("/target", response_class=HTMLResponse)
def target_document_page(
    request: Request,
    source: str = Query(...),
    document_id: str = Query(...),
) -> HTMLResponse:
    safe_source = source.strip()
    safe_document_id = document_id.strip()
    target_path = (DEMO_CORPUS_ROOT / safe_source / safe_document_id).resolve()
    expected_root = DEMO_CORPUS_ROOT.resolve()
    if expected_root not in target_path.parents or not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="Target document was not found in the local corpus.")

    text = target_path.read_text(encoding="utf-8")
    preview = text[:DEMO_MAX_TARGET_PREVIEW_CHARS]
    truncated = len(text) > len(preview)
    metadata = _document_metadata_index().get((safe_source, safe_document_id)) or {}
    return templates.TemplateResponse(
        request,
        "target_document.html",
        {
            "source": safe_source,
            "source_label": source_label(safe_source),
            "document_id": safe_document_id,
            "target_path": str(target_path),
            "judicate_name": metadata.get("judicate_name"),
            "decision_date_iso": metadata.get("decision_date_iso"),
            "judicate_iri": metadata.get("judicate_iri"),
            "text_preview": preview,
            "truncated": truncated,
        },
    )
