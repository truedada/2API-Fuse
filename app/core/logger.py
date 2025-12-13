import sys
import os
import logging
import inspect
from loguru import logger
from pathlib import Path
from app.core.config import settings

# 定义日志文件夹路径
LOG_PATH = Path("logs")
if not LOG_PATH.exists():
    LOG_PATH.mkdir(parents=True, exist_ok=True)

class InterceptHandler(logging.Handler):
    """
    用于拦截标准 logging 日志并转发到 Loguru 的处理器
    """
    def emit(self, record: logging.LogRecord) -> None:
        # 获取对应的 Loguru 日志级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # --- [新增] 核心修改：日志降级逻辑 ---
        # 如果是 apscheduler 的 INFO 日志，强制将其视为 DEBUG
        # 这样它就不会出现在 INFO 级别的控制台输出中，但会保留在 DEBUG 级别的日志文件中
        if record.name.startswith("apscheduler") and level == "INFO":
            level = "DEBUG"

        # 查找日志调用的源头，跳过 logging 模块自身的栈帧
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )

def setup_logging():
    """
    统一配置日志
    """
    # 1. 拦截标准库日志
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    # --- [建议] 屏蔽其他常用第三方库的噪音 ---
    # 如果你不想看 http 请求的详细日志，也可以把 httpx 设为 WARNING
    # logging.getLogger("httpx").setLevel(logging.WARNING) 
    # logging.getLogger("httpcore").setLevel(logging.WARNING)

    # 3. 移除 loguru 默认的控制台输出
    logger.remove()

    # --- 格式定义 ---
    file_log_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{name}:{function}:{line} - "
        "{message}"
    )

    console_log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # 根据配置决定控制台日志级别
    console_level = "DEBUG" if settings.DEBUG else "INFO"

    # 4. 添加控制台输出
    logger.add(
        sys.stderr,
        level=console_level,
        format=console_log_format,
        colorize=True,
    )

    # 5. 添加全量日志文件 (包含被降级为 DEBUG 的 apscheduler 日志)
    logger.add(
        LOG_PATH / "all.log",
        level="DEBUG", 
        format=file_log_format,
        rotation="00:00",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # 6. 添加错误日志文件
    logger.add(
        LOG_PATH / "error.log",
        level="ERROR",
        format=file_log_format,
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )
    
    logger.info(f"Loguru 配置完成，控制台级别: {console_level}")