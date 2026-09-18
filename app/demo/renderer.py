"""Render the generic demo app for one lead (static HTML, no server required)."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings, get_settings
from app.models.schemas import DemoConfig

TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)


def _initials(name: str) -> str:
    parts = [part for part in name.split() if part[:1].isalnum()]
    return "".join(part[0].upper() for part in parts[:2]) or "AI"


def _safe_json(config: DemoConfig) -> str:
    """Embed config in a <script> tag without letting site text break out."""
    payload = json.dumps(config.model_dump(), ensure_ascii=False)
    return payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_demo_html(config: DemoConfig) -> str:
    template = _env.get_template("demo.html.j2")
    return template.render(
        config=config, initials=_initials(config.business_name), config_json=_safe_json(config)
    )


def write_demo_html(config: DemoConfig, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    settings.ensure_dirs()
    path = settings.demos_dir / f"{config.lead_id}.html"
    path.write_text(render_demo_html(config), encoding="utf-8")
    return path


def demo_url(lead_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return f"{settings.demo_base_url}/demo/{lead_id}"


def demo_file_url(lead_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (settings.demos_dir / f"{lead_id}.html").resolve().as_uri()
