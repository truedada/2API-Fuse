# app/adapters/zai/adapter.py
import httpx
import json
import time
import uuid
from typing import Dict, Any, AsyncGenerator, List, Optional, Tuple
from loguru import logger
from urllib.parse import urlencode

from app.core.exceptions.definitions import ExternalServiceError
from app.adapters.base import BaseAdapter
from app.adapters.zai.sign import generate_zai_signature

from app.adapters.zai.constants import X_FE_VERSION, DEFAULT_BASE_URL
from app.adapters.zai.utils import (
    get_time_variables,
    sanitize_reasoning,
    tools_to_prompt,
    get_last_user_message,
    parse_xml_tool_calls
)
from app.adapters.zai.messages import process_messages_and_files

class ZaiAdapter(BaseAdapter):
    """
    Z.ai (智谱海外版) 逆向适配器
    支持: 流式对话、思考过程 (Reasoning)、自动签名、自动获取并保存 user_id、多模态图片上传与缓存
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        custom_url = self.config.get("base_url")
        if custom_url:
            self.base_url = custom_url.rstrip("/")
        else:
            self.base_url = DEFAULT_BASE_URL
        self.token = self.credentials.get("token") or self.credentials.get("api_key")
        self.user_id = self.credentials.get("user_id")
        
        if not self.token:
            logger.warning("ZaiAdapter 初始化时未检测到 Token")

    def _build_headers(self, signature: str = None) -> Dict[str, str]:
        """构造请求头"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
            'Authorization': f'Bearer {self.token}',
            'X-FE-Version': X_FE_VERSION,
            'Origin': 'https://chat.z.ai',
            'Referer': 'https://chat.z.ai/',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip',
            'DNT': '1',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Priority': 'u=4',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
            'Content-Type': 'application/json'
        }

        if self.extra_config and "headers" in self.extra_config:
            headers.update(self.extra_config["headers"])
            
        if signature:
            headers['X-Signature'] = signature
        return headers

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """非流式请求实现: 完整聚合流式响应"""
        content = ""
        reasoning_content = ""
        model = request_data.get("model")
        final_usage = None
        finish_reason = "stop"
        
        tool_calls_map: Dict[int, Dict[str, Any]] = {}
        
        async for chunk in self.chat_completion_stream(request_data):
            if not chunk.startswith("data: "):
                continue
            
            raw_data = chunk[6:].strip()
            if raw_data == "[DONE]":
                break
                
            try:
                data = json.loads(raw_data)
                
                if "usage" in data and data["usage"]:
                    final_usage = data["usage"]

                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    delta = choice.get("delta", {})
                    
                    if choice.get("finish_reason"):
                        finish_reason = choice.get("finish_reason")

                    content += delta.get("content", "")
                    reasoning_content += delta.get("reasoning_content", "")
                    
                    if "tool_calls" in delta:
                        for tc in delta["tool_calls"]:
                            index = tc.get("index", 0)
                            
                            if index not in tool_calls_map:
                                tool_calls_map[index] = {
                                    "index": index,
                                    "id": tc.get("id", ""),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                }
                            
                            if tc.get("id"):
                                tool_calls_map[index]["id"] = tc["id"]
                                
                            if "function" in tc:
                                if "name" in tc["function"]:
                                    tool_calls_map[index]["function"]["name"] += tc["function"]["name"]
                                if "arguments" in tc["function"]:
                                    tool_calls_map[index]["function"]["arguments"] += tc["function"]["arguments"]
                                    
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"Z.ai 非流式聚合错误: {e}")
        
        message = {
            "role": "assistant",
            "content": content if content else None
        }
        
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
            
        if tool_calls_map:
            tool_calls_list = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]
            message["tool_calls"] = tool_calls_list
            if finish_reason == "stop":
                finish_reason = "tool_calls"
        
        if not final_usage:
            final_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        return {
            "id": str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": final_usage
        }

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """流式对话核心逻辑"""
        raw_messages = request_data.get("messages", [])
        model = request_data.get("model") 
        tools = request_data.get("tools", [])
        
        # 1. 准备参数
        timestamp_ms = int(time.time() * 1000)
        request_id = str(uuid.uuid4())
        current_user_message_id = str(uuid.uuid4())
        
        tools_prompt = tools_to_prompt(tools)
        
        # 2. 处理图片上传和消息清洗
        base_headers = self._build_headers()
        messages, files = await process_messages_and_files(
            raw_messages, current_user_message_id, tools_prompt, base_headers
        )
        
        user_input = get_last_user_message(messages)
        
        if not self.user_id:
             logger.info("Z.ai user_id 缺失，尝试自动获取...")
             valid = await self.validate_credential()
             if not valid or not self.user_id:
                 raise ExternalServiceError("credentials 中的 user_id 缺失，且自动获取失败")

        time_vars = get_time_variables()
        chat_id = str(uuid.uuid4())

        params = {
            'timestamp': str(timestamp_ms),
            'requestId': request_id,
            'user_id': self.user_id,
            'version': '0.0.1',
            'platform': 'web',
            'token': self.token,
            'language': 'zh-CN',
            'current_url': f'https://chat.z.ai/c/{chat_id}',
            'signature_timestamp': str(timestamp_ms), 
        }

        try:
            signature = generate_zai_signature(user_input, params)
        except Exception as e:
            logger.error(f"Z.ai 签名生成失败: {e}")
            raise ExternalServiceError("签名算法执行失败")

        # 4. Thinking 开关
        enable_thinking = False
        reasoning_param = request_data.get("reasoning")
        if isinstance(reasoning_param, dict) and reasoning_param.get("enabled") is True:
            enable_thinking = True
        thinking_param = request_data.get("thinking") or request_data.get("extra_body", {}).get("thinking")
        if isinstance(thinking_param, dict) and thinking_param.get("type") == "enabled":
            enable_thinking = True
        if request_data.get("enable_thinking") is True:
            enable_thinking = True

        payload = {
            "stream": True,
            "model": model,
            "messages": messages,
            "signature_prompt": user_input,
            "params": {},
            "files": files,
            "current_user_message_id": current_user_message_id,
            "features": {
                "image_generation": False,
                "web_search": False, 
                "auto_web_search": False,
                "preview_mode": True,
                "enable_thinking": enable_thinking 
            },
            "variables": {
                "{{USER_NAME}}": "user",
                "{{CURRENT_DATETIME}}": time_vars['current_datetime'],
                "{{CURRENT_DATE}}": time_vars['current_date'],
                "{{CURRENT_TIME}}": time_vars['current_time']
            }
        }

        url = f"{self.base_url}/v2/chat/completions?{urlencode(params)}"
        req_headers = self._build_headers(signature)
        req_headers['Referer'] = params['current_url']

        final_usage = None
        
        # === 工具调用处理变量 ===
        tool_buffer = "" 
        text_buffer = ""
        tool_call_index = 0
        is_accumulating_tool = False 
        target_start_tag = "<glm_block"

        # 内部辅助函数：生成工具调用的 Chunk 列表 (非生成器，避免 SyntaxError)
        def _generate_tool_chunks(buffer_content: str, current_index: int) -> Tuple[List[str], int]:
            chunks = []
            # 如果缓冲区包含 invoke，即使没有闭合 glm_block 也尝试解析
            if "<invoke" in buffer_content:
                # 补全可能的闭合标签以防解析失败
                parse_buffer = buffer_content
                if "</invoke>" in parse_buffer and "</glm_block>" not in parse_buffer:
                    parse_buffer += "</glm_block>"
                
                tools_parsed = parse_xml_tool_calls(parse_buffer)
                
                for tool in tools_parsed:
                    tid = f"call_{uuid.uuid4().hex[:8]}"
                    # 1. Tool Call Header (id, type, name)
                    t_chunk_start = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "tool_calls": [{
                                    "index": current_index,
                                    "id": tid,
                                    "type": "function",
                                    "function": {"name": tool["name"], "arguments": ""}
                                }]
                            },
                            "finish_reason": None
                        }]
                    }
                    chunks.append(f"data: {json.dumps(t_chunk_start, ensure_ascii=False)}\n\n")

                    # 2. Tool Call Arguments
                    t_chunk_args = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "tool_calls": [{
                                    "index": current_index,
                                    "function": {"arguments": json.dumps(tool["arguments"], ensure_ascii=False)}
                                }]
                            },
                            "finish_reason": None
                        }]
                    }
                    chunks.append(f"data: {json.dumps(t_chunk_args, ensure_ascii=False)}\n\n")
                    current_index += 1
            return chunks, current_index

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream("POST", url, headers=req_headers, json=payload) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        logger.error(f"Z.ai 请求错误 {response.status_code}: {err_text}")
                        raise ExternalServiceError(f"Z.ai 上游返回错误: {response.status_code}")

                    async for line in response.aiter_lines():
                        if not line: continue
                        
                        if line.startswith("data: "):
                            raw_data = line[6:]
                            
                            # 处理 [DONE] 信号
                            if raw_data.strip() == "[DONE]":
                                # 结束前强制刷新缓冲区
                                if is_accumulating_tool or tool_buffer:
                                    final_buffer = tool_buffer + text_buffer
                                    generated_chunks, tool_call_index = _generate_tool_chunks(final_buffer, tool_call_index)
                                    for chunk in generated_chunks:
                                        yield chunk
                                elif text_buffer:
                                    # 如果有残留文本未发送
                                    chunk = {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"content": text_buffer}, "finish_reason": None}]}
                                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                                
                                yield "data: [DONE]\n\n"
                                break
                            
                            try:
                                json_data = json.loads(raw_data)
                                msg_type = json_data.get("type")
                                inner_data = json_data.get("data", {})
                                
                                if "error" in inner_data:
                                    logger.error(f"Z.ai 运行时错误: {inner_data['error']}")
                                    continue

                                if msg_type == "chat:completion":
                                    phase = inner_data.get("phase")
                                    delta_content = inner_data.get("delta_content", "")
                                    
                                    openai_chunk = {
                                        "id": request_id,
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": model,
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": None}]
                                    }

                                    if phase == "thinking":
                                        if delta_content:
                                            clean = sanitize_reasoning(delta_content)
                                            if clean:
                                                openai_chunk["choices"][0]["delta"]["reasoning_content"] = clean
                                                yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                    
                                    elif phase == "answer":
                                        text_buffer += delta_content
                                        
                                        if not is_accumulating_tool:
                                            tag_index = text_buffer.find(target_start_tag)
                                            
                                            if tag_index != -1:
                                                is_accumulating_tool = True
                                                pre_content = text_buffer[:tag_index]
                                                if pre_content:
                                                    openai_chunk["choices"][0]["delta"]["content"] = pre_content
                                                    yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                                
                                                tool_buffer += text_buffer[tag_index:]
                                                text_buffer = ""
                                            else:
                                                # 检查是否是标签的一部分，防止切断
                                                possible_partial_tag = False
                                                check_len = min(len(text_buffer), len(target_start_tag))
                                                for i in range(1, check_len + 1):
                                                    if target_start_tag.startswith(text_buffer[-i:]):
                                                        possible_partial_tag = True
                                                        break
                                                
                                                if not possible_partial_tag:
                                                    if text_buffer:
                                                        openai_chunk["choices"][0]["delta"]["content"] = text_buffer
                                                        yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                                        text_buffer = ""

                                        if is_accumulating_tool:
                                            tool_buffer += text_buffer
                                            text_buffer = ""
                                            
                                            # 检查是否包含闭合标签，或者包含完整的 invoke 闭合
                                            if "</glm_block>" in tool_buffer or "</invoke>" in tool_buffer:
                                                # 如果包含完整的 block 闭合，则处理
                                                if "</glm_block>" in tool_buffer:
                                                    generated_chunks, tool_call_index = _generate_tool_chunks(tool_buffer, tool_call_index)
                                                    for chunk in generated_chunks:
                                                        yield chunk
                                                    tool_buffer = ""
                                                    is_accumulating_tool = False

                                        # 处理 edit_content 补漏
                                        if "edit_content" in inner_data:
                                            edit_val = inner_data["edit_content"]
                                            if edit_val and "</details>" in edit_val:
                                                clean_tail = sanitize_reasoning(edit_val)
                                                if clean_tail:
                                                    tail_chunk = openai_chunk.copy()
                                                    tail_chunk["choices"] = [{"index": 0, "delta": {"reasoning_content": clean_tail}, "finish_reason": None}]
                                                    yield f"data: {json.dumps(tail_chunk, ensure_ascii=False)}\n\n"
                                        
                                    elif phase == "other":
                                        if "usage" in inner_data:
                                            final_usage = inner_data["usage"]
                                        if "edit_content" in inner_data:
                                            content_piece = inner_data["edit_content"]
                                            # 避免将 XML 块作为普通文本输出
                                            if content_piece and "</details>" not in content_piece and "<glm_block" not in content_piece:
                                                 openai_chunk["choices"][0]["delta"]["content"] = content_piece
                                                 yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                        
                                        if text_buffer:
                                            openai_chunk["choices"][0]["delta"]["content"] = text_buffer
                                            yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                            text_buffer = ""
                                            
                                    # 处理完成信号
                                    if inner_data.get("done") is True or phase == "done":
                                        # 关键修复：在发送 finish_reason: stop 之前，处理残留的 tool_buffer
                                        if is_accumulating_tool or tool_buffer:
                                            final_buffer = tool_buffer + text_buffer
                                            generated_chunks, tool_call_index = _generate_tool_chunks(final_buffer, tool_call_index)
                                            for chunk in generated_chunks:
                                                yield chunk
                                            tool_buffer = ""
                                            text_buffer = ""
                                        
                                        final_chunk = {
                                            "id": request_id,
                                            "object": "chat.completion.chunk",
                                            "created": int(time.time()),
                                            "model": model,
                                            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_call_index > 0 else "stop"}]
                                        }
                                        if final_usage:
                                            final_chunk["usage"] = final_usage
                                        
                                        yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                                        yield "data: [DONE]\n\n"
                                        break

                            except json.JSONDecodeError:
                                continue
            
            except httpx.RequestError as e:
                raise ExternalServiceError(f"请求 Z.ai 时发生网络错误: {e}")

    async def validate_credential(self) -> bool:
        """验证 Token 并获取 user_id"""
        url = f"{self.base_url}/v1/auths/"
        headers = self._build_headers()
        
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    fetched_uid = data.get("id")
                    
                    if fetched_uid:
                        self.user_id = fetched_uid
                        current_stored_uid = self.credentials.get("user_id")
                        if current_stored_uid != fetched_uid:
                            logger.info(f"Z.ai 更新 user_id: {fetched_uid}")
                            new_creds = self.credentials.copy()
                            new_creds["user_id"] = fetched_uid
                            await self.save_credentials(new_creds)
                        return True
                return False
            except Exception as e:
                logger.warning(f"Z.ai 身份验证失败: {e}")
                return False

    async def fetch_models(self) -> List[str]:
        """获取模型列表"""
        url = f"{self.base_url}/models"
        headers = self._build_headers()
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    if "data" in data:
                        return [m["id"] for m in data["data"]]
                return []
            except Exception as e:
                logger.error(f"获取 Z.ai 模型列表失败: {e}")
                return []
