"""Integration tests: website -> facts -> packet -> personalization -> demo."""

from __future__ import annotations

import pytest

from app.demo.config_generator import build_demo_config, synthesize_answer
from app.demo.renderer import render_demo_html
from app.enrichment.enricher import enrich_website
from app.llm.base import LLMError
from app.llm.template import TemplateProvider
from app.models.evidence import Evidence
from app.models.lead import Lead
from app.models.outreach import Personalization
from app.models.schemas import PersonalizationOutput
from app.personalization.generator import check_grounding, generate_personalization
from app.personalization.lead_packet import build_packet
from app.util.http import Fetcher


def test_sample_website_becomes_an_enriched_lead_with_sourced_evidence(settings, site_url):
    with Fetcher(settings) as fetcher:
        outcome = enrich_website(
            site_url("abc-dermatology"), "ABC Dermatology, P.C.", settings, fetcher
        )

    assert outcome.ok and outcome.pages_crawled >= 5
    fields = outcome.fields
    assert "Skin Cancer Screening" in fields["services"]
    assert "Mohs Surgery" in fields["services"]
    assert fields["has_chatbot"] is False
    assert fields["has_online_booking"] is True
    assert len(fields["faq_questions"]) >= 5
    assert fields["emails"][0] == "info@abcdermatology.example"  # info@ outranks billing@
    assert sorted(fields["locations"]) == ["Campbell, CA", "San Jose, CA"]
    assert len(fields["provider_names"]) == 4

    # Every stored claim carries the page it came from.
    assert outcome.evidence
    for item in outcome.evidence:
        assert item.source_url.startswith("file://")
        assert item.value


def test_existing_chat_widget_is_detected(settings, site_url):
    with Fetcher(settings) as fetcher:
        outcome = enrich_website(
            site_url("bayview-family-medicine"), "Bayview Family Medicine", settings, fetcher
        )
    assert outcome.fields["has_chatbot"] is True
    assert any(item.attribute == "chatbot" for item in outcome.evidence)


def _lead_with_evidence(session, settings, site_url, campaign):
    lead = Lead(
        organization_name="ABC Dermatology, P.C.",
        dedupe_key="test:abc",
        website=site_url("abc-dermatology"),
        specialty=["Dermatology"],
        city="San Jose",
        state="CA",
        phone="(408) 555-0142",
    )
    session.add(lead)
    session.flush()
    with Fetcher(settings) as fetcher:
        outcome = enrich_website(lead.website, lead.organization_name, settings, fetcher)
    for key, value in outcome.fields.items():
        if value not in (None, [], ""):
            setattr(lead, key, value)
    for item in outcome.evidence:
        session.add(
            Evidence(
                lead_id=lead.id,
                attribute=item.attribute,
                value=item.value,
                source_url=item.source_url,
                page_title=item.page_title,
            )
        )
    session.flush()
    return lead


def test_lead_packet_only_exposes_evidence_backed_facts(session, settings, site_url, campaign):
    lead = _lead_with_evidence(session, settings, site_url, campaign)
    evidence = session.query(Evidence).filter(Evidence.lead_id == lead.id).all()
    packet = build_packet(lead, evidence, campaign, settings)

    assert packet.clinic.name == "ABC Dermatology, P.C."
    assert packet.facts, "packet must carry facts"
    for fact in packet.facts:
        assert fact.source.startswith("file://")
    # Internal-only attributes never reach the model.
    assert not any(fact.fact.startswith("public_email") for fact in packet.facts)


def test_packet_generates_grounded_personalization(session, settings, site_url, campaign):
    lead = _lead_with_evidence(session, settings, site_url, campaign)
    evidence = session.query(Evidence).filter(Evidence.lead_id == lead.id).all()
    packet = build_packet(lead, evidence, campaign, settings)

    output, report = generate_personalization(packet, TemplateProvider(), settings)
    assert report.ok
    assert 40 <= len(output.email_body.split()) <= 160
    assert output.claims_used
    assert set(output.claims_used) <= {fact.id for fact in packet.facts}
    assert "revolutionary" not in output.email_body.lower()


def test_grounding_check_rejects_hype_and_invented_metrics(settings):
    from tests.test_state_and_llm import packet as sample_packet

    packet = sample_packet()
    bad = PersonalizationOutput(
        sales_angle="Our revolutionary AI delivers a guaranteed ROI of 300%.",
        email_subject="s",
        email_body="word " * 60,
        video_intro="i",
        video_outro="o",
        demo_questions=["a?", "b?"],
        claims_used=["f1"],
    )
    report = check_grounding(bad, packet, settings)
    assert not report.ok
    assert any("revolutionary" in problem for problem in report.problems)


def test_grounding_check_rejects_unknown_evidence_ids(settings):
    from tests.test_state_and_llm import packet as sample_packet

    packet = sample_packet()
    output = PersonalizationOutput(
        sales_angle="a",
        email_subject="s",
        email_body="word " * 60,
        video_intro="i",
        video_outro="o",
        demo_questions=["a?", "b?"],
        claims_used=["made-up-id"],
    )
    report = check_grounding(output, packet, settings)
    assert not report.ok
    assert any("unknown evidence" in problem for problem in report.problems)


def test_personalization_raises_when_grounding_cannot_be_satisfied(settings):
    from tests.test_state_and_llm import packet as sample_packet

    class HypeProvider(TemplateProvider):
        name = "hype"

        def generate_structured(self, prompt, schema, context=None):
            return PersonalizationOutput(
                sales_angle="A revolutionary approach",
                email_subject="s",
                email_body="word " * 60,
                video_intro="i",
                video_outro="o",
                demo_questions=["a?", "b?"],
                claims_used=["f1"],
            )

    with pytest.raises(LLMError):
        generate_personalization(sample_packet(), HypeProvider(), settings)


def test_demo_config_and_page_are_built_from_the_lead(session, settings, site_url, campaign):
    lead = _lead_with_evidence(session, settings, site_url, campaign)
    personalization = Personalization(
        lead_id=lead.id,
        sales_angle="angle",
        demo_questions=["Do you offer Mohs Surgery?", "How do I book an appointment?"],
        email_subject="subject",
        email_body="body",
        video_intro="intro",
        video_outro="outro",
    )
    config = build_demo_config(lead, personalization, settings)
    assert config.business_name == "ABC Dermatology, P.C."
    assert config.suggested_questions == personalization.demo_questions
    assert set(config.answers) == set(personalization.demo_questions)
    assert "Mohs Surgery" in config.answers["Do you offer Mohs Surgery?"]

    html = render_demo_html(config)
    assert "ABC Dermatology, P.C." in html
    assert "Not medical advice" in html
    assert "window.demo" in html


def test_demo_answers_never_give_clinical_advice(settings, site_url, session, campaign):
    lead = _lead_with_evidence(session, settings, site_url, campaign)
    answer = synthesize_answer("Should I get this mole checked?", lead)
    assert "can't give medical advice" in answer.lower()


def test_demo_page_escapes_hostile_website_text(session, settings, site_url, campaign):
    lead = _lead_with_evidence(session, settings, site_url, campaign)
    lead.organization_name = '<script>alert("xss")</script> Clinic'
    lead.services = ['</script><img src=x onerror=alert(1)>']
    personalization = Personalization(
        lead_id=lead.id,
        sales_angle="angle",
        demo_questions=["Do you offer care?", "Where are you?"],
        email_subject="s",
        email_body="b",
        video_intro="i",
        video_outro="o",
    )
    html = render_demo_html(build_demo_config(lead, personalization, settings))
    # The hostile markup survives only as inert, escaped text.
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert" in html
