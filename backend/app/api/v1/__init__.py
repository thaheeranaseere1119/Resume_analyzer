from fastapi import APIRouter

from . import auth, dashboard, health, resumes

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(auth.account)
router.include_router(dashboard.router)
router.include_router(resumes.router)
