from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...models import AnalysisRun, CoverLetter, JobApplication, JobDescription, Recommendation, Resume, ResumeVersion, User
from ..deps import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Counts and recent items for the signed-in user only. No sample data is ever mixed in."""
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(model.user_id == user.id, *where)) or 0

    status_rows = db.execute(select(JobApplication.status, func.count()).where(JobApplication.user_id == user.id)
                             .group_by(JobApplication.status)).all()
    recent = db.execute(
        select(AnalysisRun.id, AnalysisRun.overall_score, AnalysisRun.status, AnalysisRun.created_at,
               JobDescription.title, JobDescription.company, Resume.title)
        .join(JobDescription, JobDescription.id == AnalysisRun.job_id)
        .join(Resume, Resume.id == AnalysisRun.resume_id)
        .where(AnalysisRun.user_id == user.id).order_by(AnalysisRun.created_at.desc()).limit(5)).all()
    versions = db.execute(
        select(ResumeVersion.id, ResumeVersion.label, ResumeVersion.version_number, ResumeVersion.created_at,
               Resume.id, Resume.title)
        .join(Resume, Resume.id == ResumeVersion.resume_id)
        .where(Resume.user_id == user.id).order_by(ResumeVersion.created_at.desc()).limit(5)).all()
    return {
        "counts": {
            "resumes": count(Resume),
            "jobs": count(JobDescription),
            "analyses": count(AnalysisRun),
            "pending_recommendations": count(Recommendation, Recommendation.status == "pending"),
            "cover_letters": count(CoverLetter),
            "applications": count(JobApplication),
        },
        "application_statuses": {s: n for s, n in status_rows},
        "recent_analyses": [{"id": str(r[0]), "score": r[1], "status": r[2], "created_at": r[3], "job_title": r[4],
                             "company": r[5], "resume_title": r[6]} for r in recent],
        "recent_versions": [{"id": str(v[0]), "label": v[1], "version_number": v[2], "created_at": v[3],
                             "resume_id": str(v[4]), "resume_title": v[5]} for v in versions],
    }
