from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, IdMixin, JSONType, TimestampMixin


class Export(IdMixin, TimestampMixin, Base):
    __tablename__ = "exports"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"),
                                                                index=True)
    cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cover_letters.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(String(10), nullable=False)  # docx | pdf
    template: Mapped[str] = mapped_column(String(30), nullable=False)  # original | ats_classic | ats_modern | letter
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(200))
    filename: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    verification: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CoverLetter(IdMixin, TimestampMixin, Base):
    __tablename__ = "cover_letters"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_descriptions.id", ondelete="SET NULL"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cover_letters.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    tone: Mapped[str] = mapped_column(String(20), nullable=False)
    greeting: Mapped[str] = mapped_column(String(300), nullable=False)
    paragraphs: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    closing: Mapped[str] = mapped_column(Text, nullable=False)
    selected_block_ids: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    user_context: Mapped[str | None] = mapped_column(Text)  # optional, user-provided, kept separate from resume facts
    writer: Mapped[str] = mapped_column(String(20), nullable=False)
    validation: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)


class InterviewSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "interview_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_descriptions.id", ondelete="SET NULL"))
    difficulty: Mapped[str] = mapped_column(String(15), nullable=False)
    mode: Mapped[str] = mapped_column(String(15), nullable=False, default="practice")  # practice | mock
    writer: Mapped[str] = mapped_column(String(20), nullable=False)

    questions: Mapped[list[InterviewQuestion]] = relationship(back_populates="session", cascade="all, delete-orphan",
                                                               order_by="InterviewQuestion.position")


class InterviewQuestion(IdMixin, Base):
    __tablename__ = "interview_questions"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True,
                                                  nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # technical|project|experience|behavioral|follow_up
    difficulty: Mapped[str] = mapped_column(String(15), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_ref: Mapped[str | None] = mapped_column(String(300))
    answer_structure: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # labelled EXAMPLE
    follow_ups: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    user_answer: Mapped[str | None] = mapped_column(Text)
    feedback: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    session: Mapped[InterviewSession] = relationship(back_populates="questions")


class CareerRoadmap(IdMixin, TimestampMixin, Base):
    __tablename__ = "career_roadmaps"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_descriptions.id", ondelete="SET NULL"))
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    target_role: Mapped[str] = mapped_column(String(200), nullable=False)
    weekly_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    writer: Mapped[str] = mapped_column(String(20), nullable=False)

    milestones: Mapped[list[RoadmapMilestone]] = relationship(back_populates="roadmap", cascade="all, delete-orphan",
                                                               order_by="RoadmapMilestone.position")


class RoadmapMilestone(IdMixin, Base):
    __tablename__ = "roadmap_milestones"
    roadmap_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("career_roadmaps.id", ondelete="CASCADE"), index=True,
                                                  nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[int] = mapped_column(Integer, nullable=False)
    week_end: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    skill_id: Mapped[str | None] = mapped_column(String(60))
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prerequisites: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    tasks: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    project: Mapped[str | None] = mapped_column(Text)
    resources: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(15), default="todo", nullable=False)  # todo | in_progress | done

    roadmap: Mapped[CareerRoadmap] = relationship(back_populates="milestones")


class SavedJob(IdMixin, TimestampMixin, Base):
    """Job discovery listings (manual/curated entries or a configured provider)."""
    __tablename__ = "saved_jobs"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_descriptions.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    work_mode: Mapped[str | None] = mapped_column(String(20))  # remote | hybrid | onsite
    employment_type: Mapped[str | None] = mapped_column(String(30))
    experience: Mapped[str | None] = mapped_column(String(60))
    education: Mapped[str | None] = mapped_column(String(120))
    skills: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    url: Mapped[str | None] = mapped_column(String(500))


class JobApplication(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_applications"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_descriptions.id", ondelete="SET NULL"))
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="saved", index=True)
    deadline: Mapped[date | None] = mapped_column(Date)
    applied_on: Mapped[date | None] = mapped_column(Date)
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    interview_dates: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status_history: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
