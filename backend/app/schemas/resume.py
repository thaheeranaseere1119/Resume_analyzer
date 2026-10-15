from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

SECTION_TYPES = ("contact", "summary", "experience", "internships", "projects", "skills", "technical_skills",
                 "soft_skills", "education", "certifications", "achievements", "publications", "languages",
                 "volunteering", "interests", "other")
BLOCK_KINDS = ("contact", "paragraph", "bullet", "entry_header", "skills_line")


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    original_filename: str
    file_format: str
    size_bytes: int
    page_count: int | None
    scanned: bool
    ocr_confidence: float | None
    status: str
    error: str | None
    created_at: datetime


class ResumeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    status: str
    current_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    version_count: int = 0
    file_format: str | None = None
    original_filename: str | None = None


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stable_id: uuid.UUID
    section_id: uuid.UUID | None
    position: int
    kind: str
    text: str
    entry_key: str | None
    page: int | None
    char_start: int | None
    char_end: int | None
    confidence: float
    user_corrected: bool


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    section_type: str
    heading: str
    position: int
    page: int | None
    confidence: float
    warnings: list[str]


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    parent_id: uuid.UUID | None
    version_number: int
    label: str
    source: str
    document_format: str
    job_description_id: uuid.UUID | None
    created_at: datetime


class VersionOut(VersionSummary):
    page_count: int | None
    parse_warnings: list[dict]
    profile: dict
    sections: list[SectionOut]
    blocks: list[BlockOut]


class ResumeDetail(ResumeSummary):
    upload: UploadOut | None = None
    current_version: VersionOut | None = None


class ProfilePatch(BaseModel):
    expected_version_id: uuid.UUID
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=200)
    links: list[str] | None = Field(default=None, max_length=10)


class BlockCorrection(BaseModel):
    stable_id: uuid.UUID
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    section_type: str | None = Field(default=None, pattern="^(" + "|".join(SECTION_TYPES) + ")$")
    kind: str | None = Field(default=None, pattern="^(" + "|".join(BLOCK_KINDS) + ")$")


class CorrectionsIn(BaseModel):
    expected_version_id: uuid.UUID
    blocks: list[BlockCorrection] = Field(min_length=1, max_length=200)


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
