# app/api/v1/endpoints/admin.py
from fastapi import APIRouter, Depends, Query, Path
from typing import List, Optional, Dict, Any
from app.schemas.base import DataResponse, ListResponse
from app.schemas.admin import (
    ChannelCreate, ChannelResponse, ChannelUpdate, ChannelTestResponse,
    PlatformCreate, PlatformResponse, PlatformUpdate,
    ApiKeyCreate, ApiKeyResponse, ApiKeyUpdate,
    IDRequest,
    UsageLogResponse
)
from app.schemas.error import APIErrorResponse
from app.services.admin import AdminService
from app.api.deps import get_admin_service
from app.api.auth import verify_admin
from datetime import datetime
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
# Authentication 鉴权状态
# =========================================================

@router.get("/me", response_model=DataResponse[bool], operation_id="get_current_admin_identity")
async def get_current_admin_identity(
):
    """
    获取当前登录管理员信息
    用于前端初始化时检查 Token 是否有效以及获取用户详情
    """
    # 如果 current_admin 是 ORM 对象，FastAPI 会自动根据 response_model 转换，
    # 或者你需要手动转为 dict: return DataResponse(data=current_admin.dict())
    return DataResponse(data=True, message="管理员身份验证成功")


# =========================================================
# Platform 平台管理 (全部 POST/GET)
# =========================================================

@router.get("/platforms", response_model=ListResponse[PlatformResponse], operation_id="list_platforms")
async def list_platforms(
    service: AdminService = Depends(get_admin_service)
):
    """获取所有平台列表"""
    items = await service.get_platforms()
    return ListResponse(items=items, total=len(items), offset=0, limit=len(items))

@router.post("/platforms", response_model=DataResponse[PlatformResponse], operation_id="create_platform")
async def create_platform(
    payload: PlatformCreate,
    service: AdminService = Depends(get_admin_service)
):
    """创建上游平台"""
    platform = await service.create_platform(payload)
    return DataResponse(data=platform)

@router.post("/platforms/update", response_model=DataResponse[PlatformResponse], operation_id="update_platform")
async def update_platform(
    payload: PlatformUpdate,
    service: AdminService = Depends(get_admin_service)
):
    """更新平台配置"""
    platform = await service.update_platform(payload)
    return DataResponse(data=platform)

@router.post("/platforms/delete", response_model=DataResponse[bool], operation_id="delete_platform")
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

@router.get("/channels", response_model=ListResponse[ChannelResponse], operation_id="list_channels")
async def list_channels(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    platform_id: Optional[int] = None,
    service: AdminService = Depends(get_admin_service)
):
    """分页查询渠道列表，可按平台筛选"""
    items, total = await service.get_channels(limit=limit, offset=offset, platform_id=platform_id)
    return ListResponse(items=items, total=total, offset=offset, limit=limit)

@router.post("/channels", response_model=DataResponse[ChannelResponse], operation_id="create_channel")
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

@router.post("/channels/upsert", response_model=DataResponse[ChannelResponse], operation_id="upsert_channel")
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

@router.post("/channels/update", response_model=DataResponse[ChannelResponse], operation_id="update_channel")
async def update_channel(
    payload: ChannelUpdate,
    service: AdminService = Depends(get_admin_service)
):
    """更新账号配置/启用状态"""
    channel = await service.update_channel(payload)
    return DataResponse(data=channel)

@router.post("/channels/delete", response_model=DataResponse[bool], operation_id="delete_channel")
async def delete_channel(
    payload: IDRequest,
    service: AdminService = Depends(get_admin_service)
):
    """删除账号并清理Redis缓存"""
    success = await service.delete_channel(payload)
    return DataResponse(data=success)

# --- 新增：管理功能接口 ---

@router.post("/channels/{channel_id}/test", response_model=DataResponse[ChannelTestResponse], operation_id="test_channel_connectivity")
async def test_channel_connectivity(
    channel_id: int = Path(..., title="Channel ID"),
    service: AdminService = Depends(get_admin_service)
):
    """
    测试渠道连通性
    """
    result = await service.test_channel(channel_id)
    return DataResponse(data=result)

@router.post("/channels/{channel_id}/sync_balance", response_model=DataResponse[Dict[str, Any]], operation_id="sync_channel_balance")
async def sync_channel_balance(
    channel_id: int = Path(..., title="Channel ID"),
    service: AdminService = Depends(get_admin_service)
):
    """
    手动触发余额同步
    """
    result = await service.sync_channel_balance(channel_id)
    return DataResponse(data=result)

@router.post("/channels/{channel_id}/refresh", response_model=DataResponse[Dict[str, Any]], operation_id="refresh_channel_session")
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

@router.get("/apikeys", response_model=ListResponse[ApiKeyResponse], operation_id="list_apikeys")
async def list_apikeys(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: AdminService = Depends(get_admin_service)
):
    """查询 API Key 列表"""
    items, total = await service.get_apikeys(limit=limit, offset=offset)
    return ListResponse(items=items, total=total, offset=offset, limit=limit)

@router.post("/apikeys", response_model=DataResponse[ApiKeyResponse], operation_id="create_apikey")
async def create_apikey(
    payload: ApiKeyCreate,
    service: AdminService = Depends(get_admin_service)
):
    """创建分发给下游用户的 API Key"""
    apikey = await service.create_apikey(payload)
    return DataResponse(data=apikey)

@router.post("/apikeys/update", response_model=DataResponse[ApiKeyResponse], operation_id="update_apikey")
async def update_apikey(
    payload: ApiKeyUpdate,
    service: AdminService = Depends(get_admin_service)
):
    """更新 API Key (禁用/修改额度)"""
    apikey = await service.update_apikey(payload)
    return DataResponse(data=apikey)

@router.post("/apikeys/delete", response_model=DataResponse[bool], operation_id="delete_apikey")
async def delete_apikey(
    payload: IDRequest,
    service: AdminService = Depends(get_admin_service)
):
    """删除 API Key"""
    success = await service.delete_apikey(payload)
    return DataResponse(data=success)

@router.get("/logs", response_model=ListResponse[UsageLogResponse], operation_id="list_usage_logs")
async def list_usage_logs(
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    keyword: Optional[str] = Query(None, description="搜索模型名或TraceID"),
    api_key_id: Optional[int] = Query(None, description="按API Key ID筛选"),
    channel_id: Optional[int] = Query(None, description="按渠道 ID筛选"),
    start_time: Optional[datetime] = Query(None, description="开始时间"),
    end_time: Optional[datetime] = Query(None, description="结束时间"),
    service: AdminService = Depends(get_admin_service)
):
    """
    分页查询 API 调用日志
    支持按时间范围、关键字、渠道或 Key 筛选
    """
    items, total = await service.get_usage_logs(
        limit=limit,
        offset=offset,
        keyword=keyword,
        api_key_id=api_key_id,
        channel_id=channel_id,
        start_time=start_time,
        end_time=end_time
    )
    
    return ListResponse(
        items=items, 
        total=total, 
        offset=offset, 
        limit=limit
    )
@router.post("/channels/{channel_id}/sync_usage", response_model=DataResponse[Dict[str, Any]], operation_id="sync_channel_usage_quota")
async def sync_channel_usage_quota(
    channel_id: int = Path(..., title="Channel ID"),
    service: AdminService = Depends(get_admin_service)
):
    """
    手动同步渠道的使用进度 (Rate Limits)
    
    1. 调用 Adapter 获取上游剩余额度 (Remaining Quota)
    2. 计算已用额度并强制校准 Redis 计数器
    3. 将最新进度持久化回数据库
    """
    result = await service.sync_channel_usage(channel_id)
    return DataResponse(data=result)