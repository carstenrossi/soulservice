"""Soul adaptations -- the neuroplasticity layer.

Created manually via CLI now, automatically by the Dream Phase (nightly
consolidation job) in Phase 4.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

ADAPTATION_CATEGORIES = (
    "relationship_depth",
    "topic_stance",
    "behavioral_refinement",
    "shared_reference",
    "emotional_calibration",
)


class SoulAdaptation(SQLModel, table=True):
    __tablename__ = "soul_adaptations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    soul_id: UUID = Field(foreign_key="souls.id")
    category: str
    content_encrypted: bytes
    content_nonce: bytes
    confidence: float = 0.5
    source: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    superseded_by: UUID | None = None
    status: str = "active"
