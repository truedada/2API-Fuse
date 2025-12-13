import os
import sys
import uvicorn
from app.core.config import settings
from loguru import logger

def main():
    try:
        host = settings.HOST
        port = settings.PORT
        reload_flag = bool(settings.ENVIRONMENT == "dev")
        # 配置 uvicorn 启动参数
        uvicorn_config = {
            "app": "app.main:app",
            "host": host,
            "port": port,
            "reload": reload_flag,
            "workers": 1,
            "access_log": False,
            "log_config": None,  # 禁用 uvicorn 默认日志配置
        }
        if reload_flag:
            uvicorn_config.update({
                "reload_excludes": [
                    "logs/*",           # 排除所有日志文件
                    "*.log",            # 排除所有.log文件
                    "__pycache__/*",    # 排除Python缓存
                    "*.pyc",            # 排除编译的Python文件
                    ".git/*",           # 排除git目录
                    "node_modules/*",   # 排除node模块(如果有)
                    ".venv/*",          # 排除虚拟环境
                    "venv/*",           # 排除虚拟环境
                    ".env*",            # 排除环境变量文件
                    "*.db",             # 排除数据库文件
                    "*.sqlite*",        # 排除SQLite数据库
                ],
                "reload_dirs": ["app"],  # 只监控app目录下的变化
            })
        # 启动服务器
        logger.info(f"正在启动服务器 {host}:{port}...")
        uvicorn.run(**uvicorn_config)
        
    except Exception as e:
        logger.error(f"服务器启动失败: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
# pipdeptree --warn silence | Select-String -Pattern '^\w+' | Out-File -FilePath .\requirements.txt -Encoding utf8