from fastapi import APIRouter
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.chat import router as chat_router
api_router = APIRouter()
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
api_router.include_router(chat_router, tags=["Chat"])