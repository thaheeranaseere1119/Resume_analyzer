from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base, IdMixin, JSONType, TimestampMixin


class Resume(IdMixin, TimestampMixin, Base):
    __tablename__ = "resumes"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Pointer to the active version. Updated only inside a transaction that locks this row
    # (optimistic concurrency: callers pass the version they based their change on).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resume_versions.id", use_alter=True, name="fk_resumes_current_version", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)  # processing|ready|failed

    versions: Mapped[list[ResumeVersion]] = relationship(
        back_populates="resume", foreign_keys="ResumeVersion.resume_id", cascade="all, delete-orphan",
        order_by="ResumeVersion.version_number")
    uploads: Mapped[list[ResumeUpload]] = relationship(back_populates="resume", cascade="all, delete-orphan")


class ResumeUpload(IdMixin, TimestampMixin, Base):
    """The immutable original file. Never modified; removed only when the user deletes the resume."""
    __tablename__ = "resume_uploads"
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf | docx
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    scanned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|processing|ready|failed
    error: Mapped[str | None] = mapped_column(String(500))
    task_id: Mapped[str | None] = mapped_column(String(64))

    resume: Mapped[Resume] = relationship(back_populates="uploads")


class ResumeVersion(IdMixin, Base):
    __tablename__ = "resume_versions"
    __table_args__ = (UniqueConstraint("resume_id", "version_number", name="uq_resume_versions_number"),)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # upload | edit | restore | correction
    job_description_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="SET NULL"), index=True)
    document_key: Mapped[str] = mapped_column(String(200), nullable=False)  # stored file for this version
    document_format: Mapped[str] = mapped_column(String(10), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parse_warnings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    profile: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)  # name/email/phone/links (user-correctable)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    resume: Mapped[Resume] = relationship(back_populates="versions", foreign_keys=[resume_id])
    sections: Mapped[list[ResumeSection]] = relationship(back_populates="version", cascade="all, delete-orphan",
                                                          order_by="ResumeSection.position")
    blocks: Mapped[list[ResumeBlock]] = relationship(back_populates="version", cascade="all, delete-orphan",
                                                      order_by="ResumeBlock.position")


class ResumeSection(IdMixin, Base):
    __tablename__ = "resume_sections"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True,
                                                  nullable=False)
    section_type: Mapped[str] = mapped_column(String(30), nullable=False)
    heading: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    warnings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)

    version: Mapped[ResumeVersion] = relationship(back_populates="sections")


class ResumeBlock(IdMixin, Base):
    """One paragraph/bullet/line. `stable_id` survives across versions so edits can target it reliably."""
    __tablename__ = "resume_blocks"
    __table_args__ = (UniqueConstraint("version_id", "stable_id", name="uq_resume_blocks_stable"),)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True,
                                                  nullable=False)
    section_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_sections.id", ondelete="CASCADE"))
    stable_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    doc_ref: Mapped[str] = mapped_column(String(40), nullable=False)  # locator inside the version's document
    entry_key: Mapped[str | None] = mapped_column(String(20))
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    user_corrected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    version: Mapped[ResumeVersion] = relationship(back_populates="blocks")


class ResumeChange(IdMixin, Base):
    """Change history: what changed between two versions, why, and how the score moved."""
    __tablename__ = "resume_changes"
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    from_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    to_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"), nullable=False)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recommendations.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # insert|replace|remove|undo|restore|correction
    block_stable_id: Mapped[uuid.UUID | None] = mapped_column()
    before_text: Mapped[str | None] = mapped_column(Text)
    after_text: Mapped[str | None] = mapped_column(Text)
    preservation: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    predicted_score: Mapped[float | None] = mapped_column(Float)
    actual_score: Mapped[float | None] = mapped_column(Float)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserConfirmation(IdMixin, TimestampMixin, Base):
    """A fact the candidate confirmed (or denied) in their own words, e.g. 'I used Docker for ...'."""
    __tablename__ = "user_confirmations"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(80), nullable=False)  # skill id or requirement key
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_block_stable_id: Mapped[uuid.UUID | None] = mapped_column()


class ResumeSkillEvidence(IdMixin, Base):
    __tablename__ = "resume_skill_evidence"
    __table_args__ = (UniqueConstraint("version_id", "skill_id", name="uq_skill_evidence_version_skill"),)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resume_versions.id", ondelete="CASCADE"), index=True,
                                                  nullable=False)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skill_taxonomy.id", ondelete="CASCADE"), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    block_stable_id: Mapped[uuid.UUID | None] = mapped_column()
    section_type: Mapped[str | None] = mapped_column(String(30))
    snippet: Mapped[str | None] = mapped_column(Text)
    via_skill_id: Mapped[str | None] = mapped_column(String(60))
