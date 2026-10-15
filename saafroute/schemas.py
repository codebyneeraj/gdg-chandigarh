"""Typed schemas shared by the API, the model adapters and the tests.

`PhotoAnalysis` is the strict contract for the vision model. Anything the model
returns is validated against it; nothing downstream ever sees unvalidated output.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WasteType(str, Enum):
    """Dominant material, aligned with the segregation streams of India's Solid Waste
    Management Rules 2016 (wet / dry / domestic hazardous / C&D), plus bulky items."""

    mixed = "mixed"                              # unsegregated household / street waste
    dry_recyclable = "dry_recyclable"            # plastic, paper, cardboard, metal, glass
    wet_organic = "wet_organic"                  # food, garden and other biodegradable waste
    construction_debris = "construction_debris"  # rubble, bricks, C&D waste
    bulky = "bulky"                              # furniture, mattresses, large items
    e_waste = "e_waste"                          # appliances, electronics
    hazardous = "hazardous"                      # medical, chemical, batteries, sanitary
    none = "none"                                # no waste visible


class Kind(str, Enum):
    overflowing_bin = "overflowing_bin"  # waste spilling from / piled around a container
    illegal_dump = "illegal_dump"        # pile dumped where no container exists
    litter = "litter"                    # scattered small items
    clean = "clean"                      # nothing to collect


class PhotoAnalysis(BaseModel):
    """What the vision model must return. Extra keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    waste_type: WasteType
    kind: Kind
    severity: int = Field(ge=1, le=5, description="1 = a few items, 5 = large pile blocking road/public health risk")
    confidence: float = Field(ge=0.0, le=1.0)
    privacy_flag: bool = Field(description="true if a recognisable face or vehicle number plate is visible")
    reason: str = Field(min_length=1, max_length=300)


class AnalysisResult(BaseModel):
    """Outcome of running the photo pipeline, including failures."""

    status: Literal["ok", "needs_review"]
    analysis: PhotoAnalysis | None = None
    model: str
    attempts: int
    latency_s: float
    error: str | None = None


Lang = Literal["en", "hi", "pa"]


class TicketDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=120)
    body: str = Field(min_length=10, max_length=1200)


class ApproveRequest(BaseModel):
    official: str = Field(min_length=2, max_length=60)
    title: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=1200)


class RejectRequest(BaseModel):
    official: str = Field(min_length=2, max_length=60)
    note: str | None = Field(default=None, max_length=300)


class RedraftRequest(BaseModel):
    lang: Lang


# JSON schema handed to models that support constrained decoding (Ollama `format`).
PHOTO_JSON_SCHEMA = PhotoAnalysis.model_json_schema()
