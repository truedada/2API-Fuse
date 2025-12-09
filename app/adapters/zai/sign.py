# app/adapters/zai/sign.py
import math
import hmac
import hashlib
import base64
from urllib.parse import quote

# Z.ai 前端硬编码的盐值 (如未来失效需更新)
SALT_KEY = "key-@@@@)))()((9))-xxxx&&&%%%%%"

def generate_zai_signature(user_input: str, params: dict) -> str:
    """
    生成 Z.ai API 请求所需的签名 (X-Signature)。
    
    Args:
        user_input: 用户最后一条发送的消息内容 (Prompt)
        params: 包含 timestamp, requestId, user_id 的字典
    
    Returns:
        HEX 格式的签名字符串
    """
    timestamp_str = str(params['timestamp'])
    timestamp_ms = int(timestamp_str)

    # --- 步骤 1: 生成动态密钥 k ---
    # JS: const f = Math.floor(o / 300000);
    f = math.floor(timestamp_ms / 300000)
    
    # JS: const k = E0.sha256.hmac(SALT, "" + f);
    k_hmac = hmac.new(SALT_KEY.encode('utf-8'), str(f).encode('utf-8'), hashlib.sha256)
    k = k_hmac.hexdigest()

    # --- 步骤 2: 构造待签名字符串 g ---
    # 参与排序的 Key (JS逆向逻辑限定)
    signature_keys = ['timestamp', 'requestId', 'user_id']
    
    sorted_pairs = []
    for key in sorted(signature_keys):
        # 确保值存在且转为字符串
        if key in params:
            sorted_pairs.append(f"{key},{params[key]}")
    
    # payload: "key,value,key,value"
    sorted_payload = ",".join(sorted_pairs)

    # Prompt Base64 编码
    p = base64.b64encode(user_input.encode('utf-8')).decode('utf-8')

    # r = timestamp
    r = timestamp_str
    
    # 组合: payload | prompt_base64 | timestamp
    g = f"{sorted_payload}|{p}|{r}"

    # --- 步骤 3: 计算最终签名 ---
    # HMAC-SHA256(k, g)
    final_hmac = hmac.new(k.encode('utf-8'), g.encode('utf-8'), hashlib.sha256)
    signature = final_hmac.hexdigest()

    return signature