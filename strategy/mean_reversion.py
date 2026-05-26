"""
均值回归策略 - 价格偏离均值后回归
入场: 布林带触轨 + RSI超买超卖 + Z-Score极端
出场: 价格回归中轨 或 最大持仓时间
"""
import numpy as np
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, StrategySignal, SignalType


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略"""

    def __init__(self, params: dict = None):
        default_params = {
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_overbought": 75,
            "rsi_oversold": 25,
            "z_score_threshold": 2.0,
            "lookback": 30,
            "holding_period_max": 10,  # 最大持仓天数
            "atr_stop_multiplier": 1.5,
            "mean_reversion_speed": 0.5,
        }
        if params:
            default_params.update(params)
        super().__init__(name="mean_reversion", params=default_params)

    def generate_signal(self, data: pd.DataFrame, **kwargs) -> StrategySignal:
        """生成均值回归信号"""
        symbol = kwargs.get("symbol", "UNKNOWN")
        sentiment = kwargs.get("sentiment", {})

        if data.empty or len(data) < self.params["bb_period"] + 10:
            return StrategySignal(
                symbol=symbol, strategy_name=self.name,
                signal_type=SignalType.HOLD, signal_strength=0.0,
                reason="数据不足"
            )

        df = data.copy()
        df = self._ensure_indicators(df)
        latest = df.iloc[-1]

        # === 信号判断 ===
        buy_signals = 0
        sell_signals = 0
        buy_strength = 0.0
        sell_strength = 0.0

        # 1. 布林带
        bb_pct = latest.get("bb_pct")
        if bb_pct is not None:
            if bb_pct < 0.0:  # 价格低于下轨
                buy_signals += 1
                buy_strength += min(abs(bb_pct) * 2, 1.0)
            elif bb_pct < 0.2:  # 接近下轨
                buy_signals += 0.5
                buy_strength += 0.3
            elif bb_pct > 1.0:  # 价格高于上轨
                sell_signals += 1
                sell_strength += min((bb_pct - 1.0) * 2, 1.0)
            elif bb_pct > 0.8:  # 接近上轨
                sell_signals += 0.5
                sell_strength += 0.3

        # 2. RSI
        rsi = latest.get(f"rsi_{self.params['rsi_period']}", 50)
        if rsi:
            if rsi < self.params["rsi_oversold"]:
                buy_signals += 1
                buy_strength += (self.params["rsi_oversold"] - rsi) / self.params["rsi_oversold"]
            elif rsi > self.params["rsi_overbought"]:
                sell_signals += 1
                sell_strength += (rsi - self.params["rsi_overbought"]) / (100 - self.params["rsi_overbought"])

        # 3. Z-Score
        z_score = latest.get("z_score_20", 0)
        if z_score is not None:
            if z_score < -self.params["z_score_threshold"]:
                buy_signals += 1
                buy_strength += min(abs(z_score) / 4, 1.0)
            elif z_score > self.params["z_score_threshold"]:
                sell_signals += 1
                sell_strength += min(z_score / 4, 1.0)

        # 4. Stochastic
        stoch_k = latest.get("stoch_k", 50)
        if stoch_k is not None:
            if stoch_k < 20:
                buy_signals += 0.5
                buy_strength += 0.3
            elif stoch_k > 80:
                sell_signals += 0.5
                sell_strength += 0.3

        # 5. MFI (资金流量指标)
        mfi = latest.get("mfi", 50)
        if mfi is not None:
            if mfi < 20:
                buy_signals += 0.5
                buy_strength += 0.2
            elif mfi > 80:
                sell_signals += 0.5
                sell_strength += 0.2

        # 情绪反向指标（大众极度悲观时买入，极度乐观时卖出）
        if sentiment:
            sentiment_score = sentiment.get("score", 0)
            if sentiment_score < -0.5:  # 极度悲观
                buy_strength += 0.15
            elif sentiment_score > 0.5:  # 极度乐观
                sell_strength += 0.15

        # === 综合决策 ===
        total_indicators = 5  # 5个指标维度

        if buy_signals >= 2 and buy_strength > 0.5:
            signal_type = SignalType.BUY
            signal_strength = min(buy_strength / total_indicators, 1.0)
        elif sell_signals >= 2 and sell_strength > 0.5:
            signal_type = SignalType.SELL
            signal_strength = min(sell_strength / total_indicators, 1.0)
        else:
            signal_type = SignalType.HOLD
            signal_strength = max(buy_strength, sell_strength) / total_indicators

        # 止损止盈
        atr = latest.get("atr_14", latest["close"] * 0.015)
        bb_middle = latest.get("bb_middle", latest["close"])

        if signal_type == SignalType.BUY:
            stop_loss = latest["close"] - atr * self.params["atr_stop_multiplier"]
            take_profit = bb_middle  # 回归到中轨
        elif signal_type == SignalType.SELL:
            stop_loss = latest["close"] + atr * self.params["atr_stop_multiplier"]
            take_profit = bb_middle
        else:
            stop_loss = None
            take_profit = None

        reason = (
            f"BB%={bb_pct:.2f}, RSI={rsi:.1f}, Z={z_score:.2f}, "
            f"StochK={stoch_k:.1f}, MFI={mfi:.1f} | "
            f"买入信号={buy_signals:.1f}(强度={buy_strength:.2f}), "
            f"卖出信号={sell_signals:.1f}(强度={sell_strength:.2f})"
        )

        signal = StrategySignal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=signal_type,
            signal_strength=round(signal_strength, 3),
            price=latest["close"],
            stop_loss=round(stop_loss, 2) if stop_loss else None,
            take_profit=round(take_profit, 2) if take_profit else None,
            reason=reason,
            indicators_snapshot={
                "bb_pct": round(bb_pct, 3) if bb_pct else None,
                "rsi": round(rsi, 1) if rsi else None,
                "z_score": round(z_score, 2) if z_score else None,
                "stoch_k": round(stoch_k, 1) if stoch_k else None,
                "mfi": round(mfi, 1) if mfi else None,
                "atr": round(atr, 2) if atr else None,
            }
        )

        self.last_signal = signal
        return signal

    def calculate_position_size(self, account_value: float, price: float,
                                 risk_pct: float = 0.02, atr: float = None) -> float:
        """均值回归仓位计算 - 根据偏离度调整"""
        if not atr or atr <= 0:
            atr = price * 0.015

        stop_distance = atr * self.params["atr_stop_multiplier"]
        if stop_distance <= 0:
            return 0

        dollar_risk = account_value * risk_pct
        shares = dollar_risk / stop_distance
        max_shares = (account_value * 0.08) / price  # 均值回归单只最多8%

        return min(shares, max_shares)

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """确保所需指标已计算"""
        if "bb_pct" not in df.columns:
            sma = df["close"].rolling(self.params["bb_period"]).mean()
            std = df["close"].rolling(self.params["bb_period"]).std()
            df["bb_upper"] = sma + std * self.params["bb_std"]
            df["bb_lower"] = sma - std * self.params["bb_std"]
            df["bb_middle"] = sma
            df["bb_pct"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

        if "z_score_20" not in df.columns:
            mean = df["close"].rolling(20).mean()
            std = df["close"].rolling(20).std()
            df["z_score_20"] = (df["close"] - mean) / std

        return df
