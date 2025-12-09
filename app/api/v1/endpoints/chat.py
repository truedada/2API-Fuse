# app/api/v1/endpoints/chat.py
from fastapi import APIRouter, Depends, BackgroundTasks
from app.schemas.chat import ChatCompletionRequest, ModelListResponse
from app.services.chat import ChatService
from app.api.auth import verify_api_key

router = APIRouter(tags=["Chat"])

@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    background_tasks: BackgroundTasks, # 注入后台任务管理器
    api_key: str = Depends(verify_api_key)
):
    """
    OpenAI 兼容对话接口
    支持流式 (SSE) 和非流式 (JSON) 返回，支持 reasoning_content
    """
    # 将 background_tasks 传递给 Service
    return await ChatService.create_chat_completion(api_key, request, background_tasks)

# 【新增】OpenAI 兼容模型列表接口
# 兼容 /v1/models 路径
@router.get("/models", response_model=ModelListResponse)
async def list_models(
    api_key: str = Depends(verify_api_key)
):
    """
    获取系统支持的所有模型列表
    OpenAI 兼容格式
    """
    return await ChatService.get_model_list()

# 【新增】为了兼容部分客户端，可能请求 /v1/models
@router.get("/v1/models", response_model=ModelListResponse, include_in_schema=False)
async def list_chat_models(
    api_key: str = Depends(verify_api_key)
):
    return await ChatService.get_model_list()