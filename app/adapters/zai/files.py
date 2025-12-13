# app/adapters/zai/files.py
import base64
import hashlib
import time
import json
import re
import httpx
from typing import Dict, Any
from loguru import logger

from app.core.exceptions.definitions import ExternalServiceError
from app.core.redis.connection import get_redis_client
from app.adapters.zai.constants import DEFAULT_BASE_URL
async def upload_image(base64_str: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """
    上传图片到 Z.ai 或从 Redis 缓存获取
    :param base64_str: 图片base64字符串
    :param headers: 基础请求头（需包含认证信息，但不包含 Content-Type: json）
    """
    # 1. 解析 Base64
    if "base64," in base64_str:
        header_part, encoded = base64_str.split("base64,", 1)
        # 尝试从 header 提取扩展名, 如 data:image/png;base64
        ext_match = re.search(r'data:image/(.*?);', header_part)
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
    upload_url = f"{DEFAULT_BASE_URL}/v1/files/"
    
    # 构造 multipart/form-data
    files = {
        'file': (filename, image_data, f'image/{ext}')
    }
    
    # 复制 headers 避免修改原引用，且确保由 httpx 处理 boundary
    upload_headers = headers.copy()
    if 'Content-Type' in upload_headers:
        del upload_headers['Content-Type']
    
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        try:
            resp = await http_client.post(upload_url, headers=upload_headers, files=files)
            if resp.status_code != 200:
                logger.error(f"Z.ai 图片上传失败 {resp.status_code}: {resp.text}")
                raise ExternalServiceError("图片上传服务异常")
            
            file_obj = resp.json()
            # 确保返回的数据包含必要的字段
            if "id" not in file_obj:
                if "data" in file_obj and "id" in file_obj["data"]:
                    file_obj = file_obj["data"]
            
            # 4. 写入缓存 (3天过期)
            await client.set(redis_key, json.dumps(file_obj), ex=86400 * 3)
            logger.debug(f"Z.ai 图片上传成功并缓存: {filename}")
            return file_obj
            
        except httpx.RequestError as e:
            raise ExternalServiceError(f"图片上传网络错误: {e}")