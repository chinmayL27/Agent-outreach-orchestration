"""FastAPI app serving /demo/{lead_id} from the database.

Optional: the pipeline writes standalone HTML to artifacts/demos/, so video
recording and review work without a running server.  The server exists for
sharing a live link in the outreach email.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.db import init_db, session_scope
from app.demo.renderer import render_demo_html
from app.models.outreach import DemoArtifact
from app.models.schemas import DemoConfig

app = FastAPI(title="Outreach demo app", docs_url=None, redoc_url=None)


def _load_config(lead_id: str) -> DemoConfig:
    init_db()
    with session_scope() as session:
        artifact = (
            session.query(DemoArtifact).filter(DemoArtifact.lead_id == lead_id).one_or_none()
        )
        if artifact is None or not artifact.config:
            raise HTTPException(status_code=404, detail="demo not found for this lead")
        return DemoConfig.model_validate(artifact.config)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/demo/{lead_id}", response_class=HTMLResponse)
def demo_page(lead_id: str) -> HTMLResponse:
    return HTMLResponse(render_demo_html(_load_config(lead_id)))


@app.get("/demo/{lead_id}/config")
def demo_config(lead_id: str) -> JSONResponse:
    return JSONResponse(_load_config(lead_id).model_dump())
