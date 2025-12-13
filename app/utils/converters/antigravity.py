# app/utils/converters/antigravity.py
import uuid
import json
import traceback
from typing import Dict, Any, Tuple, Optional, List
from loguru import logger

from app.utils.converters.gemini import GeminiConverter
from app.adapters.antigravity import constants

class AntigravityConverter(GeminiConverter):
    """
    Antigravity 专用转换器
    继承自 GeminiConverter，处理特殊的封包格式和 Claude/Thinking 兼容性
    """

    @staticmethod
    async def openai_to_antigravity_payload(request_data: Dict[str, Any], project_id: str) -> Dict[str, Any]:
        """
        将 OpenAI 请求转换为 Antigravity 专用的嵌套 Payload
        """
        # 1. 复用父类方法生成标准的 Gemini Payload
        gemini_request = await GeminiConverter.openai_to_gemini_payload(request_data)

        # 2. 解析模型和 Thinking 模式
        # 获取用户请求的模型名称 (此时已经过 Platform 层映射)
        input_model = request_data.get("model", "")
        internal_model, thinking_mode = AntigravityConverter._parse_thinking_mode(input_model)

        # 3. 针对 Antigravity 的特殊处理
        # 移除 safetySettings (Antigravity 通常不需要显式传递，或者由网关控制)
        gemini_request.pop("safetySettings", None)

        # 3.1 强制设置 Tool Config 模式
        tool_config = gemini_request.setdefault("toolConfig", {})
        fc_config = tool_config.setdefault("functionCallingConfig", {})
        # 如果父类没有设置特定的 mode (如 ANY/NONE)，则默认为 VALIDATED
        if "mode" not in fc_config:
            fc_config["mode"] = "VALIDATED"

        # 3.2 [特殊修正] System Instruction 格式修正
        # Antigravity 要求 systemInstruction 必须包含 role: "user"
        if "systemInstruction" in gemini_request:
            gemini_request["systemInstruction"]["role"] = "user"

        # 4. 处理 Thinking 和 Claude 的兼容性配置
        is_claude = "claude" in internal_model.lower()
        # [修复] 传入 internal_model 以便在 _process_generation_config 中判断 Flash 模型
        AntigravityConverter._process_generation_config(gemini_request, is_claude, thinking_mode, internal_model)

        # 5. 如果是 Claude 模型，需要进行更深度的 Tools Schema 清洗
        if is_claude and "tools" in gemini_request:
            AntigravityConverter._deep_clean_claude_tools(gemini_request["tools"])

        # 6. 构造 Antigravity 外层封装
        # Session ID 生成逻辑，用于保持会话一致性
        n = uuid.uuid4().int & (1 << 63) - 1
        gemini_request["sessionId"] = f"-{n}"

        final_payload = {
            "model": internal_model,
            "userAgent": constants.USER_AGENT,
            "project": project_id,
            "requestId": f"agent-{uuid.uuid4()}",
            "request": gemini_request
        }

        return final_payload

    @staticmethod
    def parse_antigravity_stream_chunk(chunk_str: str, model: str, request_id: str) -> Optional[Dict[str, Any]]:
        """
        解析 Antigravity 的流式 Chunk
        Antigravity 的 chunk 格式通常是: data: {"response": { ... gemini_chunk ... }}
        """
        try:
            if not chunk_str or chunk_str == "[DONE]":
                return None
            
            # 这里的 chunk_str 通常是 "data: {...}" 格式，已经在 Adapter 层被提取了 data 内容
            # 但为了兼容 BaseAdapter 的流处理，我们假设传入的是原始 SSE line 或者 JSON 字符串
            
            if chunk_str.startswith("data:"):
                json_str = chunk_str[6:].strip()
            else:
                json_str = chunk_str

            if not json_str or json_str == "[DONE]":
                return None

            raw_data = json.loads(json_str)
            #logger.debug(f"Antigravity 流式Chunk: {raw_data}")
            
            # Antigravity 的核心逻辑：解包 "response" 字段
            # 有时候它直接返回 Gemini 格式，有时候包在 response 里
            actual_gemini_chunk = raw_data.get("response", raw_data)

            # 重新封装成标准 SSE 格式字符串，以便复用父类的解析逻辑
            # 父类 parse_gemini_stream_chunk 接收的是 bytes 类型的 "data: {...}"
            reconstructed_line = f"data: {json.dumps(actual_gemini_chunk)}"
            
            return GeminiConverter.parse_gemini_stream_chunk(
                reconstructed_line.encode('utf-8'),
                model,
                request_id
            )

        except json.JSONDecodeError:
            return None
        except Exception as e:
            logger.debug(f"Antigravity Chunk Parse Error: {e}")
            return None

    # ==========================
    # Helper Methods (辅助方法)
    # ==========================

    @staticmethod
    def _parse_thinking_mode(model_name: str) -> Tuple[str, str]:
        """
        解析模型名称中的 thinking 后缀。
        Returns: (real_internal_model_id, thinking_mode)
        """
        # 判断是否为 Claude 系列
        is_claude = "claude" in model_name.lower()

        # 模式 1: Max Thinking (32k Budget)
        # 这是一个功能性后缀，无论是否为 Claude 都应当剥离，
        # 因为 Antigravity 不存在 xxx-maxthinking 的真实模型ID。
        # 剥离后通过 thinkingConfig 注入 32k budget。
        if model_name.endswith("-maxthinking"):
            return model_name.replace("-maxthinking", ""), "max"
        
        # 模式 2: No Thinking (0 Budget)
        if model_name.endswith("-nothinking"):
            return model_name.replace("-nothinking", ""), "none"
            
        # 模式 3: Standard Thinking (1k Budget / Default)
        if model_name.endswith("-thinking"):
            if is_claude:
                # [关键修正] Claude 模型保留 -thinking 后缀
                # 因为 claude-sonnet-4-5-thinking 是 Antigravity 真实存在的模型 ID
                # 不应剥离后缀，否则会变成普通版
                return model_name, "standard"
            else:
                # Gemini 等模型，-thinking 通常是功能性后缀，需要剥离
                return model_name.replace("-thinking", ""), "standard"
            
        return model_name, "default"

    @staticmethod
    def _process_generation_config(req: Dict, is_claude: bool, thinking_mode: str, model_name: str):
        """处理 generationConfig，特别是 Thinking 和 Claude 的冲突"""
        
        # [针对 Computer Use 模型的特殊修复]
        # rev19-uic3-1p (Gemini 2.5 Computer Use) 强制要求 Thinking Budget > 0
        # 如果用户未指定模式 (default)，则强制升级为 max
        if "rev19-uic3-1p" in model_name and thinking_mode == "default":
            thinking_mode = "max"

        gen_config = req.setdefault("generationConfig", {})

        # 1. 非 Claude 模型移除 maxOutputTokens (Antigravity 可能会报错)
        if not is_claude and "maxOutputTokens" in gen_config:
            del gen_config["maxOutputTokens"]

        # 2. Thinking Budget 设置
        if thinking_mode == "max":
            # [修复] 适配 Flash 模型限额
            # Flash 系列模型最大支持 24576，其他 Pro 模型支持 32768
            if "flash" in model_name.lower():
                gen_config["thinkingConfig"] = {"thinkingBudget": 24576, "includeThoughts": True}
            else:
                gen_config["thinkingConfig"] = {"thinkingBudget": 32768, "includeThoughts": True}
        
        elif thinking_mode == "standard":
            # 标准思考模式：Claude 默认自带思考，Gemini 需要注入 budget
            gen_config["thinkingConfig"] = {"thinkingBudget": 1024, "includeThoughts": True}
        
        elif thinking_mode == "none":
            # 显式关闭思考
            if "thinkingConfig" in gen_config:
                del gen_config["thinkingConfig"]
        
        elif thinking_mode == "default":
            # 清理不支持的字段 (如 thinkingLevel)
            if "thinkingConfig" in gen_config and "thinkingLevel" in gen_config["thinkingConfig"]:
                del gen_config["thinkingConfig"]["thinkingLevel"]

        # 3. Claude 兼容性修复 
        # Claude 在开启思考时，严禁传递 topP 和 topK，否则 API 报错
        is_thinking_enabled = (
            thinking_mode in ["max", "standard"] or 
            (thinking_mode == "default" and gen_config.get("thinkingConfig", {}).get("includeThoughts"))
        )

        if is_claude and is_thinking_enabled:
            gen_config.pop("topP", None)
            gen_config.pop("topK", None)

    @staticmethod
    def _deep_clean_claude_tools(tools: List[Dict]):
        """
        Claude 通过 Antigravity 调用时，对 JSON Schema 有极严格的校验。
        需要移除所有不支持的验证关键字。
        """
        for tool in tools:
            for func in tool.get("function_declarations", []):
                if "parameters" in func:
                    AntigravityConverter._clean_schema_recursive(func["parameters"])

    @staticmethod
    def _clean_schema_recursive(schema: Dict):
        if not isinstance(schema, dict):
            return

        # Antigravity/Claude Schema 黑名单
        blacklist = [
            "$schema", "maxItems", "minItems", "minLength", "maxLength",
            "exclusiveMinimum", "exclusiveMaximum", "$ref", "$defs",
            "additionalProperties", "uniqueItems", "default", "title"
        ]
        for key in blacklist:
            schema.pop(key, None)

        # 处理 anyOf (Claude 偏好单一结构)
        if schema.get("anyOf") and isinstance(schema["anyOf"], list):
            # 取第一个非 null 的定义，简化结构
            first_valid = next((x for x in schema["anyOf"] if x.get("type") != "null"), schema["anyOf"][0])
            del schema["anyOf"]
            schema.update(first_valid)
            AntigravityConverter._clean_schema_recursive(schema)
            return

        for prop in schema.get("properties", {}).values():
            AntigravityConverter._clean_schema_recursive(prop)
        
        if "items" in schema:
            if isinstance(schema["items"], dict):
                AntigravityConverter._clean_schema_recursive(schema["items"])
            elif isinstance(schema["items"], list):
                for item in schema["items"]:
                    AntigravityConverter._clean_schema_recursive(item)