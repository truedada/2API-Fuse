# app/api/v1/endpoints/antigravity.py

from fastapi import APIRouter, Depends
from app.schemas.base import DataResponse
from app.schemas.antigravity_auth import (
    AntigravityAuthURLRequest,
    AntigravityAuthURLResponse,
    AntigravityCallbackRequest,
    AntigravityCallbackResponse
)
from app.services.antigravity_auth import AntigravityAuthService
from app.api.deps import get_antigravity_service
from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository

router = APIRouter(prefix="/antigravity", tags=["Antigravity OAuth"])



@router.post("/auth-url", response_model=DataResponse[AntigravityAuthURLResponse])
async def generate_antigravity_auth_url(
    payload: AntigravityAuthURLRequest,
    service: AntigravityAuthService = Depends(get_antigravity_service)
):
    """
    生成 Antigravity 专用授权链接 (Internal API Scopes)
    """
    result = await service.create_auth_url(payload)
    return DataResponse(data=result)

@router.post("/callback", response_model=DataResponse[AntigravityCallbackResponse])
async def handle_antigravity_callback(
    payload: AntigravityCallbackRequest,
    service: AntigravityAuthService = Depends(get_antigravity_service)
):
    """
    Antigravity 回调处理：
    1. 换取 Token
    2. 调用 v1internal:loadCodeAssist 获取真实 Project ID (自动激活资格)
    3. 创建渠道
    """
    result = await service.handle_callback_and_save(payload)
    return DataResponse(data=result)