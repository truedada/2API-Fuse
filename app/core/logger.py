import sys
import os
import logging
import inspect  # 引入 inspect 以获取更准确的堆栈信息
from loguru import logger
from pathlib import Path

# 定义日志文件夹路径
LOG_PATH = Path("logs")
if not LOG_PATH.exists():
    LOG_PATH.mkdir(parents=True, exist_ok=True)

class InterceptHandler(logging.Handler):
    """
    用于拦截标准 logging 日志并转发到 Loguru 的处理器
    (修复了 Python 3.11+ 下显示 logging:callHandlers 的问题)
    """
    def emit(self, record: logging.LogRecord) -> None:
        # 获取对应的 Loguru 日志级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # --- 核心修复：更健壮的栈帧查找逻辑 ---
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
    统一配置日志：
    """
    # 1. 拦截标准库日志 (Root Logger)
    # force=True 确保覆盖之前可能存在的配置
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    # 2. 显式开启 http 相关的日志 (关键步骤)
    # httpx 和 httpcore 默认非常安静，需要手动调低级别才能看到请求细节
    for log_name in ["httpx", "httpcore"]:
        log = logging.getLogger(log_name)
        log.handlers = [InterceptHandler()]
        log.propagate = False # 防止重复传播
        log.setLevel(logging.INFO) # 调试网络错误时，建议临时改为 logging.DEBUG

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

    # 4. 添加控制台输出
    logger.add(
        sys.stderr,
        level="INFO",
        format=console_log_format,
        colorize=True,
    )

    # 5. 添加全量日志文件
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
    
    logger.info("Loguru 配置完成")