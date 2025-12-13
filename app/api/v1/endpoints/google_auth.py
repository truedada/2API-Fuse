# app/api/v1/endpoints/google.py
from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.schemas.base import DataResponse, ListResponse
from app.schemas.google_auth import (
    GoogleAuthURLRequest,
    GoogleAuthURLResponse,
    GoogleAuthCallbackRequest,
    GoogleAuthCallbackResponse,
    PlatformSimpleResponse
)
from app.services.geminicli_auth import GeminiCliAuthService
from app.services.antigravity_auth import AntigravityAuthService
from app.services.admin import AdminService
from app.api.deps import get_geminicli_auth_service, get_antigravity_service, get_admin_service
from app.models.platform import Platform

# 定义精简版响应模型，仅供此处前端选择使用
router = APIRouter(prefix="/google", tags=["Google OAuth"])

@router.get("/platforms", response_model=ListResponse[PlatformSimpleResponse], operation_id="list_google_supported_platforms")
async def list_google_supported_platforms(
    service: AdminService = Depends(get_admin_service)
):
    """
    获取支持 Google OAuth 的平台列表 (精简版)
    用于前端下拉选择将账号导入到哪个平台
    仅返回 adapter_type 为 geminicli 或 antigravity 的平台
    """
    # 通过response model应该就能限制
    items = await service.get_platforms()
    return ListResponse(items=items, total=len(items), offset=0, limit=len(items))

@router.post("/geminicli/auth-url", response_model=DataResponse[GoogleAuthURLResponse], operation_id="generate_geminicli_auth_url")
async def generate_auth_url(
    payload: GoogleAuthURLRequest,
    service: GeminiCliAuthService = Depends(get_geminicli_auth_service),
    # _: bool = Depends(verify_admin) # 如需鉴权请解注
):
    """
    生成 Google 授权跳转链接 (使用后端配置)
    """
    result = await service.create_auth_url(payload)
    return DataResponse(data=result)

@router.post("/geminicli/callback", response_model=DataResponse[GoogleAuthCallbackResponse], operation_id="handle_geminicli_auth_callback")
async def handle_auth_callback(
    payload: GoogleAuthCallbackRequest,
    service: GeminiCliAuthService = Depends(get_geminicli_auth_service),
    # _: bool = Depends(verify_admin) # 如需鉴权请解注
):
    """
    回调处理：解析完整回调 URL，换取 Token 并自动创建渠道
    """
    result = await service.handle_callback_and_save(payload)
    return DataResponse(data=result)

@router.post("/antigravity/auth-url", response_model=DataResponse[GoogleAuthURLResponse], operation_id="generate_antigravity_auth_url")
async def generate_antigravity_auth_url(
    payload: GoogleAuthURLRequest,
    service: AntigravityAuthService = Depends(get_antigravity_service)
):
    """
    生成 Antigravity 专用授权链接 (Internal API Scopes)
    """
    result = await service.create_auth_url(payload)
    return DataResponse(data=result)

@router.post("/antigravity/callback", response_model=DataResponse[GoogleAuthCallbackResponse], operation_id="handle_antigravity_auth_callback")
async def handle_antigravity_callback(
    payload: GoogleAuthCallbackRequest,
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