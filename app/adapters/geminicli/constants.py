# app/adapters/geminicli/constants.py

# 默认域名 (不带 path)
# 这是 Google Cloud Code 内部 API 的根地址
DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com"

# 固定 API 路径前缀
# 所有请求都会拼接此后缀，例如 /v1internal:generateContent
API_PATH_PREFIX = "/v1internal"

# 标准 OAuth2 Token 刷新地址
GOOGLE_OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"

# User Agent
# 用于伪装成 GCLI 客户端
USER_AGENT = "geminicli-oauth/1.0"

# GCLI 凭证所需的 Scopes (仅供参考，实际刷新时依赖 refresh_token 的原有 scope)
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]