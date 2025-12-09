# -*- coding: utf-8 -*-
# --- app/core/exceptions/handlers.py ---
"""
全局异常处理器注册。
将自定义异常、FastAPI/Pydantic 异常、数据库异常等映射为统一的 JSON 响应格式。
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from tortoise.exceptions import DoesNotExist, IntegrityError, OperationalError, DBConnectionError
from pydantic import ValidationError
from typing import Optional
# 确保 logger 和 definitions 已被加载/配置
from loguru import logger
from app.core.exceptions.definitions import (
    BaseAPIException, NotFound, ResourceConflict, DatabaseError
)
from app.core.config import settings
# --- 统一响应格式 ---
def create_error_response(
    status_code: int,
    code: int,
    error_type: str,
    message: str,
    errors: Optional[list] = None,
    headers: Optional[dict] = None
 ) -> JSONResponse:
    """创建统一格式的错误响应体"""
    content = {
        "code": code,
        "type": error_type,
        "message": message,
        "success": False
    }
    if errors:
        content["errors"] = errors
    return JSONResponse(status_code=status_code, content=content, headers=headers)

# --- 异常处理器 ---

async def api_exception_handler(request: Request, exc: BaseAPIException) -> JSONResponse:
    """处理自定义 BaseAPIException"""
    logger.warning(f"业务异常 [{exc.status_code} {exc.error_code}]: {exc.detail} | Path: {request.url.path}")
    return create_error_response(
        status_code=exc.status_code,
        code=exc.error_code,
        error_type=exc.error_type,
        message=exc.detail,
        headers=exc.headers
    )

async def http_exception_handler(request: Request, exc: HTTPException | StarletteHTTPException) -> JSONResponse:
     """处理 FastAPI/Starlette 内置 HTTPException"""
     # logger.info(f"HTTP 异常 [{exc.status_code}]: {exc.detail} | Path: {request.url.path}")

     # 根据HTTP状态码映射错误码
     error_code_map = {
         400: 400001,  # 60xxxx: 系统内部错误 -> HTTP错误
         401: 100001,
         403: 200001,
         404: 300001,
         405: 600001,
         422: 400001,
         500: 600001,
     }

     return create_error_response(
         status_code=exc.status_code,
         code=error_code_map.get(exc.status_code, 600099),
         error_type=f"HTTP_ERROR_{exc.status_code}",
         message=exc.detail,
         headers=exc.headers if hasattr(exc, 'headers') else None
     )

def _serialize_validation_errors(errors: list) -> list:
    """序列化验证错误，确保所有数据都可以JSON序列化"""
    import base64
    import json

    def _serialize_value(value):
        """递归序列化值，确保JSON可序列化"""
        if value is None:
            return None
        elif isinstance(value, (str, int, float, bool)):
            return value
        elif isinstance(value, bytes):
            return base64.b64encode(value).decode('utf-8')
        elif isinstance(value, (list, tuple)):
            return [_serialize_value(item) for item in value]
        elif isinstance(value, dict):
            return {k: _serialize_value(v) for k, v in value.items()}
        else:
            # 对于其他不可序列化的对象（如方法、函数等），转换为字符串
            try:
                json.dumps(value)  # 测试是否可序列化
                return value
            except (TypeError, ValueError):
                return str(value)

    serialized_errors = []
    for error in errors:
        serialized_error = {}
        for key, value in error.items():
            serialized_error[key] = _serialize_value(value)
        serialized_errors.append(serialized_error)
    return serialized_errors

async def validation_exception_handler(request: Request, exc: RequestValidationError | ValidationError) -> JSONResponse:
     """处理 Pydantic 请求体验证异常"""
     errors = exc.errors()
     details = []
     # 简化错误信息
     for error in errors:
          loc = ".".join(map(str, error['loc']))
          details.append(f"{loc}: {error['msg']} ({error['type']})")

     detail_str = "; ".join(details)
     logger.warning(f"请求参数验证失败: {detail_str} | Path: {request.url.path}")

     # 序列化错误信息，确保可以JSON序列化
     serialized_errors = _serialize_validation_errors(errors)

     return create_error_response(
         status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
         code=400001,  # 40xxxx: 请求验证错误
         error_type="VALIDATION_ERROR",
         message="请求参数验证失败",
         errors=serialized_errors
      )

async def tortoise_does_not_exist_handler(request: Request, exc: DoesNotExist) -> JSONResponse:
      """处理 Tortoise ORM 资源未找到异常"""
      logger.info(f"数据库资源未找到: {exc} | Path: {request.url.path}")
      # 转换为自定义的 404 异常
      return await api_exception_handler(request, NotFound(detail=str(exc)))

async def tortoise_integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
      """处理 Tortoise ORM 数据库完整性约束异常 (如唯一键冲突)"""
      logger.warning(f"数据库完整性约束冲突: {exc} | Path: {request.url.path}")
       # 转换为自定义的 409 异常
      return await api_exception_handler(request, ResourceConflict(detail=f"数据冲突或重复: {exc}"))
      
async def tortoise_db_error_handler(request: Request, exc: OperationalError | DBConnectionError) -> JSONResponse:
      """处理 Tortoise ORM 数据库操作或连接异常"""
      logger.error(f"数据库操作或连接错误: {exc} | Path: {request.url.path}")
      # 转换为自定义的 500 异常
      return await api_exception_handler(request, DatabaseError(detail="数据库操作失败或连接不可用"))

async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
     """
     捕获所有未处理的异常 (500 Internal Server Error)。
     记录详细日志，但在非调试模式下返回通用错误信息，避免泄露服务器内部细节。
     """
     logger.exception(f"发生未处理的服务器内部错误: {exc.__class__.__name__} - {exc} | Path: {request.url.path}")

     detail = "服务器内部错误，请联系管理员"
     # 调试模式下，返回详细错误信息
     if settings.DEBUG:
         detail = f"Internal Error: {exc.__class__.__name__} - {str(exc)}"

     return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=600001,  # 60xxxx: 系统内部错误
        error_type="INTERNAL_SERVER_ERROR",
        message=detail
     )


# --- 注册函数 ---
def register_exception_handlers(app: FastAPI):
    """
    向 FastAPI 应用实例注册所有全局异常处理器。
    注意注册顺序：更具体的异常应在更通用的异常之前注册。
    """
    logger.info("正在注册全局异常处理器...")
    # 自定义异常
    app.add_exception_handler(BaseAPIException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler) # celery task 可能抛出
    # Tortoise ORM 异常
    app.add_exception_handler(DoesNotExist, tortoise_does_not_exist_handler)
    app.add_exception_handler(IntegrityError, tortoise_integrity_error_handler)
    app.add_exception_handler(OperationalError, tortoise_db_error_handler)
    app.add_exception_handler(DBConnectionError, tortoise_db_error_handler)
     # FastAPI / Starlette HTTP 异常
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    # 验证码错误
    
    # 兜底：所有其他 Exception
    app.add_exception_handler(Exception, generic_exception_handler)
    logger.info("全局异常处理器注册完成。")
