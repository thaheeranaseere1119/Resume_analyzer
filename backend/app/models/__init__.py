"""Import every model so Alembic autogenerate and metadata.create_all see the full schema."""
from .analysis import AnalysisComponent, AnalysisRun, Recommendation, RecommendationEvidence, RecommendationReview, RequirementMatch
from .career import (CareerRoadmap, CoverLetter, Export, InterviewQuestion, InterviewSession, JobApplication,
                     RoadmapMilestone, SavedJob)
from .job import JobDescription, JobRequirement, SkillAlias, SkillTaxonomy
from .resume import (Resume, ResumeBlock, ResumeChange, ResumeSection, ResumeSkillEvidence, ResumeUpload,
                     ResumeVersion, UserConfirmation)
from .user import AuditEvent, AuthSession, PasswordResetToken, User, UserProfile

__all__ = [n for n in dir() if n[:1].isupper()]
