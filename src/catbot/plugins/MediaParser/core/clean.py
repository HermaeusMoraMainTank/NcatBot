import asyncio
import shutil
import zoneinfo
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .compat import _log, ConfigWrapper as AstrBotConfig


class CacheCleaner:
    """
    每天固定时间自动清理插件缓存目录的调度器封装。
    """

    JOBNAME = "CacheCleaner"

    def __init__(self, context: Optional[Any], config: AstrBotConfig):
        self.clean_cron = config["clean_cron"]
        self.cache_dir = Path(config["cache_dir"])

        # 尝试从context获取时区，如果失败使用默认时区
        tz = None
        if context is not None:
            try:
                tz = context.get_config().get("timezone")
            except Exception:
                pass

        self.timezone = (
            zoneinfo.ZoneInfo(tz) if tz else zoneinfo.ZoneInfo("Asia/Shanghai")
        )
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.scheduler.start()

        self.register_task()

        _log.info(f"{self.JOBNAME} 已启动，任务周期：{self.clean_cron}")

    def register_task(self):
        try:
            self.trigger = CronTrigger.from_crontab(self.clean_cron)
            self.scheduler.add_job(
                func=self._clean_plugin_cache,
                trigger=self.trigger,
                name=f"{self.JOBNAME}_scheduler",
                max_instances=1,
            )
        except Exception as e:
            _log.error(f"[{self.JOBNAME}] Cron 格式错误：{e}")

    async def _clean_plugin_cache(self) -> None:
        """删除并重建缓存目录"""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, shutil.rmtree, self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            _log.info("Cache directory cleaned and recreated.")
        except Exception:
            _log.exception("Error while cleaning cache directory.")

    async def stop(self):
        self.scheduler.remove_all_jobs()
        _log.info(f"[{self.JOBNAME}] 已停止")
