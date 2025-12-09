# app/schemas/chat.py
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Union, Dict, Any
import time

# --- 辅助模型 ---

class StreamOptions(BaseModel):
    include_usage: Optional[bool] = False

class CompletionTokensDetails(BaseModel):
    reasoning_tokens: Optional[int] = 0
    audio_tokens: Optional[int] = 0
    text_tokens: Optional[int] = 0

class PromptTokensDetails(BaseModel):
    cached_tokens: Optional[int] = 0
    text_tokens: Optional[int] = 0
    audio_tokens: Optional[int] = 0
    image_tokens: Optional[int] = 0

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: Optional[PromptTokensDetails] = None
    completion_tokens_details: Optional[CompletionTokensDetails] = None

# --- 请求体 Request ---

class RequestMessage(BaseModel):
    role: str
    content: Union[str, List[Dict], None]
    name: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    model: str = Field(..., description="请求的模型名称")
    messages: List[RequestMessage] = Field(..., description="对话历史")
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stream_options: Optional[StreamOptions] = None # 确保定义此字段
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0
    frequency_penalty: Optional[float] = 0
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None

    # 允许 extra 字段，以防用户直接在根节点传参
    model_config = ConfigDict(extra='allow') 

# --- 响应体 Response ---

class ChoiceDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    # 【重点】显式支持 reasoning_content
    reasoning_content: Optional[str] = None 

class Message(BaseModel):
    role: str
    content: Union[str, List[Dict], None]
    reasoning_content: Optional[str] = None 
    name: Optional[str] = None

class Choice(BaseModel):
    index: int
    message: Optional[Message] = None # 非流式
    delta: Optional[ChoiceDelta] = None # 流式
    finish_reason: Optional[str] = None

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[Choice]
    usage: Optional[Usage] = None
    system_fingerprint: Optional[str] = None


# OpenAI 模型对象结构
class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = 1677610602
    owned_by: str = "system"

# 模型列表响应结构
class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelCard]