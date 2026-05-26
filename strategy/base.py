"""
策略基类 - 定义所有量化策略的统一接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import pandas as pd
from loguru import logger


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    EXIT = "EXIT"


@dataclass
class StrategySignal:
    """策略信号"""
    symbol: str
    strategy_name: str
    signal_type: SignalType
    signal_strength: float = 0.0      # 0.0 - 1.0
    price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    indicators_snapshot: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class StrategyPerformance:
    """策略绩效"""
    strategy_name: str
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_holding_period: float = 0.0  # 天


class BaseStrategy(ABC):
    """
    策略基类 - 所有策略必须继承此类
    """

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}
        self.enabled = True
        self.last_signal: Optional[StrategySignal] = None
        self.performance = StrategyPerformance(strategy_name=name)
        self.trade_history: list[dict] = []

    @abstractmethod
    def generate_signal(self, data: pd.DataFrame, **kwargs) -> StrategySignal:
        """
        生成交易信号
        Args:
            data: 包含OHLCV和技术指标的DataFrame
            **kwargs: 额外参数（新闻情绪、基本面等）
        Returns:
            StrategySignal 信号对象
        """
        pass

    @abstractmethod
    def calculate_position_size(self, account_value: float, price: float,
                                 risk_pct: float = 0.02, atr: float = None) -> float:
        """
        计算仓位大小
        Args:
            account_value: 账户总价值
            price: 当前价格
            risk_pct: 单笔风险百分比
            atr: ATR值（用于波动率调仓）
        Returns:
            建议持仓数量（股数）
        """
        pass

    def validate_signal(self, signal: StrategySignal) -> bool:
        """验证信号有效性"""
        if not self.enabled:
            logger.debug(f"策略 {self.name} 已禁用，跳过信号")
            return False

        if signal.signal_type == SignalType.HOLD:
            return False  # HOLD信号不执行

        if signal.signal_strength < 0.3:
            logger.debug(f"信号强度不足: {signal.signal_strength:.2f} < 0.3")
            return False

        if signal.price <= 0:
            logger.warning(f"无效价格: {signal.price}")
            return False

        return True

    def update_performance(self, trade_result: dict):
        """更新策略绩效"""
        self.trade_history.append(trade_result)
        self._recalculate_performance()

    def _recalculate_performance(self):
        """重新计算绩效指标"""
        if not self.trade_history:
            return

        trades = self.trade_history
        returns = [t.get("return_pct", 0) for t in trades]

        self.performance.total_trades = len(trades)
        self.performance.winning_trades = sum(1 for r in returns if r > 0)
        self.performance.losing_trades = sum(1 for r in returns if r < 0)
        self.performance.win_rate = self.performance.winning_trades / max(self.performance.total_trades, 1)
        self.performance.total_return = sum(returns)

        wins = [r for r in returns if r > 0]
        losses = [abs(r) for r in returns if r < 0]
        self.performance.avg_win = sum(wins) / max(len(wins), 1)
        self.performance.avg_loss = sum(losses) / max(len(losses), 1)
        self.performance.profit_factor = (
            sum(wins) / max(sum(losses), 0.001)
        )

        # 简单Sharpe计算
        if len(returns) > 1:
            import numpy as np
            returns_arr = np.array(returns)
            if returns_arr.std() > 0:
                self.performance.sharpe_ratio = (returns_arr.mean() / returns_arr.std()) * (252 ** 0.5)

    def get_status(self) -> dict:
        """获取策略状态"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "params": self.params,
            "performance": {
                "total_return": self.performance.total_return,
                "sharpe_ratio": self.performance.sharpe_ratio,
                "max_drawdown": self.performance.max_drawdown,
                "win_rate": self.performance.win_rate,
                "profit_factor": self.performance.profit_factor,
                "total_trades": self.performance.total_trades,
            },
            "last_signal": {
                "type": self.last_signal.signal_type.value if self.last_signal else None,
                "strength": self.last_signal.signal_strength if self.last_signal else None,
                "timestamp": self.last_signal.timestamp.isoformat() if self.last_signal else None,
            }
        }

    def update_params(self, new_params: dict):
        """更新策略参数（自反思调参用）"""
        old_params = self.params.copy()
        self.params.update(new_params)
        logger.info(f"策略 {self.name} 参数更新: {old_params} -> {self.params}")
