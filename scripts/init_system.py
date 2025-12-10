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
            "name": "Z2API",
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
            "base_url": "https://cloudcode-pa.googleapis.com/v1internal",
            "default_models": [
            "gemini-2.5-pro",
            "gemini-2.5-pro-maxthinking",
            "gemini-2.5-pro-nothinking",
            "gemini-2.5-flash",
            "gemini-2.5-flash-maxthinking",
            "gemini-2.5-flash-nothinking",
            "gemini-3-pro-preview",
            "gemini-3-pro-preview-maxthinking",
            "gemini-3-pro-preview-nothinking"
    ]
        }
    ]

    for p_data in platforms:
        # 使用 update_or_create 防止重复运行脚本报错
        await Platform.update_or_create(
            name=p_data["name"],
            defaults=p_data
        )
    logger.info(f"✅ 已预创建 {len(platforms)} 个平台")

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