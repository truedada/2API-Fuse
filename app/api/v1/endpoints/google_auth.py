from fastapi import APIRouter, Depends
from app.schemas.base import DataResponse
from app.schemas.google_auth import (
    GoogleAuthURLRequest,
    GoogleAuthURLResponse,
    GoogleAuthCallbackRequest,
    GoogleAuthCallbackResponse
)
from app.services.google_auth import GoogleAuthService
from app.api.deps import get_google_auth_service
from app.api.auth import verify_admin 

router = APIRouter(prefix="/google", tags=["Google OAuth"])

@router.post("/auth-url", response_model=DataResponse[GoogleAuthURLResponse])
async def generate_auth_url(
    payload: GoogleAuthURLRequest,
    service: GoogleAuthService = Depends(get_google_auth_service),
    # _: bool = Depends(verify_admin) # 如需鉴权请解注
):
    """
    生成 Google 授权跳转链接 (使用后端配置)
    """
    result = await service.create_auth_url(payload)
    return DataResponse(data=result)

@router.post("/callback", response_model=DataResponse[GoogleAuthCallbackResponse])
async def handle_auth_callback(
    payload: GoogleAuthCallbackRequest,
    service: GoogleAuthService = Depends(get_google_auth_service),
    # _: bool = Depends(verify_admin) # 如需鉴权请解注
):
    """
    回调处理：解析完整回调 URL，换取 Token 并自动创建渠道
    """
    result = await service.handle_callback_and_save(payload)
    return DataResponse(data=result)