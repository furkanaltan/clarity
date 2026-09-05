"""Heller A4-Hochformat-PDF-Report (Rov.E) via WeasyPrint.

Rendert das helle Design-Template (report_templates/rove_pdf_report.html) mit
denselben Daten wie der Web-Report: der Kontext kommt aus
rove_web_report_renderer.build_render_context() -> Web und PDF teilen eine
einzige Datenquelle (inkl. KI-Texte, kein Drift).

Der Weblink-Renderer bleibt davon voellig unberuehrt.
"""
from __future__ import annotations

from pathlib import Path

import jinja2

APP_DIR = Path(__file__).resolve().parent
PDF_TEMPLATE_PATH = APP_DIR / "report_templates" / "rove_pdf_report.html"


def build_pdf_report(user_id: int, report_month: str, output_path: Path, report_data: dict | None = None) -> Path:
    """Erzeugt das helle PDF fuer einen Nutzer/Monat. Gibt den Ausgabepfad zurueck."""
    # Import lokal, damit ein fehlendes WeasyPrint nur diesen Pfad trifft (nicht den Bot-Start).
    from weasyprint import HTML

    import report_engine
    import rove_web_report_renderer

    data = report_data or report_engine.build_report_data(user_id, report_month)
    context = rove_web_report_renderer.build_render_context(data)

    template = PDF_TEMPLATE_PATH.read_text(encoding="utf-8")
    html_doc = jinja2.Template(template).render(**context)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_doc, base_url=str(PDF_TEMPLATE_PATH.parent)).write_pdf(str(output_path))
    return output_path
