# app/schemas/antigravity_auth.py

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class AntigravityAuthURLRequest(BaseModel):
    # 允许前端传 redirect_uri，方便本地不同端口调试
    redirect_uri: Optional[str] = Field(None, description="回调地址 (默认 http://localhost:8080)")

class AntigravityAuthURLResponse(BaseModel):
    url: str

class AntigravityCallbackRequest(BaseModel):
    callback_url: str = Field(..., description="完整的回调URL (包含 ?code=...)")
    platform_id: int = Field(..., description="绑定的平台ID")
    channel_name: Optional[str] = Field(None, description="自定义渠道名称")
    # Antigravity 也可以手动指定 project_id 跳过自动获取
    project_id: Optional[str] = Field(None, description="手动指定 Project ID")

class AntigravityCallbackResponse(BaseModel):
    channel_id: int
    channel_name: str
    email: Optional[str]
    credentials_snapshot: Dict[str, Any]