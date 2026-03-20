from fastapi import APIRouter
from app.api import jobs, upload, config_api, system, validation

router = APIRouter()
router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
router.include_router(upload.router, prefix="/jobs", tags=["upload"])
router.include_router(validation.router, tags=["validation"])
router.include_router(config_api.router, prefix="/config", tags=["config"])
router.include_router(system.router, tags=["system"])
