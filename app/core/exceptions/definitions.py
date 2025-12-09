# -*- coding: utf-8 -*-
from typing import Optional, Dict, Any

class BaseAPIException(Exception):
    """API 异常基类"""
    def __init__(self,
                 status_code: int,
                 detail: str,
                 error_code: int = 600099,
                 error_type: str = "API_ERROR",
                 headers: Optional[Dict[str, Any]] = None):
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.error_type = error_type
        self.headers = headers
        super().__init__(detail)

# --- 401 认证相关 (Authentication) ---
class AuthenticationRequired(BaseAPIException):
    def __init__(self, detail: str = "缺少认证凭证"):
        super().__init__(401, detail, 100001, "AUTH_REQUIRED", headers={"WWW-Authenticate": "Bearer"})

class InvalidCredentials(BaseAPIException):
    def __init__(self, detail: str = "凭证无效"):
        super().__init__(401, detail, 100002, "INVALID_CREDENTIALS", headers={"WWW-Authenticate": "Bearer"})

class TokenExpired(BaseAPIException):
    def __init__(self, detail: str = "凭证已过期"):
        super().__init__(401, detail, 100003, "TOKEN_EXPIRED", headers={"WWW-Authenticate": "Bearer"})

# --- 403 权限相关 (Authorization) ---
class PermissionDenied(BaseAPIException):
    def __init__(self, detail: str = "权限不足"):
        super().__init__(403, detail, 200001, "PERMISSION_DENIED")

# --- 404 资源相关 ---
class NotFound(BaseAPIException):
    def __init__(self, detail: str = "请求的资源未找到"):
        super().__init__(404, detail, 300006, "NOT_FOUND")

# --- 400/422 请求相关 ---
class InvalidInput(BaseAPIException):
    def __init__(self, detail: str = "输入参数无效"):
        super().__init__(400, detail, 400003, "INVALID_INPUT")

# --- 409 冲突 ---
class ResourceConflict(BaseAPIException):
    def __init__(self, detail: str = "资源已存在或状态冲突"):
        super().__init__(409, detail, 300003, "RESOURCE_CONFLICT")

# --- 429 限流 ---
class RateLimitExceeded(BaseAPIException):
    def __init__(self, detail: str = "请求过于频繁", retry_after_seconds: int = 60):
        headers = {"Retry-After": str(retry_after_seconds)}
        super().__init__(429, detail, 600002, "RATE_LIMIT_EXCEEDED", headers=headers)

# --- 500 系统级错误 ---
class InternalServerError(BaseAPIException):
    def __init__(self, detail: str = "服务器内部错误"):
        super().__init__(500, detail, 600001, "INTERNAL_SERVER_ERROR")

class DatabaseError(InternalServerError):
    def __init__(self, detail: str = "数据库操作失败"):
        super().__init__(detail)
        self.error_code = 500001
        self.error_type = "DATABASE_ERROR"

class ExternalServiceError(BaseAPIException):
    """上游服务（如 OpenAI）报错"""
    def __init__(self, detail: str = "上游服务响应错误"):
        super().__init__(502, detail, 500003, "UPSTREAM_ERROR")

class ServiceUnavailable(BaseAPIException):
    """上游服务不可用"""
    def __init__(self, detail: str = "上游服务不可用"):
        super().__init__(503, detail, 500003, "UPSTREAM_UNAVAILABLE")