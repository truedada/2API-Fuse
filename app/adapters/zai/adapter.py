# app/adapters/zai/adapter.py
import httpx
import json
import time
import uuid
import re
import datetime
import hashlib
import base64
from typing import Dict, Any, AsyncGenerator, List, Tuple, Optional
from loguru import logger
from urllib.parse import urlencode

from app.core.exceptions.definitions import ExternalServiceError
from app.adapters.base import BaseAdapter
from app.adapters.zai.sign import generate_zai_signature
from app.core.redis.connection import get_redis_client

class ZaiAdapter(BaseAdapter):
    """
    Z.ai (智谱海外版) 逆向适配器
    支持: 流式对话、思考过程 (Reasoning)、自动签名、自动获取并保存 user_id、多模态图片上传与缓存
    """
    
    # Z.ai 前端版本号 (Header requirement)
    X_FE_VERSION = "prod-fe-1.0.149"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = "https://chat.z.ai/api" # 基础路径
        # 从 credentials 中获取 user_id 和 token
        self.token = self.credentials.get("token") or self.credentials.get("api_key")
        self.user_id = self.credentials.get("user_id") # 必须提供，用于签名
        
        if not self.token:
            # 允许初始化时不报错，但在实际调用时会检查
            logger.warning("ZaiAdapter 初始化时未检测到 Token")

    def _build_headers(self, signature: str = None, is_upload: bool = False) -> Dict[str, str]:
        """构造请求头"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
            'Authorization': f'Bearer {self.token}',
            'X-FE-Version': self.X_FE_VERSION,
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
            'Cache-Control': 'no-cache'
        }
        
        # 上传文件时不能指定 Content-Type 为 json，httpx 会自动设置 multipart/form-data
        if not is_upload:
            headers['Content-Type'] = 'application/json'

        # 合并额外的 headers 配置
        if self.extra_config and "headers" in self.extra_config:
            headers.update(self.extra_config["headers"])
            
        if signature:
            headers['X-Signature'] = signature
        return headers

    def _get_last_user_message(self, messages: List[Dict]) -> str:
        """提取最后一条用户消息用于签名"""
        for msg in reversed(messages):
            if msg["role"] == "user":
                content = msg["content"]
                # 处理多模态格式，只提取文本用于签名
                if isinstance(content, list):
                    text = ""
                    for part in content:
                        if part.get("type") == "text":
                            text += part.get("text", "")
                    return text
                return str(content)
        return "你好" # Fallback

    def _get_time_variables(self) -> Dict[str, str]:
        """生成 Payload 中需要的各种时间变量"""
        now = datetime.datetime.now()
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        return {
            "current_datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "current_date": now.strftime("%Y-%m-%d"),
            "current_time": now.strftime("%H:%M:%S"),
            "current_weekday": now.strftime("%A"),
            "local_time": now.isoformat() + "Z", # 模拟 ISO 格式
            "utc_time": utc_now.strftime("%a, %d %b %Y %H:%M:%S GMT")
        }

    def _sanitize_reasoning(self, text: str) -> str:
        """
        清洗思考过程文本
        去除 HTML 标签 (<details>, <summary>) 和 Markdown 引用符号
        """
        if not text:
            return ""
        
        # 移除 HTML 标签
        clean = re.sub(r'<details[^>]*>', '', text)
        clean = re.sub(r'<summary[^>]*>.*?</summary>', '', clean, flags=re.DOTALL)
        clean = clean.replace('</details>', '')
        
        # 移除 Markdown 引用符号 (例如 "\n> " 或 "> ")
        clean = re.sub(r'\n>\s?', '\n', clean)
        clean = re.sub(r'^>\s?', '', clean)
        
        return clean

    async def _upload_image(self, base64_str: str) -> Dict[str, Any]:
        """
        上传图片到 Z.ai 或从 Redis 缓存获取
        """
        # 1. 解析 Base64
        if "base64," in base64_str:
            header, encoded = base64_str.split("base64,", 1)
            # 尝试从 header 提取扩展名, 如 data:image/png;base64
            ext_match = re.search(r'data:image/(.*?);', header)
            ext = ext_match.group(1) if ext_match else "jpeg"
        else:
            encoded = base64_str
            ext = "jpeg"

        try:
            image_data = base64.b64decode(encoded)
        except Exception:
            logger.error("Base64 解码失败")
            raise ExternalServiceError("无效的图片数据")

        # 2. 计算 Hash 并检查 Redis
        md5_hash = hashlib.md5(image_data).hexdigest()
        redis_key = f"2api:zai:img:{md5_hash}"
        
        client = await get_redis_client()
        cached_data = await client.get(redis_key)
        
        if cached_data:
            logger.debug(f"Z.ai 图片缓存命中: {md5_hash}")
            return json.loads(cached_data)

        # 3. 上传图片
        filename = f"pasted_image_{int(time.time()*1000)}.{ext}"
        upload_url = f"{self.base_url}/v1/files/"
        
        # 构造 multipart/form-data
        files = {
            'file': (filename, image_data, f'image/{ext}')
        }
        
        headers = self._build_headers(is_upload=True)
        
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            try:
                resp = await http_client.post(upload_url, headers=headers, files=files)
                if resp.status_code != 200:
                    logger.error(f"Z.ai 图片上传失败 {resp.status_code}: {resp.text}")
                    raise ExternalServiceError("图片上传服务异常")
                
                file_obj = resp.json()
                # 确保返回的数据包含必要的字段，有时接口返回结构可能略有不同
                if "id" not in file_obj:
                    # 尝试处理嵌套结构
                    if "data" in file_obj and "id" in file_obj["data"]:
                        file_obj = file_obj["data"]
                
                # 4. 写入缓存 (3天过期)
                await client.set(redis_key, json.dumps(file_obj), ex=86400 * 3)
                logger.debug(f"Z.ai 图片上传成功并缓存: {filename}")
                return file_obj
                
            except httpx.RequestError as e:
                raise ExternalServiceError(f"图片上传网络错误: {e}")

    async def _process_messages_and_files(self, messages: List[Dict], current_msg_id: str) -> Tuple[List[Dict], List[Dict]]:
        """
        处理消息中的图片，上传并生成 files 列表，同时返回清洗后的纯文本消息
        """
        clean_messages = []
        files_list = []
        
        for msg in messages:
            new_msg = msg.copy()
            content = msg.get("content")
            
            # 仅处理 User 角色的多模态消息
            if msg["role"] == "user" and isinstance(content, list):
                text_content = ""
                
                for part in content:
                    msg_type = part.get("type")
                    
                    if msg_type == "text":
                        text_content += part.get("text", "")
                        
                    elif msg_type == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            # 上传图片
                            file_data = await self._upload_image(url)
                            
                            # 构造 Z.ai 需要的 file 对象结构
                            # 注意：url 字段通常是 /api/v1/files/{id}/content 形式
                            file_id = file_data.get("id")
                            cdn_url = file_data.get("meta", {}).get("cdn_url") or f"/api/v1/files/{file_id}/content"
                            
                            file_item = {
                                "type": "image",
                                "file": file_data, # 包含完整的回包数据
                                "id": file_id,
                                "url": f"/api/v1/files/{file_id}/content", # 使用相对路径作为引用
                                "name": file_data.get("filename", "image.jpg"),
                                "status": "uploaded",
                                "size": file_data.get("size", 0),
                                "itemId": str(uuid.uuid4()), # 前端生成的临时 ID
                                "media": "image",
                                "ref_user_msg_id": current_msg_id # 关联当前消息 ID
                            }
                            files_list.append(file_item)
                        elif url.startswith("http"):
                             # TODO: 处理网络图片链接（需下载后转传），暂时忽略或仅拼接链接
                             text_content += f" [Image: {url}] "

                new_msg["content"] = text_content
            
            clean_messages.append(new_msg)
            
        return clean_messages, files_list

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        非流式请求实现
        由于 Z.ai 原生是流式的，这里我们复用流式逻辑并拼接结果
        """
        content = ""
        reasoning_content = ""
        model = request_data.get("model")
        
        async for chunk in self.chat_completion_stream(request_data):
            if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                try:
                    data = json.loads(chunk[6:])
                    if "choices" in data:
                        delta = data["choices"][0]["delta"]
                        content += delta.get("content", "")
                        reasoning_content += delta.get("reasoning_content", "")
                except:
                    pass
        
        return {
            "id": str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning_content
                },
                "finish_reason": "stop"
            }]
        }

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        流式对话核心逻辑
        """
        raw_messages = request_data.get("messages", [])
        model = request_data.get("model") # 默认模型
        
        # 1. 准备参数
        timestamp_ms = int(time.time() * 1000)
        request_id = str(uuid.uuid4())
        
        # 生成当前用户消息 ID，用于关联上传的文件
        current_user_message_id = str(uuid.uuid4())
        
        # 2. 处理图片上传和消息清洗
        # 这一步会修改 messages 结构为纯文本，并生成 files 列表
        messages, files = await self._process_messages_and_files(raw_messages, current_user_message_id)
        
        # 获取用于签名的最后一条用户文本
        user_input = self._get_last_user_message(messages)
        
        # 确保 user_id 存在
        if not self.user_id:
             logger.info("Z.ai user_id 缺失，尝试自动获取...")
             valid = await self.validate_credential()
             if not valid or not self.user_id:
                 raise ExternalServiceError("credentials 中的 user_id 缺失，且自动获取失败")

        # 生成时间相关的变量
        time_vars = self._get_time_variables()
        chat_id = str(uuid.uuid4())

        # 构造参数
        params = {
            'timestamp': str(timestamp_ms),
            'requestId': request_id,
            'user_id': self.user_id,
            'version': '0.0.1',
            'platform': 'web',
            'token': self.token,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
            'language': 'zh-CN',
            'languages': 'zh-CN,zh,zh-TW,zh-HK,en-US,en',
            'timezone': 'Asia/Shanghai',
            'cookie_enabled': 'true',
            'screen_width': '1920',
            'screen_height': '1080',
            'screen_resolution': '1920x1080',
            'viewport_height': '906',
            'viewport_width': '1005',
            'viewport_size': '1005x906',
            'color_depth': '24',
            'pixel_ratio': '1.5',
            'current_url': f'https://chat.z.ai/c/{chat_id}',
            'pathname': f'/c/{chat_id}',
            'search': '',
            'hash': '',
            'host': 'chat.z.ai',
            'hostname': 'chat.z.ai',
            'protocol': 'https:',
            'referrer': '',
            'title': 'Z.ai Chat - Free AI powered by GLM-4.6 & GLM-4.5',
            'timezone_offset': '-480',
            'local_time': time_vars['local_time'],
            'utc_time': time_vars['utc_time'],
            'is_mobile': 'false',
            'is_touch': 'false',
            'max_touch_points': '5',
            'browser_name': 'Firefox',
            'os_name': 'Windows',
            'signature_timestamp': str(timestamp_ms), 
        }

        # 3. 生成签名
        try:
            signature = generate_zai_signature(user_input, params)
        except Exception as e:
            logger.error(f"Z.ai 签名生成失败: {e}")
            raise ExternalServiceError("签名算法执行失败")

        # 4. 处理 Thinking 开关逻辑
        enable_thinking = False
        
        # 优先检查 thinking 字典参数
        thinking_param = request_data.get("thinking")
        if not thinking_param:
            thinking_param = request_data.get("extra_body", {}).get("thinking")
        
        if isinstance(thinking_param, dict) and thinking_param.get("type") == "enabled":
            enable_thinking = True
        
        # 兼容旧的布尔值开关
        if request_data.get("enable_thinking") is True:
            enable_thinking = True

        # 5. 构造请求体
        payload = {
            "stream": True,
            "model": model,
            "messages": messages, # 使用清洗后的纯文本消息
            "signature_prompt": user_input,
            "params": {},
            "files": files, # 注入上传的文件列表
            "current_user_message_id": current_user_message_id, # 注入当前消息ID
            "features": {
                "image_generation": False,
                "web_search": False,
                "auto_web_search": False,
                "preview_mode": True,
                "flags": [],
                "enable_thinking": enable_thinking 
            },
            "variables": {
                "{{USER_NAME}}": "user",
                "{{USER_LOCATION}}": "Unknown",
                "{{CURRENT_DATETIME}}": time_vars['current_datetime'],
                "{{CURRENT_DATE}}": time_vars['current_date'],
                "{{CURRENT_TIME}}": time_vars['current_time'],
                "{{CURRENT_WEEKDAY}}": time_vars['current_weekday'],
                "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
                "{{USER_LANGUAGE}}": "zh-CN"
            },
            "background_tasks": {"title_generation": True, "tags_generation": True}
        }

        url = f"{self.base_url}/v2/chat/completions?{urlencode(params)}"
        headers = self._build_headers(signature)
        headers['Referer'] = params['current_url']

        final_usage = None # 用于缓存 usage 信息

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        logger.error(f"Z.ai 请求错误 {response.status_code}: {err_text}")
                        raise ExternalServiceError(f"Z.ai 上游返回错误: {response.status_code}")

                    async for line in response.aiter_lines():
                        if not line: continue
                        
                        if line.startswith("data: "):
                            raw_data = line[6:]
                            if raw_data.strip() == "[DONE]":
                                yield "data: [DONE]\n\n"
                                break
                            
                            try:
                                json_data = json.loads(raw_data)
                                
                                msg_type = json_data.get("type")
                                inner_data = json_data.get("data", {})
                                
                                # --- 1. 错误处理 ---
                                if "error" in inner_data:
                                    error_info = inner_data["error"]
                                    if error_info.get("code") == "INTERNAL_ERROR":
                                        logger.info(f"Z.ai 忽略内部错误信号: {error_info}")
                                    else:
                                        logger.error(f"Z.ai 运行时错误: {error_info}")
                                        continue

                                # --- 2. 内容处理 ---
                                if msg_type == "chat:completion":
                                    phase = inner_data.get("phase")
                                    delta_content = inner_data.get("delta_content", "")
                                    
                                    # 构造标准 OpenAI Chunk
                                    openai_chunk = {
                                        "id": request_id,
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": model,
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": None}]
                                    }

                                    if phase == "thinking":
                                        # --- 思考阶段清理 ---
                                        if delta_content:
                                            clean = self._sanitize_reasoning(delta_content)
                                            if clean:
                                                openai_chunk["choices"][0]["delta"]["reasoning_content"] = clean
                                                yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                    
                                    elif phase == "answer":
                                        # --- 回答阶段 ---
                                        
                                        # 特殊处理：检查 edit_content 中是否包含思考过程的尾部 (edit_index 事件)
                                        # 即使是 answer 阶段，upstream 也可能通过 edit_content 发送最后一段思考内容
                                        if "edit_content" in inner_data:
                                            edit_val = inner_data["edit_content"]
                                            if edit_val and "</details>" in edit_val:
                                                # 提取并发送这部分思考内容，防止丢失
                                                clean_tail = self._sanitize_reasoning(edit_val)
                                                if clean_tail:
                                                    tail_chunk = openai_chunk.copy()
                                                    tail_chunk["choices"] = [{"index": 0, "delta": {"reasoning_content": clean_tail}, "finish_reason": None}]
                                                    yield f"data: {json.dumps(tail_chunk, ensure_ascii=False)}\n\n"

                                        # 处理正常的回答内容
                                        if delta_content:
                                            openai_chunk["choices"][0]["delta"]["content"] = delta_content
                                            yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                    
                                    elif phase == "other":
                                        # --- 其他阶段 (Usage 和 补漏文本) ---
                                        
                                        # 情况A: 捕获 usage 并缓存
                                        if "usage" in inner_data:
                                            final_usage = inner_data["usage"]

                                        # 情况B: 捕获 edit_content (补漏文本)
                                        if "edit_content" in inner_data:
                                            content_piece = inner_data["edit_content"]
                                            # 如果不是思考结束的标签，则认为是正文补漏
                                            if content_piece and "</details>" not in content_piece:
                                                openai_chunk["choices"][0]["delta"]["content"] = content_piece
                                                yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"

                                # --- 3. 结束信号处理 (放在内容处理之后) ---
                                if inner_data.get("done") is True:
                                    # 构建最后一个包，带上 finish_reason 和 usage (如果有)
                                    final_chunk = {
                                        "id": request_id,
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": model,
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                                    }
                                    if final_usage:
                                        final_chunk["usage"] = final_usage
                                    
                                    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                                    yield "[DONE]\n\n"
                                    break

                            except json.JSONDecodeError:
                                continue
                                
            except httpx.RequestError as e:
                raise ExternalServiceError(f"请求 Z.ai 时发生网络错误: {e}")

    # --- 管理接口 ---

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