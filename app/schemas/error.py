from pydantic import BaseModel, Field
from typing import Optional, List, Any

class APIErrorResponse(BaseModel):
    """
    用于 Swagger/OpenAPI 文档展示的通用错误结构
    """
    code: int = Field(..., description="业务错误码 (六位)")
    type: str = Field(..., description="错误类型标识")
    message: str = Field(..., description="错误详情描述")
    success: bool = Field(False, description="恒为 False")
    errors: Optional[List[Any]] = Field(None, description="详细验证错误信息(可选)")