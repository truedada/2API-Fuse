# app/utils/converters/gemini.py

import json
import uuid
import time
import base64
import httpx
import re
import hashlib  # 新增：用于生成稳定的 Tool Call ID
from typing import List, Dict, Any, Optional, Union, Tuple
from loguru import logger

class GeminiConverter:
    """
    OpenAI <-> Google Gemini API 格式全能转换器
    
    特点：
    - 支持 Gemini 2.0 Flash Thinking (reasoning_content)
    - 支持 Function Calling 双向转换
    - 支持 多模态 (图片 URL 自动转 Base64)
    - 严格的错误处理和日志记录
    - [新增] 支持通过模型后缀 (-maxthinking, -nothinking) 自动控制思考预算
    - [修复] 增强的 Tool Call ID 映射与流式 ID 稳定性
    """

    # Gemini 安全设置：默认全部放开，防止因安全策略导致的拒答
    DEFAULT_SAFETY_SETTINGS = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    ]

    # =========================================================================
    # Request Conversion (OpenAI -> Gemini)
    # =========================================================================

    @staticmethod
    async def openai_to_gemini_payload(request_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 OpenAI 格式的请求字典转换为 Gemini API 格式
        """
        messages = request_dict.get("messages", [])
        model_name = request_dict.get("model", "").lower()
        
        contents = []
        system_instructions = []
        
        # --- [修复步骤 1] 建立全局 Tool ID 到 Function Name 的映射 ---
        # OpenAI 的 tool 消息只带 ID，Gemini 需要 Name。
        # 简单的回溯查找容易失败，这里预先扫描所有 assistant 消息，记录下 ID 和 Name 的对应关系。
        tool_id_to_name_map = {}
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    t_id = tc.get("id")
                    f_name = tc.get("function", {}).get("name")
                    if t_id and f_name:
                        tool_id_to_name_map[t_id] = f_name

        # 1. 遍历消息列表
        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content")
            
            # --- System Prompt 处理 ---
            # Gemini 将 system 放在顶层字段，而非 contents 数组
            if role == "system":
                if isinstance(content, str):
                    system_instructions.append(content)
                elif isinstance(content, list):
                    # 处理罕见的 system 多模态，通常只取文本
                    texts = [p["text"] for p in content if p.get("type") == "text"]
                    system_instructions.extend(texts)
                continue

            # --- 角色映射 ---
            # OpenAI: assistant -> Gemini: model
            # OpenAI: tool -> Gemini: function (在 parts 中体现为 functionResponse)
            # 其他情况归为 user
            gemini_role = "model" if role == "assistant" else "user"
            
            parts = []

            # --- A. 处理 Tool Execution Results (Role: tool) ---
            # OpenAI 格式: role="tool", tool_call_id="call_xxx", content="{...}"
            # Gemini 格式: role="user", parts=[{functionResponse: {name: "func_name", response: {...}}}]
            if role == "tool":
                # 【难点】Gemini 必须要有 function name，但 OpenAI 的 tool 消息只有 id。
                tool_call_id = msg.get("tool_call_id")
                
                # 策略 1: 尝试从 msg 中获取 'name' (部分客户端如 Cherry Studio/NextChat 可能会透传)
                func_name = msg.get("name")
                
                # 策略 2: [修复] 使用全局映射表查找 (最准确的方式)
                if not func_name and tool_call_id:
                    func_name = tool_id_to_name_map.get(tool_call_id)
                
                # 策略 3: [修复] 容错回溯 (如果表里没有，或者 ID 为 None，尝试看上一条消息)
                # 这种情况常见于 Claude Code 等客户端发送了不带 ID 的 tool 消息
                if not func_name:
                    # 检查上一条消息是否是 assistant 且只有一个工具调用
                    prev_msg = messages[i-1] if i > 0 else {}
                    if prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls"):
                        tcs = prev_msg["tool_calls"]
                        if len(tcs) == 1:
                            func_name = tcs[0]["function"]["name"]
                            logger.warning(f"Tool ID {tool_call_id} 未在映射中找到，根据上下文推断为: {func_name}")

                # --- [修复步骤 2] 智能降级处理 ---
                # 如果经过上述所有策略仍然找不到 func_name (例如 ID 为 None 且无法推断)，
                # 绝对不能发送 name=None 或 name="unknown" 的 functionResponse，这会导致 Gemini 报错或幻觉。
                # 此时我们将这条消息转换为普通的 User Text Message，保留内容但改变形式。
                if not func_name:
                    logger.warning(f"无法找到 Tool Response (ID: {tool_call_id}) 对应的函数名，降级为文本消息以避免报错。")
                    
                    # 尝试格式化内容为字符串
                    content_str = str(content)
                    if isinstance(content, (dict, list)):
                        try:
                            content_str = json.dumps(content, ensure_ascii=False)
                        except:
                            pass
                    
                    # 构造一个带标识的文本消息，让模型知道这是工具输出
                    parts.append({"text": f"[Tool Output]\n{content_str}"})
                    contents.append({"role": "user", "parts": parts})
                    continue

                # 如果找到了函数名，正常构造 functionResponse
                try:
                    # Gemini 要求 response 必须是 Object，不能是 String
                    if isinstance(content, str):
                        try:
                            response_obj = json.loads(content)
                        except json.JSONDecodeError:
                            response_obj = {"result": content}
                    else:
                        response_obj = content if content is not None else {"result": "success"}
                except Exception:
                    response_obj = {"result": str(content)}

                parts.append({
                    "functionResponse": {
                        "name": func_name,
                        "response": response_obj
                    }
                })
                
                # Tool Response 在 Gemini 中必须属于 user 角色
                contents.append({"role": "user", "parts": parts})
                continue

            # --- B. 处理常规内容 (文本 & 图片) ---
            if content:
                if isinstance(content, str):
                    parts.append({"text": content})
                elif isinstance(content, list):
                    for item in content:
                        item_type = item.get("type")
                        if item_type == "text":
                            parts.append({"text": item["text"]})
                        elif item_type == "image_url":
                            # 处理图片
                            img_data = await GeminiConverter._process_image(item["image_url"]["url"])
                            if img_data:
                                parts.append(img_data)

            # --- C. 处理 Assistant 的工具调用 (Tool Calls) ---
            # OpenAI: tool_calls=[{id:..., function: {name:..., arguments:...}}]
            # Gemini: parts=[{functionCall: {name:..., args:...}}]
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except json.JSONDecodeError:
                        logger.warning(f"Tool arguments 解析失败: {args_str}")
                        args = {}
                    
                    parts.append({
                        "functionCall": {
                            "name": func.get("name"),
                            "args": args
                        }
                    })

            # 只有当 parts 不为空时才添加 (防止空消息报错)
            if parts:
                contents.append({"role": gemini_role, "parts": parts})
            elif gemini_role == "model":
                # 容错：Gemini 不允许 model 发送空内容，填充一个空格
                contents.append({"role": "model", "parts": [{"text": " "}]})

        # 2. 构造 Generation Config
        generation_config = {}
        if request_dict.get("temperature") is not None:
            generation_config["temperature"] = request_dict.get("temperature")
        if request_dict.get("max_tokens") is not None:
            generation_config["maxOutputTokens"] = request_dict.get("max_tokens")
        if request_dict.get("top_p") is not None:
            generation_config["topP"] = request_dict.get("top_p")
        if request_dict.get("stop"):
            stops = request_dict["stop"]
            generation_config["stopSequences"] = stops if isinstance(stops, list) else [stops]

        # --- D. 处理 Thinking Config (核心修改) ---
        # 优先级：模型后缀变体 > 用户传入的 extra_body > 默认无
        
        final_budget = None
        final_include_thoughts = True

        if model_name.endswith("-maxthinking"):
            # 变体 1: 满血思考 (Max Budget)
            # Gemini 2.0 目前最大支持 64k tokens 左右的思考，这里设为 64000 安全值
            if "flash" in model_name:
                final_budget = 24576
            else:
                final_budget = 32768
            final_include_thoughts = True
            
        elif model_name.endswith("-nothinking"):
            # 变体 2: 禁用思考
            final_budget = 0
            final_include_thoughts = False
            
        else:
            # 变体 3: 原始模式 (用户自定义)
            extra_body = request_dict.get("extra_body", {})
            if extra_body and "google" in extra_body:
                thinking_conf = extra_body["google"].get("thinking_config")
                if thinking_conf:
                    raw_budget = thinking_conf.get("thinking_budget")
                    try:
                        final_budget = int(raw_budget) if raw_budget is not None else 1024
                    except (ValueError, TypeError):
                        final_budget = 1024
                    
                    final_include_thoughts = thinking_conf.get("include_thoughts", True)

        # 只有确定有 thinking 配置时才写入
        if final_budget is not None:
            # 安全检查：如果 Budget <= 0，强制关闭思考
            if final_budget <= 0:
                final_include_thoughts = False
                final_budget = 0 # 规范化

            generation_config["thinkingConfig"] = {
                "thinkingBudget": final_budget,
                "includeThoughts": final_include_thoughts
            }

        payload = {
            "contents": contents,
            "generationConfig": generation_config,
            "safetySettings": GeminiConverter.DEFAULT_SAFETY_SETTINGS
        }

        # 添加 System Instruction
        if system_instructions:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_instructions)}]}

        # 3. 处理 Tools 定义
        if request_dict.get("tools"):
            gemini_tools = GeminiConverter._convert_tools_definition(request_dict["tools"])
            if gemini_tools:
                payload["tools"] = gemini_tools
                
            # 处理 tool_choice (Gemini 叫 toolConfig)
            tool_choice = request_dict.get("tool_choice")
            if tool_choice:
                payload["toolConfig"] = GeminiConverter._convert_tool_choice(tool_choice)

        return payload

    # =========================================================================
    # Response Conversion (Gemini -> OpenAI)
    # =========================================================================

    @staticmethod
    def gemini_response_to_openai(gemini_resp: Dict[str, Any], model: str) -> Dict[str, Any]:
        """
        将 Gemini 非流式响应转换为 OpenAI 格式
        """
        candidates = gemini_resp.get("candidates", [])
        choices = []
        
        usage = GeminiConverter._map_usage(gemini_resp.get("usageMetadata"))

        # 如果没有 candidates 但有 usage (可能是被 filter 了)，返回空响应或错误
        if not candidates and gemini_resp.get("promptFeedback"):
            # 处理安全拦截
            return {
                "id": f"chatcmpl-{uuid.uuid4()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Request blocked by safety filters."}, "finish_reason": "content_filter"}],
                "usage": usage
            }

        for idx, candidate in enumerate(candidates):
            parts = candidate.get("content", {}).get("parts", [])
            finish_reason = GeminiConverter._map_finish_reason(candidate.get("finishReason"))
            
            content_str = ""
            reasoning_content = ""
            tool_calls = []

            for part in parts:
                # 1. Function Call
                if "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:9]}", # Gemini 不返回 ID，随机生成
                        "type": "function",
                        "function": {
                            "name": fc.get("name"),
                            "arguments": json.dumps(fc.get("args", {}))
                        }
                    })
                    finish_reason = "tool_calls" # 强制覆盖结束原因
                
                # 2. Text & Thinking
                if "text" in part:
                    # Gemini 2.0 Thinking 逻辑: 检查 'thought' 字段
                    # 注意：GCLI/Internal API 的返回结构可能将 thought 标记在 part 属性中
                    if part.get("thought", False):
                        reasoning_content += part["text"]
                    else:
                        content_str += part["text"]

            message = {
                "role": "assistant",
                "content": content_str if content_str else None,
            }
            
            # 只有有内容时才添加字段
            if reasoning_content:
                message["reasoning_content"] = reasoning_content
            if tool_calls:
                message["tool_calls"] = tool_calls

            choices.append({
                "index": idx,
                "message": message,
                "finish_reason": finish_reason
            })

        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": choices,
            "usage": usage
        }

    @staticmethod
    def parse_gemini_stream_chunk(chunk_data: bytes, model: str, req_id: str) -> Optional[Dict[str, Any]]:
        """
        解析 Gemini SSE 流式 Chunk -> OpenAI Chunk
        修复：支持在同一个 Chunk 中同时包含内容(candidates)和Token统计(usageMetadata)
        """
        line = chunk_data.decode("utf-8").strip()
        if not line.startswith("data:"):
            return None
        
        json_str = line[6:].strip()
        if not json_str or json_str == "[DONE]":
            return None
        
        try:
            data = json.loads(json_str)
            
            # 初始化 OpenAI Chunk 结构
            chunk_response = {
                "id": req_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": []
            }

            # 1. 优先处理 Usage Metadata (无论是否有 content)
            # Gemini 流式返回中，usageMetadata 可能与最后一个 candidate 一起返回
            if data.get("usageMetadata"):
                chunk_response["usage"] = GeminiConverter._map_usage(data["usageMetadata"])

            # 2. 处理 Candidates (Content Delta)
            if data.get("candidates"):
                candidate = data["candidates"][0]
                parts = candidate.get("content", {}).get("parts", [])
                finish_reason = GeminiConverter._map_finish_reason(candidate.get("finishReason"))
                
                delta = {}
                content_delta = ""
                reasoning_delta = ""
                tool_calls_delta = []

                for part in parts:
                    # 处理流式工具调用
                    if "functionCall" in part:
                        fc = part["functionCall"]
                        func_name = fc.get("name", "unknown")
                        
                        # --- [修复步骤 3] 生成稳定的 Tool Call ID ---
                        # 在流式传输中，同一个工具调用的 ID 必须保持一致。
                        # 由于 parse_gemini_stream_chunk 是无状态的，我们使用 hash(req_id + func_name) 来生成确定性 ID。
                        # 这样即使 Gemini 分多次返回，或者客户端重新拼接，ID 也是固定的。
                        hash_input = f"{req_id}_{func_name}"
                        stable_id = f"call_{hashlib.md5(hash_input.encode()).hexdigest()[:10]}"

                        tool_calls_delta.append({
                            "index": 0,
                            "id": stable_id, # 使用稳定 ID
                            "type": "function",
                            "function": {
                                "name": func_name,
                                "arguments": json.dumps(fc.get("args", {}))
                            }
                        })
                    
                    # 处理文本和思考
                    if "text" in part:
                        if part.get("thought", False):
                            reasoning_delta += part["text"]
                        else:
                            content_delta += part["text"]

                if content_delta: delta["content"] = content_delta
                if reasoning_delta: delta["reasoning_content"] = reasoning_delta
                if tool_calls_delta: delta["tool_calls"] = tool_calls_delta
                
                # 构建 choices 列表
                chunk_response["choices"].append({
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason
                })
            
            # 3. 验证返回有效性
            # 如果既没有 choices (content) 也没有 usage，则视为无效 Chunk (OpenAI 客户端通常不喜欢空 choices 且无 usage 的包)
            if not chunk_response["choices"] and "usage" not in chunk_response:
                return None

            return chunk_response

        except Exception as e:
            # 这里的 log 可以改为 debug，防止流式数据中有少量格式噪音刷屏
            # logger.debug(f"Parse chunk failed: {e}")
            return None

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    async def _process_image(url: str) -> Optional[Dict[str, Any]]:
        """
        下载或解析图片 -> Gemini InlineData
        """
        try:
            mime_type = "image/jpeg"
            b64_data = ""

            if url.startswith("data:"):
                # Data URI
                header, b64_str = url.split(",", 1)
                mime_type = header.split(":")[1].split(";")[0]
                b64_data = b64_str
            else:
                # HTTP URL -> Download
                # 注意：生产环境建议增加 Redis 缓存图片的 base64，减少下载延迟
                async with httpx.AsyncClient(verify=False) as client:
                    resp = await client.get(url, timeout=15.0)
                    resp.raise_for_status()
                    
                    content_type = resp.headers.get("content-type")
                    if content_type:
                        mime_type = content_type
                    
                    b64_data = base64.b64encode(resp.content).decode("utf-8")
            
            return {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": b64_data
                }
            }
        except Exception as e:
            logger.error(f"Image processing failed ({url[:50]}...): {e}")
            return None

    @staticmethod
    def _convert_tools_definition(openai_tools: List[Dict]) -> List[Dict]:
        """
        OpenAI Tools -> Gemini FunctionDeclarations
        清洗掉 Gemini 不支持的 schema 字段
        """
        funcs = []
        for t in openai_tools:
            if t.get("type") != "function":
                continue
                
            f_def = t.get("function", {})
            params = f_def.get("parameters", {})
            
            # 清洗 parameters (Gemini 极其挑剔)
            cleaned_params = GeminiConverter._clean_schema(params)
            
            funcs.append({
                "name": f_def.get("name"),
                "description": f_def.get("description"),
                "parameters": cleaned_params
            })
            
        return [{"function_declarations": funcs}] if funcs else []

    @staticmethod
    def _clean_schema(schema: Dict) -> Dict:
        """
        递归清洗 JSON Schema，移除 Gemini 不支持的关键字
        """
        if not isinstance(schema, dict):
            return schema
            
        # Gemini 不支持的关键字黑名单
        UNSUPPORTED_KEYS = [
            "title", "default", "examples", "example", 
            "additionalProperties", "$schema", "strict"
        ]
        
        clean = {}
        for k, v in schema.items():
            if k in UNSUPPORTED_KEYS:
                continue
            
            if k == "type" and v == "object" and "properties" not in schema:
                 # Gemini 不喜欢空的 object 定义，有时需要处理
                 pass

            if isinstance(v, dict):
                clean[k] = GeminiConverter._clean_schema(v)
            elif isinstance(v, list):
                clean[k] = [GeminiConverter._clean_schema(i) if isinstance(i, dict) else i for i in v]
            else:
                clean[k] = v
        
        return clean

    @staticmethod
    def _convert_tool_choice(tool_choice: Union[str, Dict]) -> Dict:
        """映射 tool_choice 到 toolConfig"""
        if tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        elif tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif tool_choice == "required":
             return {"functionCallingConfig": {"mode": "ANY"}}
        elif isinstance(tool_choice, dict):
            # 指定特定函数
            func_name = tool_choice.get("function", {}).get("name")
            if func_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [func_name]
                    }
                }
        return {}

    @staticmethod
    def _map_usage(gemini_usage: Dict) -> Dict:
        if not gemini_usage:
            return None
        return {
            "prompt_tokens": gemini_usage.get("promptTokenCount", 0),
            "completion_tokens": gemini_usage.get("candidatesTokenCount", 0),
            "total_tokens": gemini_usage.get("totalTokenCount", 0)
        }

    @staticmethod
    def _map_finish_reason(reason: str) -> Optional[str]:
        mapping = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
            "OTHER": "stop",
            # Gemini 2.0 可能会返回一些新的状态
            "MALFORMED_FUNCTION_CALL": "stop" 
        }
        return mapping.get(reason)
