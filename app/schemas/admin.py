# app/schemas/admin.py
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
from pydantic import Field
from app.schemas.base import BaseSchema

# --- 通用 ---
class IDRequest(BaseSchema):
    id: int

# --- Platform Schemas ---
class PlatformCreate(BaseSchema):
    name: str = Field(..., description="平台名称")
    adapter_type: str = Field("openai", description="适配器类型")
    base_url: str = Field(..., description="API 基础地址")
    proxy_url: Optional[str] = None
    model_map: Dict[str, str] = {}
    
    # 【新增】默认模型列表，用于 Channel 继承
    default_models: List[str] = Field(
        default=[],
        description="默认支持的模型列表。当 Channel 的 supported_models 为空时，将继承此配置。"
    )

    # 【新增】
    extra_config: Dict[str, Any] = Field(
        default={}, 
        description="公共配置，如公共Cookie或Headers"
    )

class PlatformUpdate(BaseSchema):
    id: int
    name: Optional[str] = None
    adapter_type: Optional[str] = None
    base_url: Optional[str] = None
    proxy_url: Optional[str] = None
    model_map: Optional[Dict[str, str]] = None
    # 【新增】
    default_models: Optional[List[str]] = None
    extra_config: Optional[Dict[str, Any]] = None

class PlatformResponse(PlatformCreate):
    id: int

# --- Channel Schemas ---
class ChannelCreate(BaseSchema):
    name: str
    platform_id: int
    credentials: Dict[str, Any]
    
    # 【修改】设置默认值为 []，允许前端传空列表以表示"继承平台配置"
    supported_models: List[str] = []
    
    weight: int = 1
    rate_limits: Dict[str, Any] = {}

class ChannelUpdate(BaseSchema):
    id: int
    name: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    supported_models: Optional[List[str]] = None
    weight: Optional[int] = None
    rate_limits: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

class ChannelResponse(ChannelCreate):
    id: int
    is_active: bool
    platform_name: Optional[str] = None
    error_count: int = 0
    next_reset_time: int = 0
    
    # 【新增】状态和余额字段
    balance: float = 0.0
    balance_updated_at: Optional[datetime] = None
    status_msg: Optional[str] = None
    test_at: Optional[datetime] = None
    usage_progress: Dict[str, Any] = {}

class ChannelTestResponse(BaseSchema):
    id: int
    is_valid: bool
    msg: str
    elapsed: float

# --- ApiKey Schemas ---
class ApiKeyCreate(BaseSchema):
    name: str = "My App"
    balance: int = 100 

class ApiKeyUpdate(BaseSchema):
    id: int
    name: Optional[str] = None
    balance: Optional[int] = None 
    is_active: Optional[bool] = None

class ApiKeyResponse(BaseSchema):
    id: int
    name: Optional[str]
    key: str
    balance: int
    used_count: int
    total_tokens: int
    is_active: bool
    created_at: datetime

# --- [新增] Usage Log Schemas ---
class UsageLogResponse(BaseSchema):
    id: int
    trace_id: Optional[str] = None
    model_name: str
    
    # 统计数据
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: int
    is_stream: bool
    
    # 关联信息 (为了前端展示方便，直接展开名称)
    api_key_name: Optional[str] = Field(None, description="API Key 名称")
    api_key_str: Optional[str] = Field(None, description="API Key 掩码或标识")
    channel_name: Optional[str] = Field(None, description="渠道名称")
    platform_name: Optional[str] = Field(None, description="所属平台名称")
    
    created_at: datetime