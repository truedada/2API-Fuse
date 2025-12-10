from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from app.core.config import settings

# 生成授权链接请求
class GoogleAuthURLRequest(BaseModel):
    # 允许前端覆盖回调地址（可选），否则使用后端配置的默认值
    pass
    #redirect_uri: Optional[str] = Field(settings.GOOGLE_REDIRECT_URI, description="可选：覆盖后端配置的默认回调地址")

class GoogleAuthURLResponse(BaseModel):
    url: str

# 回调处理请求
class GoogleAuthCallbackRequest(BaseModel):
    # 修改：直接接收完整的回调 URL
    callback_url: str = Field(..., description="浏览器地址栏的完整回调URL (包含 ?code=...)")
    platform_id: int = Field(..., description="绑定的平台ID")
    channel_name: Optional[str] = Field(None, description="自定义渠道名称，留空则自动生成")
    # [新增] 手动指定项目ID
    project_id: Optional[str] = Field(None, description="手动指定的 Google Cloud Project ID，若提供则跳过自动检测")

# 回调响应
class GoogleAuthCallbackResponse(BaseModel):
    channel_id: int
    channel_name: str
    email: Optional[str]
    credentials_snapshot: Dict[str, Any]