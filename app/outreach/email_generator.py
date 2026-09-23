"""Assemble the outreach email: generated copy + links + CAN-SPAM footer.

CAN-SPAM applies to commercial email including B2B: accurate headers, a
non-deceptive subject, a valid physical postal address, a working opt-out.
The footer is added here, never by the LLM.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models.lead import Lead
from app.models.outreach import Personalization
from app.outreach.publisher import is_publicly_reachable
from app.util.text import word_count


@dataclass
class RenderedEmail:
    recipient: str
    subject: str
    body_text: str
    body_html: str
    word_count: int
    #: True when every asset link in the body is reachable by the recipient.
    links_public: bool = False
    demo_url: str | None = None


class EmailValidationError(RuntimeError):
    pass


def _footer_lines(settings: Settings) -> list[str]:
    lines = [f"{settings.sender_name}", settings.sender_company]
    if settings.sender_postal_address:
        lines.append(settings.sender_postal_address)
    if settings.unsubscribe_mailto:
        lines.append(
            f"Don't want these emails? Reply STOP or email {settings.unsubscribe_mailto} "
            "and you won't be contacted again."
        )
    return [line for line in lines if line]


def render_email(
    lead: Lead,
    personalization: Personalization,
    *,
    demo_url: str | None = None,
    video_note: str | None = None,
    settings: Settings | None = None,
) -> RenderedEmail:
    settings = settings or get_settings()
    recipient = lead.primary_email
    if not recipient:
        raise EmailValidationError(f"{lead.organization_name} has no public contact email")
    if not settings.sender_postal_address:
        raise EmailValidationError(
            "SENDER_POSTAL_ADDRESS is required (CAN-SPAM requires a valid postal address)"
        )
    if not settings.unsubscribe_mailto:
        raise EmailValidationError("UNSUBSCRIBE_MAILTO is required (CAN-SPAM opt-out mechanism)")

    body = personalization.email_body.strip()
    # A localhost or file:// link is worse than no link: it looks broken to the
    # recipient. Only publicly reachable URLs are written into the message.
    public_demo_url = demo_url if is_publicly_reachable(demo_url) else None
    links: list[str] = []
    if public_demo_url:
        links.append(f"Personalized demo: {public_demo_url}")
    if video_note:
        links.append(video_note)

    text_parts = [body]
    if links:
        text_parts.append("\n".join(links))
    text_parts.append("\n".join(_footer_lines(settings)))
    body_text = "\n\n".join(part for part in text_parts if part)

    paragraphs = "".join(
        f"<p>{html.escape(paragraph).replace(chr(10), '<br />')}</p>"
        for paragraph in body.split("\n\n")
        if paragraph.strip()
    )
    link_html = ""
    if public_demo_url:
        link_html += (
            f'<p><a href="{html.escape(public_demo_url, quote=True)}">'
            "See the personalized demo</a></p>"
        )
    if video_note:
        link_html += f"<p>{html.escape(video_note)}</p>"
    footer_html = "<hr />" + "".join(
        f'<p style="color:#6b7280;font-size:12px;margin:2px 0">{html.escape(line)}</p>'
        for line in _footer_lines(settings)
    )
    body_html = (
        '<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'font-size:15px;color:#111827">'
        f"{paragraphs}{link_html}{footer_html}</body></html>"
    )

    subject = personalization.email_subject.strip()
    if not subject:
        raise EmailValidationError("generated email has no subject")

    return RenderedEmail(
        recipient=recipient,
        subject=subject[:150],
        body_text=body_text,
        body_html=body_html,
        word_count=word_count(body),
        links_public=bool(public_demo_url),
        demo_url=public_demo_url,
    )
