"""
策略管理器 - 统一管理所有策略，执行策略路由与信号汇总
"""
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from config.settings import BASE_DIR
from strategy.base import BaseStrategy, StrategySignal, SignalType
from strategy.momentum_trend import MomentumTrendStrategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.event_driven import EventDrivenStrategy


class StrategyManager:
    """策略管理器"""

    STRATEGY_MAP = {
        "momentum_trend": MomentumTrendStrategy,
        "mean_reversion": MeanReversionStrategy,
        "event_driven": EventDrivenStrategy,
    }

    def __init__(self, config_path: str = None):
        self.config_path = config_path or str(BASE_DIR / "config" / "stocks.yaml")
        self.strategies: dict[str, BaseStrategy] = {}
        self.stock_configs: dict[str, dict] = {}  # symbol -> config
        self.strategy_defaults: dict = {}

        self._load_config()
        self._initialize_strategies()

    def _load_config(self):
        """加载选股配置"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            for stock in config.get("watchlist", []):
                symbol = stock["symbol"]
                self.stock_configs[symbol] = stock

            self.strategy_defaults = config.get("strategy_defaults", {})
            self.reflection_config = config.get("reflection", {})

            logger.info(f"配置加载完成: {len(self.stock_configs)} 只股票")

        except Exception as e:
            logger.error(f"配置加载失败: {e}")
            self.stock_configs = {}
            self.strategy_defaults = {}

    def _initialize_strategies(self):
        """初始化每只股票的策略实例"""
        for symbol, config in self.stock_configs.items():
            strategy_name = config.get("strategy", "momentum_trend")
            params = config.get("params", {})

            # 合并默认参数
            default_params = self.strategy_defaults.get(strategy_name, {})
            merged_params = {**default_params, **params}

            strategy_class = self.STRATEGY_MAP.get(strategy_name, MomentumTrendStrategy)
            strategy_key = f"{symbol}_{strategy_name}"
            self.strategies[strategy_key] = strategy_class(params=merged_params)

            logger.debug(f"策略初始化: {symbol} -> {strategy_name}")

        logger.info(f"策略初始化完成: {len(self.strategies)} 个策略实例")

    def generate_signal(self, symbol: str, data: pd.DataFrame,
                        sentiment: dict = None, earnings: dict = None,
                        macro_events: list = None) -> Optional[StrategySignal]:
        """为指定股票生成交易信号"""
        config = self.stock_configs.get(symbol)
        if not config:
            logger.warning(f"未配置股票: {symbol}")
            return None

        strategy_name = config.get("strategy", "momentum_trend")
        strategy_key = f"{symbol}_{strategy_name}"
        strategy = self.strategies.get(strategy_key)

        if not strategy or not strategy.enabled:
            return None

        signal = strategy.generate_signal(
            data,
            symbol=symbol,
            sentiment=sentiment or {},
            earnings=earnings or {},
            macro_events=macro_events or [],
        )

        # 验证信号
        if not strategy.validate_signal(signal):
            return None

        logger.info(
            f"[信号] {symbol} | {strategy_name} | {signal.signal_type.value} | "
            f"强度={signal.signal_strength:.2f} | 价格={signal.price:.2f}"
        )
        return signal

    def get_all_signals(self, market_data: dict[str, pd.DataFrame],
                        sentiments: dict[str, dict] = None,
                        macro_events: list = None) -> list[StrategySignal]:
        """
        生成所有股票的交易信号
        Args:
            market_data: {symbol: DataFrame}
            sentiments: {symbol: sentiment_dict}
            macro_events: 宏观事件列表
        """
        signals = []
        sentiments = sentiments or {}

        for symbol, config in self.stock_configs.items():
            data = market_data.get(symbol)
            if data is None or data.empty:
                continue

            sentiment = sentiments.get(symbol, {})
            signal = self.generate_signal(
                symbol, data, sentiment=sentiment, macro_events=macro_events
            )
            if signal:
                signals.append(signal)

        # 按信号强度排序
        signals.sort(key=lambda s: s.signal_strength, reverse=True)

        logger.info(f"生成信号: {len(signals)}/{len(self.stock_configs)} 只股票有交易信号")
        return signals

    def calculate_position(self, symbol: str, account_value: float,
                           price: float, atr: float = None) -> float:
        """计算仓位"""
        config = self.stock_configs.get(symbol)
        if not config:
            return 0

        strategy_name = config.get("strategy", "momentum_trend")
        strategy_key = f"{symbol}_{strategy_name}"
        strategy = self.strategies.get(strategy_key)

        if not strategy:
            return 0

        return strategy.calculate_position_size(account_value, price, atr=atr)

    def get_strategy_status(self) -> dict:
        """获取所有策略状态"""
        return {
            key: strategy.get_status()
            for key, strategy in self.strategies.items()
        }

    def update_strategy_params(self, symbol: str, strategy_name: str, new_params: dict):
        """更新策略参数（复盘调参用）"""
        strategy_key = f"{symbol}_{strategy_name}"
        strategy = self.strategies.get(strategy_key)
        if strategy:
            strategy.update_params(new_params)
            # 同步更新配置文件
            self._update_config_file(symbol, new_params)

    def disable_strategy(self, symbol: str, strategy_name: str, reason: str = ""):
        """禁用策略"""
        strategy_key = f"{symbol}_{strategy_name}"
        strategy = self.strategies.get(strategy_key)
        if strategy:
            strategy.enabled = False
            logger.warning(f"策略已禁用: {strategy_key}, 原因: {reason}")

    def enable_strategy(self, symbol: str, strategy_name: str):
        """启用策略"""
        strategy_key = f"{symbol}_{strategy_name}"
        strategy = self.strategies.get(strategy_key)
        if strategy:
            strategy.enabled = True
            logger.info(f"策略已启用: {strategy_key}")

    def _update_config_file(self, symbol: str, new_params: dict):
        """更新配置文件中的参数"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            for stock in config.get("watchlist", []):
                if stock.get("symbol") == symbol:
                    stock.get("params", {}).update(new_params)
                    break

            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        except Exception as e:
            logger.error(f"更新配置文件失败: {e}")
