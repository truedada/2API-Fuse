# app/api/v1/endpoints/admin.py
from fastapi import APIRouter, Depends, Query, Path
from typing import List, Optional, Dict, Any
from app.schemas.base import DataResponse, ListResponse
from app.schemas.admin import (
    ChannelCreate, ChannelResponse, ChannelUpdate, ChannelTestResponse,
    PlatformCreate, PlatformResponse, PlatformUpdate,
    ApiKeyCreate, ApiKeyResponse, ApiKeyUpdate,
    IDRequest
)
from app.schemas.error import APIErrorResponse
from app.services.admin import AdminService
from app.api.deps import get_admin_service
from app.api.auth import verify_admin

responses_doc = {
    400: {"model": APIErrorResponse, "description": "请求验证失败"},
    401: {"model": APIErrorResponse, "description": "未授权"},
    404: {"model": APIErrorResponse, "description": "资源未找到"},
    409: {"model": APIErrorResponse, "description": "资源冲突"},
    500: {"model": APIErrorResponse, "description": "服务器内部错误"},
    422: {"model": APIErrorResponse, "description": "数据校验失败"},
}

router = APIRouter(prefix="/admin", tags=["Admin"], responses=responses_doc, dependencies=[Depends(verify_admin)])

# =========================================================
# Platform 平台管理 (全部 POST/GET)
# =========================================================

@router.get("/platforms", response_model=ListResponse[PlatformResponse])
async def list_platforms(
    service: AdminService = Depends(get_admin_service)
):
    """获取所有平台列表"""
    items = await service.get_platforms()
    return ListResponse(items=items, total=len(items), offset=0, limit=len(items))

@router.post("/platforms", response_model=DataResponse[PlatformResponse])
async def create_platform(
    payload: PlatformCreate,
    service: AdminService = Depends(get_admin_service)
):
    """创建上游平台"""
    platform = await service.create_platform(payload)
    return DataResponse(data=platform)

@router.post("/platforms/update", response_model=DataResponse[PlatformResponse])
async def update_platform(
    payload: PlatformUpdate,
    service: AdminService = Depends(get_admin_service)
):
    """更新平台配置"""
    platform = await service.update_platform(payload)
    return DataResponse(data=platform)

@router.post("/platforms/delete", response_model=DataResponse[bool])
async def delete_platform(
    payload: IDRequest,
    service: AdminService = Depends(get_admin_service)
):
    """删除平台 (需确保无下属渠道)"""
    success = await service.delete_platform(payload)
    return DataResponse(data=success)


# =========================================================
# Channel 渠道管理 (全部 POST/GET)
# =========================================================

@router.get("/channels", response_model=ListResponse[ChannelResponse])
async def list_channels(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    platform_id: Optional[int] = None,
    service: AdminService = Depends(get_admin_service)
):
    """分页查询渠道列表，可按平台筛选"""
    items, total = await service.get_channels(limit=limit, offset=offset, platform_id=platform_id)
    return ListResponse(items=items, total=total, offset=offset, limit=limit)

@router.post("/channels", response_model=DataResponse[ChannelResponse])
async def create_channel(
    payload: ChannelCreate,
    service: AdminService = Depends(get_admin_service)
):
    """
    创建具体账号并同步Redis
    如果平台下已存在同名账号，将返回 409 Conflict
    """
    channel = await service.create_channel(payload)
    return DataResponse(data=channel)

@router.post("/channels/upsert", response_model=DataResponse[ChannelResponse])
async def upsert_channel(
    payload: ChannelCreate,
    service: AdminService = Depends(get_admin_service)
):
    """
    【新增】创建或更新账号
    如果平台下存在同名账号，则更新；否则创建。
    """
    channel = await service.upsert_channel(payload)
    return DataResponse(data=channel)

@router.post("/channels/update", response_model=DataResponse[ChannelResponse])
async def update_channel(
    payload: ChannelUpdate,
    service: AdminService = Depends(get_admin_service)
):
    """更新账号配置/启用状态"""
    channel = await service.update_channel(payload)
    return DataResponse(data=channel)

@router.post("/channels/delete", response_model=DataResponse[bool])
async def delete_channel(
    payload: IDRequest,
    service: AdminService = Depends(get_admin_service)
):
    """删除账号并清理Redis缓存"""
    success = await service.delete_channel(payload)
    return DataResponse(data=success)

# --- 新增：管理功能接口 ---

@router.post("/channels/{channel_id}/test", response_model=DataResponse[ChannelTestResponse])
async def test_channel_connectivity(
    channel_id: int = Path(..., title="Channel ID"),
    service: AdminService = Depends(get_admin_service)
):
    """
    测试渠道连通性
    """
    result = await service.test_channel(channel_id)
    return DataResponse(data=result)

@router.post("/channels/{channel_id}/sync_balance", response_model=DataResponse[Dict[str, Any]])
async def sync_channel_balance(
    channel_id: int = Path(..., title="Channel ID"),
    service: AdminService = Depends(get_admin_service)
):
    """
    手动触发余额同步
    """
    result = await service.sync_channel_balance(channel_id)
    return DataResponse(data=result)

@router.post("/channels/{channel_id}/refresh", response_model=DataResponse[Dict[str, Any]])
async def refresh_channel_session(
    channel_id: int = Path(..., title="Channel ID"),
    service: AdminService = Depends(get_admin_service)
):
    """
    手动刷新 Session/Token (针对 Web 逆向渠道)
    """
    result = await service.refresh_channel_session(channel_id)
    return DataResponse(data=result)

# =========================================================
# API Key 管理 (全部 POST/GET)
# =========================================================

@router.get("/apikeys", response_model=ListResponse[ApiKeyResponse])
async def list_apikeys(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: AdminService = Depends(get_admin_service)
):
    """查询 API Key 列表"""
    items, total = await service.get_apikeys(limit=limit, offset=offset)
    return ListResponse(items=items, total=total, offset=offset, limit=limit)

@router.post("/apikeys", response_model=DataResponse[ApiKeyResponse])
async def create_apikey(
    payload: ApiKeyCreate,
    service: AdminService = Depends(get_admin_service)
):
    """创建分发给下游用户的 API Key"""
    apikey = await service.create_apikey(payload)
    return DataResponse(data=apikey)

@router.post("/apikeys/update", response_model=DataResponse[ApiKeyResponse])
async def update_apikey(
    payload: ApiKeyUpdate,
    service: AdminService = Depends(get_admin_service)
):
    """更新 API Key (禁用/修改额度)"""
    apikey = await service.update_apikey(payload)
    return DataResponse(data=apikey)

@router.post("/apikeys/delete", response_model=DataResponse[bool])
async def delete_apikey(
    payload: IDRequest,
    service: AdminService = Depends(get_admin_service)
):
    """删除 API Key"""
    success = await service.delete_apikey(payload)
    return DataResponse(data=success)