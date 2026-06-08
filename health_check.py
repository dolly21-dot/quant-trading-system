"""
系统健康检查 - 启动前自检
检查项: 配置完整性、数据源可用性、API连通性、数据库状态、依赖版本
"""
import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from datetime import datetime, timezone
from dataclasses import dataclass

from loguru import logger


@dataclass
class HealthCheckItem:
    name: str
    status: str  # OK / WARN / FAIL
    message: str


class HealthChecker:
    """系统健康检查器"""

    def __init__(self):
        self.results: list[HealthCheckItem] = []

    def run_all(self) -> bool:
        """运行全部检查，返回True=系统健康"""
        logger.info("🏥 系统健康检查开始...")

        self._check_config()
        self._check_dependencies()
        self._check_database()
        self._check_data_sources()
        self._check_trading212_api()
        self._check_notification()

        # 汇总
        ok_count = sum(1 for r in self.results if r.status == "OK")
        warn_count = sum(1 for r in self.results if r.status == "WARN")
        fail_count = sum(1 for r in self.results if r.status == "FAIL")

        logger.info(f"🏥 检查完成: ✅ {ok_count} | ⚠️ {warn_count} | ❌ {fail_count}")

        for r in self.results:
            icon = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(r.status, "❓")
            logger.info(f"  {icon} {r.name}: {r.message}")

        return fail_count == 0

    def _add(self, name: str, status: str, message: str):
        self.results.append(HealthCheckItem(name, status, message))

    def _check_config(self):
        """检查配置完整性"""
        try:
            from config.settings import (
                Trading212Config, RiskConfig, DataConfig,
                SystemConfig, NotificationConfig
            )

            if not Trading212Config.API_KEY:
                self._add("T212 API Key", "WARN", "未配置 (Demo模式可用默认值)")
            else:
                self._add("T212 API Key", "OK", f"已配置 (env={Trading212Config.ENVIRONMENT})")

            if not RiskConfig.NO_LEVERAGE:
                self._add("杠杆约束", "FAIL", "NO_LEVERAGE应为True!")
            else:
                self._add("杠杆约束", "OK", "已禁用杠杆 ✅")

            self._add("风控参数", "OK",
                       f"单股≤{RiskConfig.MAX_POSITION_PCT:.0%} "
                       f"单笔风险≤{RiskConfig.MAX_PORTFOLIO_RISK_PCT:.0%} "
                       f"日回撤≤{RiskConfig.MAX_DAILY_DRAWDOWN_PCT:.0%}")

        except Exception as e:
            self._add("配置加载", "FAIL", str(e))

    def _check_dependencies(self):
        """检查依赖"""
        required = {
            "pandas": "pandas",
            "numpy": "numpy",
            "sqlalchemy": "SQLAlchemy",
            "yfinance": "yfinance",
            "httpx": "httpx",
            "loguru": "loguru",
            "ta": "ta",
            "vaderSentiment": "vaderSentiment",
            "feedparser": "feedparser",
            "apscheduler": "APScheduler",
            "yaml": "pyyaml",
            "dotenv": "python-dotenv",
        }

        missing = []
        for module, package in required.items():
            try:
                __import__(module)
            except ImportError:
                missing.append(package)

        if missing:
            self._add("Python依赖", "FAIL", f"缺少: {', '.join(missing)}")
        else:
            self._add("Python依赖", "OK", "全部已安装")

    def _check_database(self):
        """检查数据库"""
        try:
            from data.db import DatabaseManager
            db = DatabaseManager()
            from sqlalchemy import text
            with db.engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_count = len(tables)

            if table_count >= 7:
                self._add("数据库", "OK", f"{table_count} 张表已创建")
            else:
                self._add("数据库", "WARN", f"仅 {table_count} 张表，可能需要重建")

        except Exception as e:
            self._add("数据库", "FAIL", str(e))

    def _check_data_sources(self):
        """检查数据源"""
        # yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker("SPY")
            hist = ticker.history(period="5d")
            if not hist.empty:
                self._add("yfinance数据源", "OK", f"SPY 5日数据: {len(hist)}条")
            else:
                self._add("yfinance数据源", "WARN", "返回空数据(可能限频)")
        except Exception as e:
            self._add("yfinance数据源", "WARN", f"获取失败: {e}")

        # Twelve Data
        try:
            from config.settings import DataConfig
            if DataConfig.TWELVE_DATA_KEY:
                self._add("Twelve Data", "OK", "API Key已配置")
            else:
                self._add("Twelve Data", "WARN", "API Key未配置(备用源不可用)")
        except Exception:
            self._add("Twelve Data", "WARN", "配置未就绪")

    def _check_trading212_api(self):
        """检查Trading 212 API"""
        try:
            from config.settings import Trading212Config
            if not Trading212Config.API_KEY:
                self._add("T212 API连通", "WARN", "无API Key，无法验证")
                return

            from execution.trading212 import Trading212Client
            client = Trading212Client()
            summary = client.get_account_summary()
            if summary:
                total = summary.get("total", "N/A")
                self._add("T212 API连通", "OK", f"账户总额: {total}")
            else:
                self._add("T212 API连通", "WARN", "API返回空(检查Key)")
            client.close()

        except Exception as e:
            self._add("T212 API连通", "WARN", f"连接失败: {e}")

    def _check_notification(self):
        """检查通知配置"""
        try:
            from config.settings import NotificationConfig
            channels = []
            if NotificationConfig.WEBHOOK_URL:
                channels.append(f"Webhook({NotificationConfig.WEBHOOK_TYPE})")
            if NotificationConfig.EMAIL_SENDER:
                channels.append("Email")
            if not channels:
                channels.append("仅控制台+文件")

            self._add("通知通道", "OK" if channels else "WARN",
                       f"已配置: {', '.join(channels)}")
        except Exception:
            self._add("通知通道", "WARN", "未配置")


if __name__ == "__main__":
    checker = HealthChecker()
    healthy = checker.run_all()
    sys.exit(0 if healthy else 1)
