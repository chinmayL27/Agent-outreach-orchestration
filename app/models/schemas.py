"""Pydantic schemas used at the LLM boundary and for generated artifacts.

These are the typed contracts between workers: every stage consumes structured
data and produces structured data.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Fact(BaseModel):
    """A single evidence-backed claim about the clinic."""

    id: str
    fact: str
    source: str


class ClinicSummary(BaseModel):
    name: str
    specialty: list[str] = Field(default_factory=list)
    location: str = ""
    provider_names: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    locations_count: int = 1
    has_online_booking: bool | None = None
    has_chatbot: bool | None = None


class ProductSummary(BaseModel):
    name: str
    capabilities: list[str]


class LeadPacket(BaseModel):
    """The *only* clinic information a personalization prompt ever sees."""

    lead_id: str
    clinic: ClinicSummary
    facts: list[Fact] = Field(default_factory=list)
    product: ProductSummary
    sender_name: str = ""
    sender_company: str = ""


class PersonalizationOutput(BaseModel):
    """Structured output demanded from the LLM - one call per lead."""

    sales_angle: str
    pain_point: str = ""
    relevant_use_cases: list[str] = Field(default_factory=list)
    demo_questions: list[str] = Field(default_factory=list)
    email_subject: str
    email_body: str
    video_intro: str
    video_outro: str
    claims_used: list[str] = Field(default_factory=list)

    @field_validator("demo_questions")
    @classmethod
    def _at_least_two_questions(cls, value: list[str]) -> list[str]:
        if len(value) < 2:
            raise ValueError("demo_questions must contain at least two patient questions")
        return value[:4]


class DemoConfig(BaseModel):
    """JSON that configures the generic demo app - no per-lead code."""

    lead_id: str
    business_name: str
    specialty: str = ""
    location: str = ""
    accent_color: str = "#0f766e"
    services: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    intro_text: str = ""
    outro_text: str = ""
    cta_text: str = "Reply to the email if you'd like this built against your real workflow."
    disclaimer: str = (
        "Demonstration only - sample assistant built from public website information. "
        "Not medical advice. No patient data is used."
    )


class VideoSegment(BaseModel):
    kind: str  # intro | question | benefit | cta
    seconds: float
    text: str = ""
    answer: str = ""


class VideoScript(BaseModel):
    lead_id: str
    target_seconds: float = 60.0
    segments: list[VideoSegment]

    @property
    def total_seconds(self) -> float:
        return sum(segment.seconds for segment in self.segments)
