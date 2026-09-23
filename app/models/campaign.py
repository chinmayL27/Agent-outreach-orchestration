"""Campaign configuration (campaign.yaml) as validated Pydantic models."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class Geography(BaseModel):
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


class PracticeSize(BaseModel):
    min_providers: int = 1
    max_providers: int = 30


class ScoringWeights(BaseModel):
    """Weights live in configuration, never in source code."""

    target_specialty: int = 25
    provider_count_in_range: int = 15
    multiple_locations: int = 10
    has_online_booking: int = 10
    no_chatbot: int = 20
    extensive_faq: int = 10
    public_email: int = 10
    faq_threshold: int = 5
    premium_threshold: int = 80
    basic_threshold: int = 60


class ProductConfig(BaseModel):
    name: str = "AI Patient Assistant"
    capabilities: list[str] = Field(
        default_factory=lambda: [
            "answering common patient questions from your website content",
            "routing patients to the right service or location",
            "guiding patients to your existing appointment booking flow",
        ]
    )


class CampaignConfig(BaseModel):
    id: str = "default"
    name: str = "Default campaign"

    source: str = "nppes"
    fixture_path: str | None = None
    csv_path: str | None = None

    specialties: list[str] = Field(default_factory=list)
    geography: Geography = Field(default_factory=Geography)
    practice_size: PracticeSize = Field(default_factory=PracticeSize)
    exclude_keywords: list[str] = Field(
        default_factory=lambda: ["hospital", "university", "medical center", "department of"]
    )
    maximum_leads: int = 50

    website_map: dict[str, str] = Field(default_factory=dict)

    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    product: ProductConfig = Field(default_factory=ProductConfig)

    @field_validator("specialties", "exclude_keywords", mode="before")
    @classmethod
    def _coerce_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value

    @classmethod
    def load(cls, path: str | Path) -> "CampaignConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        # Allow either a top-level mapping or a `campaign:` block.
        data = raw.get("campaign", raw)
        config = cls.model_validate(data)
        if config.fixture_path and not Path(config.fixture_path).is_absolute():
            config.fixture_path = str((Path(path).parent / config.fixture_path).resolve())
        if config.csv_path and not Path(config.csv_path).is_absolute():
            config.csv_path = str((Path(path).parent / config.csv_path).resolve())
        return config
