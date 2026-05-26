"""
全局配置模块 - 加载环境变量与系统参数
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Trading212Config:
    """Trading 212 平台配置"""
    API_KEY = os.getenv("T212_API_KEY", "")
    ENVIRONMENT = os.getenv("T212_ENVIRONMENT", "demo")  # demo | live
    BASE_URL_DEMO = "https://demo-api.trading212.com/api/v0"
    BASE_URL_LIVE = "https://live-api.trading212.com/api/v0"

    @classmethod
    def base_url(cls) -> str:
        return cls.BASE_URL_DEMO if cls.ENVIRONMENT == "demo" else cls.BASE_URL_LIVE

    @classmethod
    def headers(cls) -> dict:
        return {"Authorization": cls.API_KEY, "Content-Type": "application/json"}


class RiskConfig:
    """风控参数"""
    MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.10"))     # 10%
    MAX_PORTFOLIO_RISK_PCT = float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "0.02"))  # 2%
    DEFAULT_STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.05"))   # 5%
    DEFAULT_TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.10"))  # 10%
    NO_LEVERAGE = True  # 硬约束：不做杠杆
    MAX_DAILY_DRAWDOWN_PCT = 0.03  # 日最大回撤3%
    MAX_CORRELATED_POSITIONS = 3   # 同行业最大持仓数


class DataConfig:
    """数据配置"""
    DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "quant_system.db"))
    NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
    ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
    MARKET_DATA_INTERVAL = "5m"    # 盘中数据频率
    HISTORICAL_PERIOD = "1y"       # 历史数据长度


class SystemConfig:
    """系统配置"""
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.getenv("LOG_FILE", str(BASE_DIR / "logs" / "system.log"))
    SCHEDULER_INTERVAL_MINUTES = 5  # 调度器间隔
    REFLECTION_HOUR = 22            # 每日复盘时间(UTC)
    BACKTEST_INITIAL_CASH = 100000  # 回测初始资金


# 选股池配置 - 可通过 stocks.yaml 自定义
DEFAULT_WATCHLIST = {
    "tech": ["AAPL", "MSFT", "GOOGL", "NVDA", "AMZN", "META", "TSM", "AVGO"],
    "healthcare": ["JNJ", "UNH", "LLY", "PFE", "ABBV"],
    "finance": ["JPM", "BAC", "GS", "V", "MA"],
    "energy": ["XOM", "CVX", "COP", "SLB"],
    "consumer": ["WMT", "COST", "PG", "KO", "PEP"],
    "etf_index": ["SPY", "QQQ", "IWM", "DIA"],
    "etf_sector": ["XLK", "XLF", "XLE", "XLV", "XLI"],
}
