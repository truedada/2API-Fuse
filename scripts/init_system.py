# scripts/init_system.py
import asyncio
import sys
from pathlib import Path

# 将项目根目录加入 python path，确保能导入 app
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from app.core.database import init_db, close_db
from app.core.migration import migration_manager
from app.models.platform import Platform
from app.models.apikey import ApiKey
from app.core.config import settings

async def seed_data():
    """预置初始数据"""
    logger.info("🌱 创建初始数据...")
    
    # 1. 预置常用平台
    platforms = [
        {
            "name": "ZAI",
            "adapter_type": "zai",
            "base_url": "https://chat.z.ai/api",
            "model_map": {
                "GLM-4.6" : "GLM-4-6-API-V1",
                "GLM-4.6V" : "glm-4.6v",
                "GLM-4.5" : "0727-360B-API",
                "GLM-4.5-Air" : "0727-106B-API"
            },
            "default_models" : ["GLM-4.6" ,"GLM-4.6V" ,"GLM-4.5","GLM-4.5-Air"]
        },
        {
            "name": "GeminiCli",
            "adapter_type": "geminicli",
            "base_url": "https://cloudcode-pa.googleapis.com",
            "default_models": [
                "gemini-2.5-pro",
                "gemini-2.5-pro-maxthinking",
                "gemini-2.5-flash",
                "gemini-2.5-flash-maxthinking",
                "gemini-3-pro-preview",
                "gemini-3-pro-preview-maxthinking",
            ]
        },
        {
            "name": "Antigravity",
            "adapter_type": "antigravity",
            "base_url": "https://cloudcode-pa.googleapis.com", 
            # 关键：在这里配置模型映射
            # 格式： "用户可见模型名(User Model)": "传递给Adapter的模型名(Internal Model)"
            "model_map": {
                # --- Gemini 3.0 系列 ---
                # 将 preview 映射到 high (Antigravity 内部ID)，并支持 maxthinking
                "gemini-3-pro-preview": "gemini-3-pro-high",
                "gemini-3-pro-preview-maxthinking": "gemini-3-pro-high-maxthinking",
                
                # Image 模型映射 (生图模型)
                "gemini-3-pro-image-preview": "gemini-3-pro-image",
                
                # --- Gemini 2.5 系列 ---
                # Computer use (计算机操作) 专用模型映射
                "gemini-2.5-computer-use-preview": "rev19-uic3-1p",
                
                # --- Claude 系列 (重要) ---
                # 必须显式映射 Thinking 版本，否则可能会被网关拦截或识别错误
                "claude-sonnet-4-5": "claude-sonnet-4-5", 
                "claude-sonnet-4-5-thinking": "claude-sonnet-4-5-thinking", 
                "claude-opus-4-5-thinking": "claude-opus-4-5-thinking",
                
                # --- GPT 系列 ---
                # 实事求是，OSS 模型直接显示为 OSS，不搞虚假映射
                "gpt-oss-120b": "gpt-oss-120b-medium"
            },
            "default_models": [
                # --- Gemini 2.5 系列 (直接使用 Adapter 支持的 ID) ---
                "gemini-2.5-pro",
                "gemini-2.5-pro-maxthinking",
                "gemini-2.5-flash",
                "gemini-2.5-flash-maxthinking",
                
                # --- Gemini 3 系列 (使用上面的 model_map) ---
                "gemini-3-pro-preview", 
                "gemini-3-pro-preview-maxthinking",
                "gemini-3-pro-low",
                "gemini-3-pro-image-preview",
                
                # --- Claude 系列 ---
                "claude-sonnet-4-5",
                "claude-sonnet-4-5-thinking",
                "claude-opus-4-5-thinking",
                
                # --- GPT 系列 ---
                "gpt-oss-120b"
            ]
        }
    ]

    for p_data in platforms:
        # 使用 update_or_create 防止重复运行脚本报错
        # 注意：这里会更新已存在平台的 default_models 和 model_map
        await Platform.update_or_create(
            name=p_data["name"],
            defaults=p_data
        )
    logger.info(f"✅ 已预创建/更新 {len(platforms)} 个平台")

    # 2. 创建一个管理员测试 Key
    admin_name = "Super Admin Key"
    
    # 先查是否存在，不使用固定Key
    existing_key = await ApiKey.get_or_none(name=admin_name)
    
    if existing_key:
        logger.info(f"ℹ️ 管理员 Key 已存在 (跳过生成): {existing_key.key} [余额: {existing_key.balance}]")
    else:
        # 不传递 key 字段，让 Model 自动调用 default=generate_sk 生成
        new_key = await ApiKey.create(
            name=admin_name,
            balance=-1, # 无限
            is_active=True
        )
        logger.info(f"✅ 已生成并创建管理员 Key: {new_key.key}")

async def main():
    logger.info("🚀 开始系统初始化...")
    
    # 1. 初始化数据库连接
    await init_db()
    
    try:
        # 2. Aerich 初始化与迁移
        if await migration_manager.is_aerich_initialized():
            logger.info("Aerich 已初始化。")
        else:
            logger.info("正在初始化 Aerich 结构...")
            success = await migration_manager.init_aerich()
            if not success:
                logger.error("❌ Aerich 初始化失败。终止。")
                return

        # 3. 确保数据库是最新的
        await migration_manager.upgrade()
        
        # 4. 写入预置数据
        await seed_data()
        
        logger.success("🎉 系统初始化完成！现在运行 'python run.py' 即可启动程序。")
        
    except Exception as e:
        logger.critical(f"❌ 初始化失败: {e}")
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())