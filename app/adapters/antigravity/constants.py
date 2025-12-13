# app/adapters/antigravity/constants.py

BASE_URL_DAILY = "https://daily-cloudcode-pa.sandbox.googleapis.com"
BASE_URL_PROD = "https://cloudcode-pa.googleapis.com"

# API Endpoints
PATH_STREAM = "/v1internal:streamGenerateContent"
PATH_GENERATE = "/v1internal:generateContent"
PATH_MODELS = "/v1internal:fetchAvailableModels"
PATH_LOAD_CODE_ASSIST = "/v1internal:loadCodeAssist"

# OAuth Config
CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"
GOOGLE_OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"

# User Agent
USER_AGENT = "antigravity/1.11.9 windows/amd64"

# Model Mappings
MODEL_ALIAS_MAP = {
    # 功能性映射
    "gemini-2.5-computer-use-preview": "rev19-uic3-1p",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
    
    # 核心模型别名整理
    "gemini-3-pro-preview": "gemini-3-pro-high", 
    
    # Claude 系列
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-sonnet-4-5-thinking": "claude-sonnet-4-5-thinking",
    "claude-opus-4-5-thinking": "claude-opus-4-5-thinking",
    
    # GPT 系列
    "gpt-oss-120b": "gpt-oss-120b-medium"
}

# Ignore List
IGNORED_MODELS = [
    "chat_20706", 
    "chat_23310", 
    "gemini-2.5-flash-lite",
    "MODEL_PLACEHOLDER_M12",
    "MODEL_PLACEHOLDER_M8",
    "MODEL_PLACEHOLDER_M7",
    "MODEL_PLACEHOLDER_M9"
]