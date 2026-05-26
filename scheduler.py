"""
任务调度器 - 定时执行数据采集、策略扫描、复盘
"""
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from config.settings import SystemConfig


class TradingScheduler:
    """交易调度器"""

    def __init__(self, trading_system):
        """trading_system: QuantTradingSystem 实例"""
        self.system = trading_system
        self.scheduler = BlockingScheduler()
        self._setup_jobs()

    def _setup_jobs(self):
        """配置定时任务"""

        # === 盘前准备 (美东时间 8:30 = UTC 13:30) ===
        self.scheduler.add_job(
            self.system.pre_market_routine,
            CronTrigger(day_of_week="mon-fri", hour=13, minute=30),
            id="pre_market",
            name="盘前准备",
        )

        # === 盘中策略扫描 (每5分钟) ===
        # 美东 9:30-16:00 = UTC 14:30-21:00
        self.scheduler.add_job(
            self.system.intraday_scan,
            CronTrigger(day_of_week="mon-fri", hour="14-20", minute="*/5"),
            id="intraday_scan",
            name="盘中策略扫描",
        )

        # === 盘后数据同步 (美东 16:30 = UTC 21:30) ===
        self.scheduler.add_job(
            self.system.post_market_routine,
            CronTrigger(day_of_week="mon-fri", hour=21, minute=30),
            id="post_market",
            name="盘后数据同步",
        )

        # === 每日复盘 (UTC 22:00) ===
        self.scheduler.add_job(
            self.system.daily_reflection,
            CronTrigger(day_of_week="mon-fri", hour=SystemConfig.REFLECTION_HOUR, minute=0),
            id="daily_reflection",
            name="每日复盘",
        )

        # === 新闻采集 (每30分钟) ===
        self.scheduler.add_job(
            self.system.collect_news,
            CronTrigger(minute="*/30"),
            id="news_collection",
            name="新闻采集",
        )

        # === 周度复盘 (周日 UTC 22:00) ===
        self.scheduler.add_job(
            self.system.weekly_reflection,
            CronTrigger(day_of_week="sun", hour=22, minute=0),
            id="weekly_reflection",
            name="周度复盘",
        )

        # === 持仓监控 (盘中每10分钟) ===
        self.scheduler.add_job(
            self.system.monitor_positions,
            CronTrigger(day_of_week="mon-fri", hour="14-20", minute="*/10"),
            id="position_monitor",
            name="持仓监控",
        )

        logger.info("✅ 调度器配置完成: 7个定时任务")

    def start(self):
        """启动调度器"""
        logger.info("🚀 量化交易系统调度器启动...")
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("调度器已停止")

    def stop(self):
        """停止调度器"""
        self.scheduler.shutdown()
        logger.info("调度器已关闭")

    def get_jobs(self) -> list[dict]:
        """获取所有任务状态"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else "N/A",
            })
        return jobs
