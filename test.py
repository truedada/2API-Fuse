import httpx
import time
import math
import base64
import uuid
import asyncio
import hmac
import hashlib
from urllib.parse import urlencode

# --- 配置信息 ---
SALT_KEY = "key-@@@@)))()((9))-xxxx&&&%%%%%"
AUTH_TOKEN = "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjQ0NGFiNmJmLWIxZGMtNDExYS1hNjhhLWVlNDk1ZjFmYzMyZiIsImVtYWlsIjoiYTMzNDExZDBhODg2QGJiZmNoYXJpdHkub3JnIn0.zCvzgMbnE5pbbvBvXvLBEQzm5nW_I65Lt5PC3qvylH5Byt6-7plAfUeF_rPMkCWeuWz1P3Xa63K2dIDcRQYVzQ"
USER_ID = "444ab6bf-b1dc-411a-a68a-ee495f1fc32f"
BASE_URL = "https://chat.z.ai/api/v2/chat/completions"

def generate_signature(user_input: str, params: dict) -> tuple[str, str]:
    """
    根据 JS 逆向逻辑生成 API 请求签名。
    修正点：只有 timestamp, requestId, user_id 参与 sorted_payload 的计算。
    """
    timestamp_str = params['timestamp']
    timestamp_ms = int(timestamp_str)

    # --- 步骤 1: 生成动态密钥 k ---
    # 对应 JS: const f = Math.floor(o / 300000);
    f = math.floor(timestamp_ms / 300000)
    
    # 对应 JS: const k = E0.sha256.hmac(SALT, "" + f);
    # 注意：JS 库生成的 k 是 hex 字符串，在下一步作为 key 使用时，使用的是其 utf-8 字节
    k_hmac = hmac.new(SALT_KEY.encode('utf-8'), str(f).encode('utf-8'), hashlib.sha256)
    k = k_hmac.hexdigest()

    # --- 步骤 2: 构造待签名字符串 g ---
    # 对应 JS: sortedPayload (c)
    # 关键修正：JS 中的 tc 函数只使用了变量 o 进行排序，o 只包含以下三个 key
    signature_keys = ['timestamp', 'requestId', 'user_id']
    
    # 提取这三个参数并排序
    sorted_pairs = []
    for key in sorted(signature_keys):
        if key in params:
            sorted_pairs.append(f"{key},{params[key]}")
    
    # JS: [["k","v"],...].join(",") -> "k,v,k,v"
    sorted_payload = ",".join(sorted_pairs)

    # 对应 JS: const p = btoa(S); (Prompt Base64)
    p = base64.b64encode(user_input.encode('utf-8')).decode('utf-8')

    # 对应 JS: const r = x; (Timestamp)
    r = timestamp_str
    
    # 对应 JS: const g = c + "|" + p + "|" + r;
    g = f"{sorted_payload}|{p}|{r}"

    # --- 步骤 3: 计算最终签名 ---
    # 对应 JS: const _ = E0.sha256.hmac(k, g).hexdigest();
    final_hmac = hmac.new(k.encode('utf-8'), g.encode('utf-8'), hashlib.sha256)
    signature = final_hmac.hexdigest()

    # 调试输出
    print("\n--- Signature Debug Info ---")
    print(f"Timestamp (r): {r}")
    print(f"Factor (f):    {f}")
    print(f"Dynamic Key (k): {k}")
    print(f"Payload (c):   {sorted_payload}")
    print(f"Prompt (p):    {p}")
    print(f"Sign String (g): {g}")
    print(f"Signature:     {signature}")
    print("----------------------------\n")

    return signature, timestamp_str


async def main():
    timestamp_ms = int(time.time() * 1000)
    request_id = str(uuid.uuid4())
    # 注意：这里的 user_input 必须与 messages 里的 content 以及 signature_prompt 字段完全一致
    user_input_prompt = "你好" 

    # 构造 URL 参数
    params = {
        'timestamp': str(timestamp_ms),
        'requestId': request_id,
        'user_id': USER_ID,
        'version': '0.0.1',
        'platform': 'web',
        'token': AUTH_TOKEN,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'language': 'zh-CN',
        'languages': 'zh-CN,zh,en',
        'timezone': 'Asia/Shanghai',
        'cookie_enabled': 'true',
        'screen_width': '1920',
        'screen_height': '1080',
        'screen_resolution': '1920x1080',
        'viewport_height': '900',
        'viewport_width': '1440',
        'viewport_size': '1440x900',
        'color_depth': '24',
        'pixel_ratio': '1',
        'current_url': 'https://chat.z.ai/',
        'pathname': '/',
        'search': '',
        'hash': '',
        'host': 'chat.z.ai',
        'hostname': 'chat.z.ai',
        'protocol': 'https:',
        'referrer': '',
        'title': 'Z.ai Chat',
        'timezone_offset': '-480',
        'local_time': '2025-12-08T12:00:00.000Z',
        'utc_time': 'Mon, 08 Dec 2025 12:00:00 GMT',
        'is_mobile': 'false',
        'is_touch': 'false',
        'max_touch_points': '0',
        'browser_name': 'Chrome',
        'os_name': 'Windows',
        # 注意：前端发包时还会带上 signature_timestamp，虽然不参与 sorted_payload 计算，但在 params 里
        'signature_timestamp': str(timestamp_ms),
    }
    
    # 生成签名
    signature, _ = generate_signature(
        user_input=user_input_prompt,
        params=params
    )

    headers = {
        'User-Agent': params['user_agent'],
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {AUTH_TOKEN}',
        'X-FE-Version': 'prod-fe-1.0.149', # 需随网站更新
        'X-Signature': signature,
        'Origin': 'https://chat.z.ai',
        'Referer': 'https://chat.z.ai/',
    }

    # 构造请求体
    json_data = {
        "stream": True,
        "model": "GLM-4-6-API-V1",
        "messages": [{"role": "user", "content": user_input_prompt}],
        "signature_prompt": user_input_prompt,
        "params": {},
        "features": {
            "image_generation": False,
            "web_search": False,
            "auto_web_search": False,
            "preview_mode": True,
            "flags": [],
            "enable_thinking": True
        },
        "variables": {
            "{{USER_NAME}}": "user",
            "{{USER_LOCATION}}": "Unknown",
            "{{CURRENT_DATETIME}}": "2025-12-08 20:00:00",
            "{{CURRENT_DATE}}": "2025-12-08",
            "{{CURRENT_TIME}}": "20:00:00",
            "{{CURRENT_WEEKDAY}}": "Monday",
            "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
            "{{USER_LANGUAGE}}": "zh-CN"
        },
        #"chat_id": str(uuid.uuid4()),
        #"id": str(uuid.uuid4()),
        #"current_user_message_id": str(uuid.uuid4()),
        #"current_user_message_parent_id": None,
        "background_tasks": {"title_generation": True, "tags_generation": True}
    }
    
    # 发送请求
    # 注意：params 需要拼接到 URL 中
    request_url = f"{BASE_URL}?{urlencode(params)}"

    async with httpx.AsyncClient() as client:
        try:
            print(f"Sending POST request to {BASE_URL}...")
            response = await client.post(
                request_url,
                headers=headers,
                json=json_data,
                timeout=30
            )

            print(f"Response Status: {response.status_code}")
            
            if response.status_code != 200:
                print(response.text)
                return

            print("--- Streaming Response ---")
            async for line in response.aiter_lines():
                if line:
                    print(line)
            print("\n--------------------------")

        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())