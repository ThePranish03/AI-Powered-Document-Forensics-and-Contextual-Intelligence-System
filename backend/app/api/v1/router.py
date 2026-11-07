from fastapi import APIRouter

from app.api.v1 import analysis, auth, dashboard, investigations, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(investigations.router, prefix="/investigations", tags=["investigations"])
api_router.include_router(analysis.router, prefix="/investigations", tags=["analysis"])
