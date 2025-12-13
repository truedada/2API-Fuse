from fastapi import APIRouter
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.chat import router as chat_router
from app.api.v1.endpoints.google_auth import router as google_auth_router
api_router = APIRouter()
api_router.include_router(chat_router)

api_prefix_router = APIRouter(prefix="/api")
api_prefix_router.include_router(admin_router)
api_prefix_router.include_router(google_auth_router, prefix="/admin")

api_router.include_router(api_prefix_router)
