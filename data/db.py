"""
数据库管理模块 - SQLite + SQLAlchemy
存储：行情数据、新闻、交易记录、策略信号、复盘报告
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, String, Float, Integer, DateTime, Text, Boolean,
    Index, JSON, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from loguru import logger

from config.settings import DataConfig

Base = declarative_base()


# ============================================================
# 数据模型定义
# ============================================================

class OHLCV(Base):
    """K线行情数据"""
    __tablename__ = "ohlcv"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    interval = Column(String(10), nullable=False)  # 1m, 5m, 1h, 1d
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    adjusted_close = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", "interval", name="uq_ohlcv_symbol_ts_interval"),
        Index("ix_ohlcv_symbol_ts", "symbol", "timestamp"),
    )


class TechnicalIndicator(Base):
    """技术指标数据"""
    __tablename__ = "technical_indicators"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    indicator_name = Column(String(50), nullable=False)  # RSI, MACD, BB, ATR, ADX, etc.
    value = Column(Float)
    extra = Column(JSON)  # 额外参数，如MACD的signal/histogram
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", "indicator_name", name="uq_indicator"),
        Index("ix_indicator_symbol_ts", "symbol", "timestamp"),
    )


class NewsArticle(Base):
    """新闻文章"""
    __tablename__ = "news_articles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), index=True)  # 关联股票，可为空(宏观新闻)
    title = Column(String(500), nullable=False)
    source = Column(String(100))
    url = Column(String(1000))
    published_at = Column(DateTime)
    content = Column(Text)
    sentiment_score = Column(Float)       # -1.0 到 1.0
    sentiment_label = Column(String(20))  # positive, negative, neutral
    relevance_score = Column(Float)       # 与股票的相关度
    tags = Column(JSON)
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_news_symbol_pub", "symbol", "published_at"),
    )


class MacroEvent(Base):
    """宏观经济事件"""
    __tablename__ = "macro_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_name = Column(String(200), nullable=False)
    country = Column(String(10))  # US, CN, EU, etc.
    event_type = Column(String(50))  # CPI, GDP, FOMC, etc.
    scheduled_at = Column(DateTime)
    actual_value = Column(Float)
    forecast_value = Column(Float)
    previous_value = Column(Float)
    impact = Column(String(10))  # high, medium, low
    sentiment_impact = Column(Float)  # 对市场情绪的影响评分
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TradeRecord(Base):
    """交易记录"""
    __tablename__ = "trade_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(50), unique=True)  # T212 order ID
    symbol = Column(String(20), nullable=False, index=True)
    t212_ticker = Column(String(30))  # Trading212 instrument ticker
    side = Column(String(10), nullable=False)  # BUY, SELL
    order_type = Column(String(20))  # market, limit, stop, stop_limit
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    strategy_name = Column(String(50))
    signal_reason = Column(Text)  # 入场原因
    status = Column(String(20), default="pending")  # pending, filled, cancelled, rejected
    filled_at = Column(DateTime)
    commission = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_trade_symbol_created", "symbol", "created_at"),
        Index("ix_trade_strategy", "strategy_name"),
    )


class StrategySignal(Base):
    """策略信号记录"""
    __tablename__ = "strategy_signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy_name = Column(String(50), nullable=False)
    signal_type = Column(String(20), nullable=False)  # BUY, SELL, HOLD, EXIT
    signal_strength = Column(Float)  # 0.0 - 1.0
    price_at_signal = Column(Float)
    indicators_snapshot = Column(JSON)  # 触发信号时的指标快照
    executed = Column(Boolean, default=False)
    trade_id = Column(Integer)  # 关联 trade_records.id
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_signal_symbol_strategy", "symbol", "strategy_name"),
    )


class PositionSnapshot(Base):
    """持仓快照"""
    __tablename__ = "position_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    t212_ticker = Column(String(30))
    quantity = Column(Float, nullable=False)
    avg_entry_price = Column(Float)
    current_price = Column(Float)
    unrealized_pnl = Column(Float)
    unrealized_pnl_pct = Column(Float)
    market_value = Column(Float)
    weight_in_portfolio = Column(Float)
    snapshot_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_position_snapshot", "symbol", "snapshot_at"),
    )


class ReflectionReport(Base):
    """复盘报告"""
    __tablename__ = "reflection_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    report_type = Column(String(20), nullable=False)  # daily, weekly, monthly
    report_date = Column(DateTime, nullable=False)
    portfolio_return = Column(Float)
    portfolio_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    avg_win = Column(Float)
    avg_loss = Column(Float)
    strategy_evaluations = Column(JSON)  # 各策略评估
    action_items = Column(JSON)          # 改进措施
    parameter_changes = Column(JSON)     # 参数调整记录
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_reflection_type_date", "report_type", "report_date"),
    )


class CompanyFundamental(Base):
    """公司基本面数据"""
    __tablename__ = "company_fundamentals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    data_date = Column(DateTime, nullable=False)
    market_cap = Column(Float)
    pe_ratio = Column(Float)
    forward_pe = Column(Float)
    pb_ratio = Column(Float)
    ps_ratio = Column(Float)
    ev_ebitda = Column(Float)
    debt_to_equity = Column(Float)
    roe = Column(Float)
    roa = Column(Float)
    revenue_growth = Column(Float)
    earnings_growth = Column(Float)
    dividend_yield = Column(Float)
    free_cash_flow = Column(Float)
    analyst_rating = Column(JSON)  # {buy: N, hold: N, sell: N, target_price: N}
    earnings_date = Column(DateTime)
    sector = Column(String(50))
    industry = Column(String(100))
    extra_data = Column(JSON)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("symbol", "data_date", name="uq_fundamental_symbol_date"),
    )


# ============================================================
# 数据库管理器
# ============================================================

class DatabaseManager:
    """数据库管理器 - 单例模式"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = None):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self.db_path = db_path or DataConfig.DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False}
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._create_tables()
        logger.info(f"数据库初始化完成: {self.db_path}")

    def _create_tables(self):
        """创建所有表"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        """获取数据库会话"""
        return self.SessionLocal()

    def bulk_insert_ohlcv(self, records: list[dict]):
        """批量插入K线数据"""
        session = self.get_session()
        try:
            for r in records:
                existing = session.query(OHLCV).filter_by(
                    symbol=r["symbol"],
                    timestamp=r["timestamp"],
                    interval=r["interval"]
                ).first()
                if existing:
                    # 更新已有数据
                    for key, val in r.items():
                        if key not in ("id",):
                            setattr(existing, key, val)
                else:
                    session.add(OHLCV(**r))
            session.commit()
            logger.debug(f"批量写入K线数据: {len(records)} 条")
        except Exception as e:
            session.rollback()
            logger.error(f"批量写入K线失败: {e}")
            raise
        finally:
            session.close()

    def insert_trade(self, trade_data: dict) -> int:
        """插入交易记录"""
        session = self.get_session()
        try:
            trade = TradeRecord(**trade_data)
            session.add(trade)
            session.commit()
            logger.info(f"交易记录已保存: {trade.symbol} {trade.side} {trade.quantity}@{trade.price}")
            return trade.id
        except Exception as e:
            session.rollback()
            logger.error(f"保存交易记录失败: {e}")
            raise
        finally:
            session.close()

    def update_trade_status(self, trade_id: int, status: str, filled_at: datetime = None):
        """更新交易状态"""
        session = self.get_session()
        try:
            trade = session.query(TradeRecord).get(trade_id)
            if trade:
                trade.status = status
                trade.updated_at = datetime.now(timezone.utc)
                if filled_at:
                    trade.filled_at = filled_at
                session.commit()
        finally:
            session.close()

    def get_recent_ohlcv(self, symbol: str, interval: str = "1d", limit: int = 200) -> list[OHLCV]:
        """获取最近K线数据"""
        session = self.get_session()
        try:
            return session.query(OHLCV).filter_by(
                symbol=symbol, interval=interval
            ).order_by(OHLCV.timestamp.desc()).limit(limit).all()
        finally:
            session.close()

    def get_trades_by_date_range(self, start: datetime, end: datetime, symbol: str = None) -> list[TradeRecord]:
        """按日期范围查询交易"""
        session = self.get_session()
        try:
            q = session.query(TradeRecord).filter(
                TradeRecord.created_at >= start,
                TradeRecord.created_at <= end
            )
            if symbol:
                q = q.filter(TradeRecord.symbol == symbol)
            return q.all()
        finally:
            session.close()

    def save_reflection_report(self, report_data: dict) -> int:
        """保存复盘报告"""
        session = self.get_session()
        try:
            report = ReflectionReport(**report_data)
            session.add(report)
            session.commit()
            logger.info(f"复盘报告已保存: {report.report_type} {report.report_date}")
            return report.id
        except Exception as e:
            session.rollback()
            logger.error(f"保存复盘报告失败: {e}")
            raise
        finally:
            session.close()

    def get_latest_reflection(self, report_type: str = "daily") -> ReflectionReport:
        """获取最新复盘报告"""
        session = self.get_session()
        try:
            return session.query(ReflectionReport).filter_by(
                report_type=report_type
            ).order_by(ReflectionReport.report_date.desc()).first()
        finally:
            session.close()

    def insert_signal(self, signal_data: dict) -> int:
        """保存策略信号"""
        session = self.get_session()
        try:
            signal = StrategySignal(**signal_data)
            session.add(signal)
            session.commit()
            return signal.id
        except Exception as e:
            session.rollback()
            logger.error(f"保存信号失败: {e}")
            raise
        finally:
            session.close()

    def save_news(self, news_data: dict) -> int:
        """保存新闻"""
        session = self.get_session()
        try:
            article = NewsArticle(**news_data)
            session.add(article)
            session.commit()
            return article.id
        except Exception as e:
            session.rollback()
            logger.error(f"保存新闻失败: {e}")
            raise
        finally:
            session.close()

    def get_news_for_symbol(self, symbol: str, limit: int = 50) -> list[NewsArticle]:
        """获取股票相关新闻"""
        session = self.get_session()
        try:
            return session.query(NewsArticle).filter_by(
                symbol=symbol
            ).order_by(NewsArticle.published_at.desc()).limit(limit).all()
        finally:
            session.close()
