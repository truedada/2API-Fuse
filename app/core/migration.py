# app/core/migration.py
import sys
import subprocess
from pathlib import Path
from loguru import logger
from app.core.config import settings

class MigrationManager:
    """
    数据库迁移管理器 (基于 Aerich)
    """
    def __init__(self):
        self.migrations_dir = Path("migrations")
        self.pyproject_path = Path("pyproject.toml")
        # 指向 database.py 中定义的 TORTOISE_ORM 变量
        self.tortoise_config_module = "app.core.database.TORTOISE_ORM"

    async def is_aerich_initialized(self) -> bool:
        """检查 Aerich 是否已初始化"""
        if not self.pyproject_path.exists():
            return False
        if not self.migrations_dir.exists():
            return False
        # 检查是否存在 models 迁移目录 (Aerich 默认 app 名为 models 或配置中指定的)
        # 这里假设我们的 app 配置名为 'models' (参考 database.py)
        if not (self.migrations_dir / "models").exists():
            return False
        return True

    def _run_command(self, cmd: list[str]) -> bool:
        """运行 Shell 命令"""
        try:
            logger.debug(f"正在执行命令: {' '.join(cmd)}")
            # 注意: 这里使用 sys.executable 确保使用当前虚拟环境的 python
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=".", timeout=60
            )
            if result.returncode != 0:
                logger.error(f"命令执行失败： {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            return False

    async def init_aerich(self) -> bool:
        """初始化 Aerich (生成配置文件和初始迁移)"""
        if settings.ENVIRONMENT == "test":
            logger.info("Test environment, skipping Aerich init.")
            return True

        logger.info("初始化 Aerich...")
        
        # 1. aerich init -t ...
        cmd_init = [
            sys.executable, "-m", "aerich", "init",
            "-t", self.tortoise_config_module,
            "--location", str(self.migrations_dir)
        ]
        if not self._run_command(cmd_init):
            return False

        # 2. aerich init-db
        logger.info("Generating initial migration (init-db)...")
        cmd_init_db = [sys.executable, "-m", "aerich", "init-db"]
        if not self._run_command(cmd_init_db):
            return False
            
        logger.success("Aerich 初始化成功。")
        return True

    async def upgrade(self) -> bool:
        """应用迁移 (aerich upgrade)"""
        logger.info("正在应用数据库迁移...")
        cmd = [sys.executable, "-m", "aerich", "upgrade"]
        if self._run_command(cmd):
            logger.success("数据库迁移应用成功。")
            return True
        return False

# 单例
migration_manager = MigrationManager()

async def auto_migrate_on_startup():
    """启动时自动检查并应用迁移"""
    # 仅在非生产环境或显式开启自动迁移时运行
    # 这里简单逻辑：只要配置好就尝试 upgrade，保证 DB 是最新的
    try:
        if not await migration_manager.is_aerich_initialized():
            logger.warning("Aerich 尚未初始化，已跳过自动迁移。请运行 'python scripts/init_system.py' 初始化。")
            return

        await migration_manager.upgrade()
    except Exception as e:
        logger.error(f"数据库自动迁移执行失败: {e}")