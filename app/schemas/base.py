# app/schemas/base.py
from pydantic import BaseModel, ConfigDict, Field, computed_field
from typing import Optional, List, TypeVar, Generic, Any, Union

T = TypeVar('T')

class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True) # 允许从 ORM 模型读取数据

class Msg(BaseModel):
    """通用消息响应"""
    message: str
    success: bool = True

class DataResponse(BaseModel, Generic[T]):
    """标准数据响应"""
    data: Optional[T] = None
    code: int = 200
    message: str = "success"
    success: bool = True

class ListResponse(BaseModel, Generic[T]):
    """标准列表响应"""
    items: List[T]
    total: int
    offset: int = 0
    limit: int = 100
    success: bool = True
    message: str = "success"

    @computed_field
    @property
    def has_more(self) -> bool:
        return (self.offset + self.limit) < self.total