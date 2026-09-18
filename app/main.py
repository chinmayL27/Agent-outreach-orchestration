"""`outreach` - the command line for the whole pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy import func, select

from app.config import PROJECT_ROOT, get_settings
from app.db import db_file_path, init_db, session_scope
from app.llm.factory import get_provider
from app.models.campaign import CampaignConfig
from app.models.evidence import Evidence
from app.models.lead import Lead
from app.models.outreach import EmailDraft, SendRecord
from app.observability.events import stage_counts
from app.orchestration import pipeline
from app.orchestration.state import LeadStatus
from app.outreach import approval, suppression
from app.outreach.sender import get_email_provider

app = typer.Typer(
    add_completion=False,
    help="Local-first healthcare outreach orchestrator (discover -> ... -> review -> send).",
)
suppress_app = typer.Typer(help="Manage the suppression list.")
app.add_typer(suppress_app, name="suppress")

DEFAULT_CAMPAIGN = PROJECT_ROOT / "campaign.yaml"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
    )


def _load_campaign(path: Optional[Path]) -> CampaignConfig:
    config_path = path or DEFAULT_CAMPAIGN
    if not Path(config_path).exists():
        typer.secho(f"campaign file not found: {config_path}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    return CampaignConfig.load(config_path)


def _echo_summary(summary: pipeline.StageSummary) -> None:
    color = typer.colors.GREEN if summary.failed == 0 else typer.colors.YELLOW
    typer.secho(str(summary), fg=color)
    for note in summary.notes[:8]:
        typer.secho(f"    - {note}", fg=typer.colors.YELLOW)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Emit stage event logs.")) -> None:
    _setup_logging(verbose)
    get_settings().ensure_dirs()
    init_db()


# ---------------------------------------------------------------------------
@app.command("init-db")
def init_db_command() -> None:
    """Create the SQLite schema."""
    init_db()
    typer.secho(f"database ready at {db_file_path()}", fg=typer.colors.GREEN)


@app.command()
def discover(
    campaign_file: Optional[Path] = typer.Argument(None, help="campaign.yaml (default: ./campaign.yaml)"),
    city: Optional[str] = typer.Option(None, help="Override campaign city."),
    state: Optional[str] = typer.Option(None, help="Override campaign state."),
    specialty: Optional[str] = typer.Option(None, help="Override campaign specialty."),
    limit: Optional[int] = typer.Option(None, help="Override maximum_leads."),
    source: Optional[str] = typer.Option(None, help="Override source: nppes | fixture | csv."),
) -> None:
    """Find clinics/providers and store them as leads."""
    campaign = _load_campaign(campaign_file)
    if city:
        campaign.geography.city = city
    if state:
        campaign.geography.state = state
    if specialty:
        campaign.specialties = [specialty]
    if limit:
        campaign.maximum_leads = limit
    if source:
        campaign.source = source
    with session_scope() as session:
        _echo_summary(pipeline.discover(session, campaign))


@app.command()
def enrich(
    campaign_file: Optional[Path] = typer.Argument(None),
    limit: Optional[int] = typer.Option(None, help="Max leads to crawl."),
    force: bool = typer.Option(False, "--force", help="Re-crawl leads already enriched."),
) -> None:
    """Crawl each lead's public website and store facts with their sources."""
    campaign = _load_campaign(campaign_file)
    with session_scope() as session:
        _echo_summary(pipeline.enrich(session, campaign, limit=limit, force=force))


@app.command()
def score(
    campaign_file: Optional[Path] = typer.Argument(None),
    force: bool = typer.Option(False, "--force", help="Re-score leads already scored."),
) -> None:
    """Apply the deterministic score and qualify or backlog each lead."""
    campaign = _load_campaign(campaign_file)
    with session_scope() as session:
        _echo_summary(pipeline.score(session, campaign, force=force))


@app.command()
def personalize(
    campaign_file: Optional[Path] = typer.Argument(None),
    limit: Optional[int] = typer.Option(5, help="Max qualified leads to personalize."),
    provider: Optional[str] = typer.Option(None, help="template | claude_cli | ollama | anthropic"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Generate the sales angle, demo questions, email and video copy."""
    campaign = _load_campaign(campaign_file)
    llm = get_provider(provider)
    typer.secho(f"using LLM provider: {llm.name}", fg=typer.colors.BLUE)
    with session_scope() as session:
        _echo_summary(
            pipeline.personalize(session, campaign, provider=llm, limit=limit, force=force)
        )


@app.command()
def demo(
    campaign_file: Optional[Path] = typer.Argument(None),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Write the customized demo config + standalone demo page per lead."""
    campaign = _load_campaign(campaign_file)
    with session_scope() as session:
        _echo_summary(pipeline.build_demos(session, campaign, force=force))


@app.command()
def video(
    campaign_file: Optional[Path] = typer.Argument(None),
    limit: Optional[int] = typer.Option(5, help="Max videos to record."),
    speed: float = typer.Option(1.0, help="Playback speed multiplier (>1 records a shorter video)."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Record the ~60 second personalized demo video."""
    campaign = _load_campaign(campaign_file)
    with session_scope() as session:
        _echo_summary(
            pipeline.render_videos(session, campaign, limit=limit, force=force, speed=speed)
        )


@app.command("email")
def email_command(
    campaign_file: Optional[Path] = typer.Argument(None),
    force: bool = typer.Option(False, "--force", help="Rebuild drafts, including human-edited ones."),
) -> None:
    """Render the outreach email and move the lead into the review queue."""
    campaign = _load_campaign(campaign_file)
    with session_scope() as session:
        _echo_summary(pipeline.build_emails(session, campaign, force=force))


@app.command()
def run(
    campaign_file: Optional[Path] = typer.Argument(None),
    limit: Optional[int] = typer.Option(5, help="Max leads to personalize/record."),
    provider: Optional[str] = typer.Option(None, help="LLM provider override."),
    skip_video: bool = typer.Option(False, "--skip-video", help="Skip the recording stage."),
    speed: float = typer.Option(1.0, help="Video speed multiplier."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Run everything through REVIEW_REQUIRED.  Never sends email."""
    campaign = _load_campaign(campaign_file)
    llm = get_provider(provider)
    typer.secho(f"campaign: {campaign.name}  |  LLM provider: {llm.name}", fg=typer.colors.BLUE)
    with session_scope() as session:
        summaries = pipeline.run_campaign(
            session,
            campaign,
            provider=llm,
            demo_limit=limit,
            skip_video=skip_video,
            video_speed=speed,
            force=force,
        )
    for summary in summaries:
        _echo_summary(summary)
    typer.secho(
        "\nNothing has been sent. Review with `outreach review next`.", fg=typer.colors.BLUE
    )


# ---------------------------------------------------------------------------
review_app = typer.Typer(help="Human approval queue.")
app.add_typer(review_app, name="review")


def _print_review_item(item: approval.ReviewItem) -> None:
    lead = item.lead
    typer.secho(f"\n{lead.organization_name}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Score:     {lead.score} ({lead.tier})   {lead.location_label}")
    typer.echo(f"Recipient: {item.draft.recipient}")
    typer.echo(f"Website:   {lead.website or '-'}")
    typer.echo("\nEvidence:")
    for evidence in item.evidence[:6]:
        typer.echo(f"  - {evidence.attribute}: {evidence.value}  [{evidence.source_url}]")
    typer.echo(f"\nSubject: {item.draft.subject}")
    typer.echo("-" * 68)
    typer.echo(item.draft.body_text)
    typer.echo("-" * 68)
    typer.echo(f"Demo:  {item.demo.html_path if item.demo else '-'}")
    typer.echo(f"Video: {item.video.path if item.video and item.video.path else '-'}")


@review_app.command("list")
def review_list(limit: int = typer.Option(20)) -> None:
    """Show everything waiting for approval."""
    with session_scope() as session:
        items = approval.pending(session, limit=limit)
        if not items:
            typer.secho("review queue is empty", fg=typer.colors.YELLOW)
            return
        for item in items:
            typer.echo(
                f"{item.lead.score:>3}  {item.lead.organization_name:<40} "
                f"{item.draft.recipient:<32} {item.lead.id}"
            )


@review_app.command("next")
def review_next(
    approve_all: bool = typer.Option(False, "--yes", help="Approve without prompting (careful)."),
) -> None:
    """Walk the queue one lead at a time: [A]pprove / [R]eject / [E]dit / [S]kip."""
    with session_scope() as session:
        items = approval.pending(session)
        if not items:
            typer.secho("review queue is empty", fg=typer.colors.YELLOW)
            return
        for item in items:
            _print_review_item(item)
            if approve_all:
                approval.approve(session, item.lead, approved_by="--yes")
                typer.secho("approved", fg=typer.colors.GREEN)
                continue
            choice = typer.prompt("[A]pprove / [R]eject / [E]dit / [S]kip / [Q]uit", default="S")
            action = choice.strip().lower()[:1]
            if action == "a":
                approval.approve(session, item.lead)
                typer.secho("approved", fg=typer.colors.GREEN)
            elif action == "r":
                approval.reject(session, item.lead, typer.prompt("reason", default=""))
                typer.secho("rejected", fg=typer.colors.YELLOW)
            elif action == "e":
                subject = typer.prompt("subject", default=item.draft.subject)
                typer.echo("Paste the new body, end with a single '.' on its own line:")
                lines: list[str] = []
                while True:
                    line = input()
                    if line.strip() == ".":
                        break
                    lines.append(line)
                approval.edit(session, item.lead, subject=subject, body="\n".join(lines) or None)
                typer.secho("edited (still pending approval)", fg=typer.colors.BLUE)
            elif action == "q":
                break


@review_app.command("approve")
def review_approve(lead_id: str) -> None:
    """Approve one lead by id."""
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            typer.secho("no such lead", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        approval.approve(session, lead)
        typer.secho(f"approved {lead.organization_name}", fg=typer.colors.GREEN)


@review_app.command("reject")
def review_reject(lead_id: str, reason: str = typer.Option("", "--reason")) -> None:
    """Reject one lead by id."""
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            typer.secho("no such lead", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        approval.reject(session, lead, reason)
        typer.secho(f"rejected {lead.organization_name}", fg=typer.colors.YELLOW)


# ---------------------------------------------------------------------------
@app.command()
def send(
    campaign_file: Optional[Path] = typer.Argument(None),
    limit: Optional[int] = typer.Option(None, help="Max approved emails to send."),
    provider: Optional[str] = typer.Option(None, help="console | smtp"),
    attach_video: bool = typer.Option(True, help="Attach the rendered video file."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Send APPROVED emails only."""
    campaign = _load_campaign(campaign_file) if campaign_file else None
    email_provider = get_email_provider(provider)
    with session_scope() as session:
        approved = session.execute(
            select(func.count()).select_from(Lead).where(Lead.status == LeadStatus.APPROVED.value)
        ).scalar_one()
        if not approved:
            typer.secho("nothing approved to send", fg=typer.colors.YELLOW)
            return
        if not yes:
            typer.confirm(
                f"Send {approved} approved email(s) via '{email_provider.name}'?", abort=True
            )
        _echo_summary(
            pipeline.send_approved(
                session,
                campaign,
                limit=limit,
                provider=email_provider,
                attach_video=attach_video,
            )
        )


# ---------------------------------------------------------------------------
@suppress_app.command("add")
def suppress_add(
    email: str,
    reason: str = typer.Option("MANUAL_BLOCK", help="UNSUBSCRIBED | BOUNCED | MANUAL_BLOCK | INVALID"),
    note: Optional[str] = typer.Option(None),
) -> None:
    """Add an address to the suppression list."""
    with session_scope() as session:
        suppression.add(session, email, reason=reason, note=note)
    typer.secho(f"suppressed {email} ({reason})", fg=typer.colors.GREEN)


@suppress_app.command("list")
def suppress_list() -> None:
    """Show the suppression list."""
    with session_scope() as session:
        rows = suppression.list_all(session)
        if not rows:
            typer.secho("suppression list is empty", fg=typer.colors.YELLOW)
            return
        for row in rows:
            typer.echo(f"{row.email:<40} {row.reason:<14} {row.note or ''}")


# ---------------------------------------------------------------------------
@app.command()
def leads(
    status: Optional[str] = typer.Option(None, help="Filter by status."),
    limit: int = typer.Option(50),
) -> None:
    """List stored leads."""
    with session_scope() as session:
        query = select(Lead).order_by(Lead.score.desc(), Lead.organization_name).limit(limit)
        if status:
            query = query.where(Lead.status == status.upper())
        rows = list(session.execute(query).scalars())
        if not rows:
            typer.secho("no leads", fg=typer.colors.YELLOW)
            return
        typer.echo(f"{'SCORE':>5}  {'STATUS':<20} {'NAME':<40} {'EMAIL':<28} WEBSITE")
        for lead in rows:
            typer.echo(
                f"{lead.score:>5}  {lead.status:<20} {lead.organization_name[:39]:<40} "
                f"{(lead.primary_email or '-')[:27]:<28} {lead.website or '-'}"
            )


@app.command("show")
def show_lead(lead_id: str) -> None:
    """Show one lead with its evidence and generated artifacts."""
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            typer.secho("no such lead", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        payload = {
            "id": lead.id,
            "organization_name": lead.organization_name,
            "status": lead.status,
            "score": lead.score,
            "tier": lead.tier,
            "website": lead.website,
            "emails": lead.emails,
            "services": lead.services,
            "locations": lead.locations,
            "has_chatbot": lead.has_chatbot,
            "has_online_booking": lead.has_online_booking,
            "score_breakdown": lead.score_breakdown,
            "evidence": [
                {"attribute": e.attribute, "value": e.value, "source": e.source_url}
                for e in session.execute(
                    select(Evidence).where(Evidence.lead_id == lead.id)
                ).scalars()
            ],
        }
        typer.echo(json.dumps(payload, indent=2, default=str))


@app.command()
def stats() -> None:
    """Campaign funnel and per-stage event counts."""
    with session_scope() as session:
        typer.secho("Leads by status", bold=True)
        rows = session.execute(
            select(Lead.status, func.count()).group_by(Lead.status).order_by(func.count().desc())
        ).all()
        for status, count in rows:
            typer.echo(f"  {status:<22} {count}")
        if not rows:
            typer.echo("  (none)")

        typer.secho("\nStage events", bold=True)
        for stage, status, count in stage_counts(session):
            typer.echo(f"  {stage:<16} {status:<10} {count}")

        drafts = session.execute(select(func.count()).select_from(EmailDraft)).scalar_one()
        sent = session.execute(
            select(func.count()).select_from(SendRecord).where(SendRecord.status == "SENT")
        ).scalar_one()
        typer.secho("\nOutreach", bold=True)
        typer.echo(f"  drafts: {drafts}   sent: {sent}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    """Serve the demo app at /demo/{lead_id} (optional; requires the `web` extra)."""
    try:
        import uvicorn
    except ImportError:
        typer.secho("uvicorn is not installed: pip install '.[web]'", fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    uvicorn.run("app.demo.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
