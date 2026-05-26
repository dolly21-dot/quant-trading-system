"""
动量趋势策略 - 结合趋势跟踪与动量指标
入场: EMA金叉 + ADX趋势确认 + RSI非超买/超卖
出场: EMA死叉 或 RSI极端值 或 ATR止损
"""
import numpy as np
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, StrategySignal, SignalType


class MomentumTrendStrategy(BaseStrategy):
    """动量趋势策略"""

    def __init__(self, params: dict = None):
        default_params = {
            "fast_ma": 10,
            "slow_ma": 30,
            "ma_type": "EMA",  # EMA or SMA
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "adx_threshold": 25,
            "atr_stop_multiplier": 2.0,
            "volume_surge_pct": 1.5,
            "signal_strength_weights": {
                "ma_cross": 0.35,
                "adx": 0.20,
                "rsi": 0.20,
                "volume": 0.15,
                "macd": 0.10,
            }
        }
        if params:
            default_params.update(params)
        super().__init__(name="momentum_trend", params=default_params)

    def generate_signal(self, data: pd.DataFrame, **kwargs) -> StrategySignal:
        """生成动量趋势信号"""
        symbol = kwargs.get("symbol", "UNKNOWN")
        sentiment = kwargs.get("sentiment", {})

        if data.empty or len(data) < self.params["slow_ma"] + 10:
            return StrategySignal(
                symbol=symbol, strategy_name=self.name,
                signal_type=SignalType.HOLD, signal_strength=0.0,
                reason="数据不足"
            )

        df = data.copy()
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        # 计算指标（如果尚未计算）
        df = self._ensure_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # === 信号判断 ===
        signals = {}

        # 1. 均线交叉信号
        fast_col = f"ema_{self.params['fast_ma']}" if self.params["ma_type"] == "EMA" else f"sma_{self.params['fast_ma']}"
        slow_col = f"ema_{self.params['slow_ma']}" if self.params["ma_type"] == "EMA" else f"sma_{self.params['slow_ma']}"

        fast_now = latest.get(fast_col)
        slow_now = latest.get(slow_col)
        fast_prev = prev.get(fast_col)
        slow_prev = prev.get(slow_col)

        if fast_now and slow_now and fast_prev and slow_prev:
            if fast_now > slow_now and fast_prev <= slow_prev:
                signals["ma_cross"] = ("BUY", 1.0)  # 金叉
            elif fast_now < slow_now and fast_prev >= slow_prev:
                signals["ma_cross"] = ("SELL", 1.0)  # 死叉
            elif fast_now > slow_now:
                signals["ma_cross"] = ("BUY", 0.5)   # 多头排列
            else:
                signals["ma_cross"] = ("SELL", 0.5)   # 空头排列
        else:
            signals["ma_cross"] = ("HOLD", 0.0)

        # 2. ADX趋势强度
        adx = latest.get("adx", 0)
        if adx and adx > self.params["adx_threshold"]:
            di_plus = latest.get("di_plus", 0)
            di_minus = latest.get("di_minus", 0)
            if di_plus > di_minus:
                signals["adx"] = ("BUY", min(adx / 50, 1.0))
            else:
                signals["adx"] = ("SELL", min(adx / 50, 1.0))
        else:
            signals["adx"] = ("HOLD", 0.0)

        # 3. RSI
        rsi = latest.get(f"rsi_{self.params['rsi_period']}", 50)
        if rsi:
            if rsi > self.params["rsi_overbought"]:
                signals["rsi"] = ("SELL", (rsi - self.params["rsi_overbought"]) / 30)
            elif rsi < self.params["rsi_oversold"]:
                signals["rsi"] = ("BUY", (self.params["rsi_oversold"] - rsi) / 30)
            else:
                # 中性区间，根据方向给分
                if rsi > 50:
                    signals["rsi"] = ("BUY", (rsi - 50) / 50)
                else:
                    signals["rsi"] = ("SELL", (50 - rsi) / 50)
        else:
            signals["rsi"] = ("HOLD", 0.0)

        # 4. 成交量确认
        vol_ratio = latest.get("volume_ratio", 1.0)
        if vol_ratio and vol_ratio > self.params["volume_surge_pct"]:
            # 放量 - 跟随当前趋势方向
            if signals["ma_cross"][0] == "BUY":
                signals["volume"] = ("BUY", min(vol_ratio / 3, 1.0))
            else:
                signals["volume"] = ("SELL", min(vol_ratio / 3, 1.0))
        else:
            signals["volume"] = ("HOLD", 0.0)

        # 5. MACD确认
        macd_hist = latest.get("macd_histogram", 0)
        if macd_hist is not None:
            if macd_hist > 0:
                signals["macd"] = ("BUY", min(abs(macd_hist) / latest["close"] * 100, 1.0))
            else:
                signals["macd"] = ("SELL", min(abs(macd_hist) / latest["close"] * 100, 1.0))
        else:
            signals["macd"] = ("HOLD", 0.0)

        # === 综合评分 ===
        weights = self.params["signal_strength_weights"]
        buy_score = 0.0
        sell_score = 0.0

        for key, (direction, strength) in signals.items():
            w = weights.get(key, 0.1)
            if direction == "BUY":
                buy_score += w * strength
            elif direction == "SELL":
                sell_score += w * strength

        # 情绪加权
        if sentiment:
            sentiment_score = sentiment.get("score", 0)
            if sentiment_score > 0:
                buy_score += 0.1 * sentiment_score
            elif sentiment_score < 0:
                sell_score += 0.1 * abs(sentiment_score)

        # 决策
        if buy_score > sell_score and buy_score > 0.3:
            signal_type = SignalType.BUY
            signal_strength = min(buy_score, 1.0)
        elif sell_score > buy_score and sell_score > 0.3:
            signal_type = SignalType.SELL
            signal_strength = min(sell_score, 1.0)
        else:
            signal_type = SignalType.HOLD
            signal_strength = max(buy_score, sell_score)

        # 止损止盈计算
        atr = latest.get("atr_14", latest["close"] * 0.02)
        if signal_type == SignalType.BUY:
            stop_loss = latest["close"] - atr * self.params["atr_stop_multiplier"]
            take_profit = latest["close"] + atr * self.params["atr_stop_multiplier"] * 2
        elif signal_type == SignalType.SELL:
            stop_loss = latest["close"] + atr * self.params["atr_stop_multiplier"]
            take_profit = latest["close"] - atr * self.params["atr_stop_multiplier"] * 2
        else:
            stop_loss = None
            take_profit = None

        reason = self._build_reason(signals, buy_score, sell_score)

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
                "fast_ma": round(fast_now, 2) if fast_now else None,
                "slow_ma": round(slow_now, 2) if slow_now else None,
                "rsi": round(rsi, 1) if rsi else None,
                "adx": round(adx, 1) if adx else None,
                "atr": round(atr, 2) if atr else None,
                "macd_hist": round(macd_hist, 4) if macd_hist else None,
                "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
            }
        )

        self.last_signal = signal
        return signal

    def calculate_position_size(self, account_value: float, price: float,
                                 risk_pct: float = 0.02, atr: float = None) -> float:
        """ATR波动率调仓"""
        if not atr or atr <= 0:
            atr = price * 0.02  # 默认2%作为ATR

        stop_distance = atr * self.params["atr_stop_multiplier"]
        if stop_distance <= 0:
            return 0

        dollar_risk = account_value * risk_pct
        shares = dollar_risk / stop_distance
        max_shares = (account_value * 0.10) / price  # 单只最多10%

        return min(shares, max_shares)

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """确保所需指标已计算"""
        fast_col = f"ema_{self.params['fast_ma']}"
        slow_col = f"ema_{self.params['slow_ma']}"

        if fast_col not in df.columns:
            df[fast_col] = df["close"].ewm(span=self.params["fast_ma"]).mean()
        if slow_col not in df.columns:
            df[slow_col] = df["close"].ewm(span=self.params["slow_ma"]).mean()

        return df

    def _build_reason(self, signals: dict, buy_score: float, sell_score: float) -> str:
        """构建信号原因说明"""
        parts = []
        for key, (direction, strength) in signals.items():
            if strength > 0.1:
                parts.append(f"{key}={direction}({strength:.2f})")

        direction = "看多" if buy_score > sell_score else "看空"
        return f"{direction}[买分={buy_score:.2f}, 卖分={sell_score:.2f}] | {' | '.join(parts)}"
