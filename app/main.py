import contextlib
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.core.config import settings
from app.core.exceptions.handlers import register_exception_handlers
from app.core.redis.connection import init_redis, close_redis
# 引入封装好的数据库操作
from app.core.database import init_db, close_db
# 迁移
from app.core.migration import auto_migrate_on_startup # <--- 新增导入

from app.core.scheduler import SchedulerService
# 假设你有一个汇总的路由文件，或者直接导入 admin 路由
# 如果你没有 app/api/routers.py，请看代码下方的【补充说明】
from app.api.routers import api_router
from app.core.logger import setup_logging
setup_logging()
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理
    """
    # --- 1. Startup ---
    logger.info(f"正在以 {settings.ENVIRONMENT} 模式启动 {settings.PROJECT_NAME}...")

    # 初始化 Redis
    await init_redis()
    
    # 初始化 Database (调用封装好的逻辑，包含 pool_recycle 和 aerich 配置)
    await init_db()

    await auto_migrate_on_startup()
    # 2. 启动调度器
    scheduler_service = SchedulerService() # 初始化单例
    await scheduler_service.start()
    yield # 应用运行中...

    # --- 2. Shutdown ---
    logger.info("正在关闭应用...")
    
    # 关闭资源
    await close_redis()
    await close_db()
    
    await scheduler_service.stop()    
    logger.info("资源释放成功，应用关闭成功。")


def create_app() -> FastAPI:
    """应用工厂函数"""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=settings.DESCRIPTION,
        version=settings.VERSION,
        lifespan=lifespan
    )

    # 注册中间件 - CORS
    # 使用配置文件中的 BACKEND_CORS_ORIGINS
    if settings.BACKEND_CORS_ORIGINS:
        logger.info(f"允许的源: {settings.BACKEND_CORS_ORIGINS}")
        app.add_middleware(
            CORSMiddleware,
            allow_origins = [str(origin).rstrip("/") for origin in settings.BACKEND_CORS_ORIGINS],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 注册全局异常处理器
    register_exception_handlers(app)

    # 注册路由
    # 建议使用 include_router 包含 v1 下的所有路由
    app.include_router(api_router)
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    logger.info("静态文件目录已挂载到根路径 '/'。")
    return app

app = create_app()