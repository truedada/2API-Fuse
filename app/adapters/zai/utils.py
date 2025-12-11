# app/adapters/zai/utils.py
import datetime
import json
import re
import uuid
from typing import Dict, List, Any

def get_time_variables() -> Dict[str, str]:
    """生成 Payload 中需要的各种时间变量"""
    now = datetime.datetime.now()
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "current_datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "current_date": now.strftime("%Y-%m-%d"),
        "current_time": now.strftime("%H:%M:%S"),
        "current_weekday": now.strftime("%A"),
        "local_time": now.isoformat() + "Z",  # 模拟 ISO 格式
        "utc_time": utc_now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    }

def sanitize_reasoning(text: str) -> str:
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

def tools_to_prompt(tools: List[Dict]) -> str:
    """将 Tools 转换为 Prompt，强制要求 JSON 格式"""
    if not tools:
        return ""
    
    prompt = "\n\n# Tools Definition\n"
    prompt += "You have access to the following tools. When you need to call a tool, please use the <glm_block> format strictly.\n\n"
    
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        name = func.get("name")
        description = func.get("description", "")
        parameters = json.dumps(func.get("parameters", {}), ensure_ascii=False)
        
        prompt += f"## Tool: {name}\n"
        prompt += f"Description: {description}\n"
        prompt += f"Parameters: {parameters}\n\n"
        
    return prompt

def get_last_user_message(messages: List[Dict]) -> str:
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
    return "你好"  # Fallback

def parse_xml_tool_calls(buffer: str) -> List[Dict[str, Any]]:
    """
    解析 <glm_block> 中的 XML 工具调用 (GLM-4.6 格式)
    格式: <invoke name="func_name"><parameter name="arg_name">arg_value</parameter></invoke>
    """
    parsed_tools = []
    
    # 提取所有的 invoke 块
    # 使用非贪婪匹配提取 invoke
    invoke_pattern = re.compile(r'<invoke\s+name=["\'](?P<name>[^"\']+)["\']\s*>(?P<content>.*?)</invoke>', re.DOTALL)
    
    for match in invoke_pattern.finditer(buffer):
        tool_name = match.group("name")
        content = match.group("content")
        
        # 提取参数
        args = {}
        param_pattern = re.compile(r'<parameter\s+name=["\'](?P<key>[^"\']+)["\']\s*>(?P<val>.*?)</parameter>', re.DOTALL)
        
        for param_match in param_pattern.finditer(content):
            key = param_match.group("key")
            val = param_match.group("val")
            
            # 尝试将参数值解析为 JSON (处理数组或对象的情况)
            # 如果解析失败，则保留原始字符串
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
                
            args[key] = val
        
        parsed_tools.append({
            "name": tool_name,
            "arguments": args 
        })
        
    return parsed_tools