from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _strong(pw: str) -> str:
    if len(pw) < 10:
        raise ValueError("Password must be at least 10 characters.")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise ValueError("Password must contain letters and numbers.")
    return pw


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _strong(v)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(max_length=128)

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _strong(v)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _strong(v)


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    full_name: str | None = None
    headline: str | None = None
    location: str | None = None
    target_roles: list[str] = []
    preferred_locations: list[str] = []
    remote_preference: str | None = None
    experience_level: str | None = None
    weekly_study_hours: int | None = None
    llm_consent: bool = False
    llm_consent_at: datetime | None = None
    onboarding_completed: bool = False


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    headline: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=200)
    target_roles: list[str] | None = Field(default=None, max_length=10)
    preferred_locations: list[str] | None = Field(default=None, max_length=10)
    remote_preference: str | None = Field(default=None, pattern="^(remote|hybrid|onsite|any)$")
    experience_level: str | None = Field(default=None, pattern="^(student|entry|mid|senior)$")
    weekly_study_hours: int | None = Field(default=None, ge=1, le=60)
    llm_consent: bool | None = None
    onboarding_completed: bool | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    is_admin: bool
    created_at: datetime
    profile: ProfileOut | None = None


class AuthOut(BaseModel):
    user: UserOut
    csrf_token: str
    access_expires_in: int


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    user_agent: str | None
    current: bool = False


class DeleteAccountIn(BaseModel):
    password: str = Field(max_length=128)
    confirm: str = Field(pattern="^DELETE$")
