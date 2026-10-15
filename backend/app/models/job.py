from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, IdMixin, JSONType, TimestampMixin


class SkillTaxonomy(TimestampMixin, Base):
    __tablename__ = "skill_taxonomy"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # normalised skill identifier
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    implies: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    learning_project: Mapped[str | None] = mapped_column(Text)

    aliases: Mapped[list[SkillAlias]] = relationship(back_populates="skill", cascade="all, delete-orphan")


class SkillAlias(IdMixin, Base):
    __tablename__ = "skill_aliases"
    __table_args__ = (UniqueConstraint("skill_id", "alias", name="uq_skill_aliases_skill_alias"),)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skill_taxonomy.id", ondelete="CASCADE"), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(120), nullable=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    skill: Mapped[SkillTaxonomy] = relationship(back_populates="aliases")


class JobDescription(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_descriptions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    seniority: Mapped[str | None] = mapped_column(String(30))
    min_years: Mapped[float | None] = mapped_column(Float)
    degree_level: Mapped[int | None] = mapped_column(Integer)
    degree_fields: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    parse_version: Mapped[str] = mapped_column(String(20), default="jd-1", nullable=False)

    requirements: Mapped[list[JobRequirement]] = relationship(back_populates="job", cascade="all, delete-orphan",
                                                               order_by="JobRequirement.position")


class JobRequirement(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_requirements"
    __table_args__ = (UniqueConstraint("job_id", "req_key", name="uq_job_requirements_key"),)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True,
                                              nullable=False)
    req_key: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. skill:docker, resp:2, degree, years
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)  # technical_skill|tool|soft_skill|education|experience|certification|responsibility|domain
    importance: Mapped[str] = mapped_column(String(10), nullable=False)  # required | preferred
    skill_id: Mapped[str | None] = mapped_column(ForeignKey("skill_taxonomy.id", ondelete="SET NULL"))
    aliases: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    evidence_expectation: Mapped[str | None] = mapped_column(String(300))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    job: Mapped[JobDescription] = relationship(back_populates="requirements")
