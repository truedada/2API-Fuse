# app/utils/converters/gemini.py

import json
import uuid
import time
import base64
import httpx
import re
import hashlib  # 用于生成稳定的 Tool Call ID
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
    - 支持通过模型后缀 (-maxthinking, -nothinking) 自动控制思考预算
    - [增强] 增强的 Tool Call ID 映射与流式 ID 稳定性
    - [增强] 原生 Google Search 支持 (通过 web_search 工具触发)
    - [增强] 严格的 JSON Schema 转换 (兼容 Node.js SDK 逻辑)
    """

    # Gemini 安全设置：默认全部放开，防止因安全策略导致的拒答
    DEFAULT_SAFETY_SETTINGS = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    ]

    # Gemini Schema Type 枚举 (用于 Schema 规范化)
    TYPE_ENUMS = {
        "STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "OBJECT"
    }

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
                
                # 策略 3: [增强] 基于顺序的容错回溯。
                # 当 tool_call_id 缺失时 (常见于某些客户端)，我们假设 tool response 的顺序
                # 与上一个 assistant 发出的 tool_calls 列表的顺序一致。
                if not func_name:
                    last_assistant_tc_index = -1
                    for j in range(i - 1, -1, -1):
                        msg_j = messages[j]
                        if msg_j.get("role") == "assistant" and msg_j.get("tool_calls"):
                            last_assistant_tc_index = j
                            break

                    if last_assistant_tc_index != -1:
                        assistant_tool_calls = messages[last_assistant_tc_index].get("tool_calls", [])

                        # 计算自上次 assistant tool_calls 以来，这是第几个 "tool" 角色的消息
                        tool_response_ordinal = 0
                        for j in range(last_assistant_tc_index + 1, i + 1):
                            if messages[j].get("role") == "tool":
                                tool_response_ordinal += 1

                        # 如果序号在有效范围内，则按顺序匹配函数名
                        if 0 < tool_response_ordinal <= len(assistant_tool_calls):
                            matched_tool_call = assistant_tool_calls[tool_response_ordinal - 1]
                            func_name = matched_tool_call.get("function", {}).get("name")
                            if func_name:
                                logger.info(
                                    f"Tool ID '{tool_call_id}' 缺失或未在映射中找到。"
                                    f"根据消息顺序，成功推断为第 {tool_response_ordinal} 个工具调用: '{func_name}'"
                                )

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

        # --- D. 处理 Thinking Config ---
        # 优先级：模型后缀变体 > 用户传入的 extra_body > 默认无
        
        final_budget = None
        final_include_thoughts = True

        if model_name.endswith("-maxthinking"):
            # 变体 1: 满血思考 (Max Budget)
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

        # 3. 处理 Tools 定义 (包含 Function Calling 和 Web Search)
        # [修改] 参考 Node.js 逻辑，分离常规工具和 Google Search 工具
        tools_list = request_dict.get("tools")
        if tools_list:
            gemini_tools_payload = []
            
            # 检测是否请求了 Web Search (约定工具名: web_search, google_search, google_search_retrieval)
            has_web_search = any(
                t.get("function", {}).get("name") in ["web_search", "google_search", "google_search_retrieval"]
                for t in tools_list
            )
            
            # 转换常规 Function Calling
            gemini_funcs = GeminiConverter._convert_tools_definition(tools_list)
            if gemini_funcs:
                # 注意：在 Gemini 的 tools 列表中，function_declarations 和 googleSearch 是并列的对象
                gemini_tools_payload.append({"function_declarations": gemini_funcs})
            
            # 如果请求了 Web Search，添加原生 Google Search 工具
            if has_web_search:
                gemini_tools_payload.append({"googleSearch": {}})

            if gemini_tools_payload:
                payload["tools"] = gemini_tools_payload

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

            # [新增] 处理非流式的 Grounding Metadata (Citation)
            grounding_metadata = candidate.get("groundingMetadata")
            if grounding_metadata:
                # OpenAI 没有标准引用字段，这里放在 message 的 extensions 字段或 context 中
                # 为了兼容性，这里暂不强行修改 content，仅做记录。
                # 如果需要，可以将引用追加到 content 文本末尾。
                pass

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
                
                # [新增] 获取 Grounding Metadata (搜索来源)
                grounding_metadata = candidate.get("groundingMetadata")
                
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
                        # 使用 hash(req_id + func_name) 来生成确定性 ID。
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
                
                # [新增] 处理 Web Search 引用 (放入 delta 扩展字段)
                if grounding_metadata:
                    citations = []
                    for chunk in grounding_metadata.get("groundingChunks", []):
                        if "web" in chunk:
                            citations.append({
                                "url": chunk["web"].get("uri"),
                                "title": chunk["web"].get("title"),
                                "text": "Web Source"
                            })
                    if citations:
                        # 放在 delta.citations 字段，部分支持扩展字段的客户端可显示
                        delta["citations"] = citations

                # 构建 choices 列表
                chunk_response["choices"].append({
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason
                })
            
            # 3. 验证返回有效性
            # 如果既没有 choices (content) 也没有 usage，则视为无效 Chunk
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
        清洗掉 Gemini 不支持的 schema 字段，并过滤掉已处理的 web_search
        """
        funcs = []
        for t in openai_tools:
            if t.get("type") != "function":
                continue
            
            func_name = t.get("function", {}).get("name")
            
            # [修改] 如果是 web_search 类工具，跳过（因为已经在上层 payload 处理为 googleSearch）
            if func_name in ["web_search", "google_search", "google_search_retrieval"]:
                continue
                
            f_def = t.get("function", {})
            params = f_def.get("parameters", {})
            
            # [修改] 使用增强的 Schema 处理逻辑
            cleaned_params = GeminiConverter._process_json_schema(params)
            
            funcs.append({
                "name": func_name,
                "description": f_def.get("description"),
                "parameters": cleaned_params
            })
            
        return funcs

    @staticmethod
    def _process_json_schema(schema: Dict) -> Dict:
        """
        [新增] 深度清洗并转换 JSON Schema 以适配 Gemini (参考 Node.js SDK 逻辑)
        1. 处理 type: ["string", "null"] -> nullable: true
        2. 将类型转换为大写 (string -> STRING)
        3. 处理 anyOf 中包含 null 的情况
        4. 递归处理 properties 和 items
        """
        if not isinstance(schema, dict):
            return schema

        gen_ai_schema = {}
        
        # 1. 提取并转换 Type
        original_type = schema.get("type")
        
        if original_type:
            if isinstance(original_type, list):
                # 处理数组类型 (e.g., ["string", "null"])
                GeminiConverter._flatten_type_array(original_type, gen_ai_schema)
            elif original_type == "null":
                # 单独的 null 类型是不允许的，但在 logic 中可能会被上层 anyOf 处理
                pass
            else:
                # 单个类型转大写
                upper_type = original_type.upper()
                gen_ai_schema["type"] = upper_type if upper_type in GeminiConverter.TYPE_ENUMS else "TYPE_UNSPECIFIED"

        # 2. 处理 Nullable (如果 schema 本身标记了 nullable)
        if schema.get("nullable"):
            gen_ai_schema["nullable"] = True

        # 3. 处理 anyOf (特别是处理 {anyOf: [{type: null}, {type: object}]})
        if "anyOf" in schema:
            any_of = schema["anyOf"]
            # 简单启发式：如果是 2 个元素且其中一个是 null，则转为 nullable
            if isinstance(any_of, list) and len(any_of) == 2:
                is_null_0 = any_of[0].get("type") == "null"
                is_null_1 = any_of[1].get("type") == "null"
                
                if is_null_0:
                    gen_ai_schema["nullable"] = True
                    # 递归处理另一个非 null 的 schema，合并其属性
                    merged = GeminiConverter._process_json_schema(any_of[1])
                    gen_ai_schema.update(merged)
                    return gen_ai_schema # 直接返回合并结果
                elif is_null_1:
                    gen_ai_schema["nullable"] = True
                    merged = GeminiConverter._process_json_schema(any_of[0])
                    gen_ai_schema.update(merged)
                    return gen_ai_schema

            # 如果不是 null pattern，则递归处理 list
            gen_ai_schema["anyOf"] = [GeminiConverter._process_json_schema(item) for item in any_of]

        # 4. 递归处理 properties, items
        if "properties" in schema:
            gen_ai_schema["properties"] = {
                k: GeminiConverter._process_json_schema(v) 
                for k, v in schema["properties"].items()
            }
            
        if "items" in schema:
            # items 可能是 dict 或 list
            if isinstance(schema["items"], dict):
                gen_ai_schema["items"] = GeminiConverter._process_json_schema(schema["items"])
            elif isinstance(schema["items"], list):
                # Gemini 不太支持 items 为数组 (Tuple validation)，但尽量转换
                gen_ai_schema["items"] = [GeminiConverter._process_json_schema(i) for i in schema["items"]]

        # 5. 复制其他重要字段 (并过滤黑名单)
        # Gemini 不支持: title, default, examples, additionalProperties, $schema
        ALLOWED_KEYS = ["description", "required", "enum", "format"]
        for k in ALLOWED_KEYS:
            if k in schema:
                gen_ai_schema[k] = schema[k]
        
        # 6. 处理 enum 的值 (Gemini 要求 enum 必须与 type 匹配，通常是 STRING)
        if "enum" in gen_ai_schema and "type" not in gen_ai_schema:
             # 如果有 enum 但没 type，通常推断为 STRING
             gen_ai_schema["type"] = "STRING"

        return gen_ai_schema

    @staticmethod
    def _flatten_type_array(type_list: List[str], schema_obj: Dict):
        """
        [新增] 将类型数组转换为 Gemini 兼容格式
        ["string", "null"] -> type="STRING", nullable=True
        ["string", "integer"] -> anyOf=[{type: STRING}, {type: INTEGER}]
        """
        # 1. 检查是否包含 null
        if "null" in type_list:
            schema_obj["nullable"] = True
        
        # 2. 过滤掉 null
        valid_types = [t for t in type_list if t != "null"]
        
        if len(valid_types) == 1:
            upper = valid_types[0].upper()
            schema_obj["type"] = upper if upper in GeminiConverter.TYPE_ENUMS else "TYPE_UNSPECIFIED"
        elif len(valid_types) > 1:
            # 多个非 null 类型，必须转为 anyOf
            schema_obj["anyOf"] = []
            for t in valid_types:
                upper = t.upper()
                t_val = upper if upper in GeminiConverter.TYPE_ENUMS else "TYPE_UNSPECIFIED"
                schema_obj["anyOf"].append({"type": t_val})

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