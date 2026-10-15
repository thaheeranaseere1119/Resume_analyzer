from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, IdMixin, JSONType, TimestampMixin


class AnalysisRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "analysis_runs"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"),
                                                         index=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True,
                                              nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)  # queued|running|done|failed
    task_id: Mapped[str | None] = mapped_column(String(64))
    methodology: Mapped[str] = mapped_column(String(30), nullable=False)
    weights: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    overall_score: Mapped[float | None] = mapped_column(Float)
    required_coverage: Mapped[float | None] = mapped_column(Float)
    preferred_coverage: Mapped[float | None] = mapped_column(Float)
    semantic_backend: Mapped[str | None] = mapped_column(String(40))
    formatting_findings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    next_actions: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    limitations: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    components: Mapped[list[AnalysisComponent]] = relationship(back_populates="analysis", cascade="all, delete-orphan")
    matches: Mapped[list[RequirementMatch]] = relationship(back_populates="analysis", cascade="all, delete-orphan")


class AnalysisComponent(IdMixin, Base):
    __tablename__ = "analysis_components"
    __table_args__ = (UniqueConstraint("analysis_id", "key", name="uq_analysis_components_key"),)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True,
                                                   nullable=False)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    details: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    analysis: Mapped[AnalysisRun] = relationship(back_populates="components")


class RequirementMatch(IdMixin, Base):
    __tablename__ = "requirement_matches"
    __table_args__ = (UniqueConstraint("analysis_id", "requirement_id", name="uq_requirement_matches_req"),)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True,
                                                   nullable=False)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_requirements.id", ondelete="CASCADE"),
                                                      nullable=False)
    # explicitly_supported | contextually_supported | related | unverified | missing |
    # satisfied | partial | unknown | not_satisfied (experience/education)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_level: Mapped[str | None] = mapped_column(String(20))
    block_stable_id: Mapped[uuid.UUID | None] = mapped_column()
    section_type: Mapped[str | None] = mapped_column(String(30))
    snippet: Mapped[str | None] = mapped_column(Text)
    semantic_score: Mapped[float | None] = mapped_column(Float)
    related_term: Mapped[str | None] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    analysis: Mapped[AnalysisRun] = relationship(back_populates="matches")


class Recommendation(IdMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("analysis_id", "dedupe_key", name="uq_recommendations_dedupe"),)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True,
                                                   nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    target_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"),
                                                         nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    target_section: Mapped[str | None] = mapped_column(String(30))
    target_block_stable_id: Mapped[uuid.UUID | None] = mapped_column()
    target_label: Mapped[str | None] = mapped_column(String(300))
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # insert | replace | rewrite | remove | none
    mode: Mapped[str] = mapped_column(String(30), nullable=False)  # keyword_alignment|grammar|clarity|achievement|summary|insertion|repetition
    original_text: Mapped[str | None] = mapped_column(Text)
    proposed_text: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requirement_keys: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    factual_status: Mapped[str] = mapped_column(String(20), nullable=False)
    factual_claims: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    grammar: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    affected_components: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmation_question: Mapped[str | None] = mapped_column(Text)
    confirmation_subject: Mapped[str | None] = mapped_column(String(80))
    risk_warnings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    learning_suggestion: Mapped[str | None] = mapped_column(Text)
    predicted_score: Mapped[float | None] = mapped_column(Float)
    predicted_delta: Mapped[float | None] = mapped_column(Float)
    predicted_components: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    writer: Mapped[str] = mapped_column(String(20), nullable=False, default="builtin")  # claude | builtin
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|accepted|rejected|superseded|undone
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    applied_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))

    evidence: Mapped[list[RecommendationEvidence]] = relationship(back_populates="recommendation",
                                                                   cascade="all, delete-orphan")

    __mapper_args__ = {"version_id_col": lock_version}


class RecommendationEvidence(IdMixin, Base):
    __tablename__ = "recommendation_evidence"
    recommendation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"),
                                                         index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # resume_block | user_confirmation | jd_requirement
    ref: Mapped[str | None] = mapped_column(String(80))
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    recommendation: Mapped[Recommendation] = relationship(back_populates="evidence")


class RecommendationReview(IdMixin, Base):
    __tablename__ = "recommendation_reviews"
    recommendation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"),
                                                         index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # accept|reject|edit|confirm|deny|undo
    payload: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
