# app/adapters/zai/messages.py
import json
import uuid
from typing import List, Dict, Tuple
from loguru import logger

from app.adapters.zai.files import upload_image

async def process_messages_and_files(
    messages: List[Dict], 
    current_msg_id: str, 
    tools_prompt: str,
    headers: Dict[str, str]
) -> Tuple[List[Dict], List[Dict]]:
    """
    处理消息：
    1. 处理图片上传
    2. Role 转换与降级
    3. Tool Calls 转换
    4. System 消息合并
    5. Prefill 移除
    """
    clean_messages = []
    files_list = []
    
    system_prompt_buffer = [] 
    
    # 1. 预处理：分离 System 消息，处理 Prefill
    temp_messages = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        # --- 处理 System 消息 ---
        if role == "system":
            text_content = ""
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        text_content += part.get("text", "")
            else:
                text_content = str(content)
            
            if text_content:
                system_prompt_buffer.append(text_content)
            continue 
        
        temp_messages.append(msg)
    
    # --- 处理 Prefill (移除末尾的 Assistant) ---
    if temp_messages and temp_messages[-1]["role"] == "assistant":
        logger.warning(f"Z.ai 不支持 Prefill (Assistant 结尾)，已移除末尾消息: {str(temp_messages[-1].get('content'))[:20]}...")
        temp_messages.pop()
        
    if not temp_messages:
        temp_messages.append({"role": "user", "content": "Start."})
        
    # 2. 构建最终消息列表
    first_user_processed = False
    
    for msg in temp_messages:
        role = msg.get("role")
        content = msg.get("content")
        
        # --- 处理 Tool 角色 (降级为 User) ---
        if role == "tool":
            tool_content = str(content) if content is not None else ""
            try:
                if isinstance(content, (dict, list)):
                    tool_content = json.dumps(content, ensure_ascii=False)
            except:
                pass
            
            new_msg = {
                "role": "user",
                "content": f"[Tool Output]\n{tool_content}"
            }
            clean_messages.append(new_msg)
            continue
        
        # --- 处理 Assistant 角色 ---
        if role == "assistant":
            new_msg = msg.copy()
            if msg.get("tool_calls") and not content:
                tool_calls = msg["tool_calls"]
                call_descriptions = []
                for tc in tool_calls:
                    func_name = tc.get("function", {}).get("name", "unknown")
                    args = tc.get("function", {}).get("arguments", "{}")
                    call_descriptions.append(f"Calling tool `{func_name}` with args: {args}")
                new_msg["content"] = "\n".join(call_descriptions)
            
            new_msg.pop("tool_calls", None)
            new_msg.pop("function_call", None)
            
            if new_msg.get("content") is None:
                new_msg["content"] = " " 
            
            clean_messages.append(new_msg)
            continue
        
        # --- 处理 User 角色 (合并 System + 多模态) ---
        if role == "user":
            new_msg = msg.copy()
            text_content = ""
            
            # 如果是第一条 User 消息，注入 System Prompt 和 Tools Prompt
            if not first_user_processed:
                full_system_text = "\n\n".join(system_prompt_buffer)
                
                if tools_prompt:
                    full_system_text += f"\n{tools_prompt}"
                
                if full_system_text:
                    text_content += f"<system_instructions>\n{full_system_text}\n</system_instructions>\n\n"
                first_user_processed = True
            
            # 处理 User 内容
            if isinstance(content, list):
                for part in content:
                    msg_type = part.get("type")
                    if msg_type == "text":
                        text_content += part.get("text", "")
                    elif msg_type == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            # 调用上传逻辑
                            file_data = await upload_image(url, headers)
                            file_id = file_data.get("id")
                            file_item = {
                                "type": "image",
                                "file": file_data,
                                "id": file_id,
                                "url": f"/api/v1/files/{file_id}/content",
                                "name": file_data.get("filename", "image.jpg"),
                                "status": "uploaded",
                                "size": file_data.get("size", 0),
                                "itemId": str(uuid.uuid4()),
                                "media": "image",
                                "ref_user_msg_id": current_msg_id
                            }
                            files_list.append(file_item)
                        elif url.startswith("http"):
                             text_content += f" [Image: {url}] "
            else:
                text_content += str(content)
            new_msg["content"] = text_content
            clean_messages.append(new_msg)
            continue
        
    return clean_messages, files_list