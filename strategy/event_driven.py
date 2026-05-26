"""
事件驱动策略 - 基于新闻、财报、宏观事件交易
入场: 重大事件 + 情绪极端 + 成交量异动
出场: 事件消化完毕 或 止损
"""
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, StrategySignal, SignalType


class EventDrivenStrategy(BaseStrategy):
    """事件驱动策略"""

    def __init__(self, params: dict = None):
        default_params = {
            "sentiment_threshold": 0.3,       # 情绪阈值
            "volume_surge_pct": 1.5,          # 成交量异动倍数
            "news_weight": 0.4,               # 新闻权重
            "earnings_weight": 0.3,           # 财报权重
            "macro_weight": 0.3,              # 宏观权重
            "atr_stop_multiplier": 2.5,       # 事件驱动给更大止损空间
            "holding_period_max": 5,          # 事件驱动短持仓
            "event_decay_hours": 48,          # 事件影响衰减时间
        }
        if params:
            default_params.update(params)
        super().__init__(name="event_driven", params=default_params)

    def generate_signal(self, data: pd.DataFrame, **kwargs) -> StrategySignal:
        """生成事件驱动信号"""
        symbol = kwargs.get("symbol", "UNKNOWN")
        news_sentiment = kwargs.get("sentiment", {})
        earnings_info = kwargs.get("earnings", {})
        macro_events = kwargs.get("macro_events", [])

        if data.empty:
            return StrategySignal(
                symbol=symbol, strategy_name=self.name,
                signal_type=SignalType.HOLD, signal_strength=0.0,
                reason="无数据"
            )

        latest = data.iloc[-1]
        news_score = 0.0
        earnings_score = 0.0
        macro_score = 0.0
        event_detected = False

        # === 1. 新闻情绪分析 ===
        if news_sentiment:
            sentiment_label = news_sentiment.get("sentiment", "neutral")
            sentiment_score = news_sentiment.get("score", 0.0)
            article_count = news_sentiment.get("article_count", 0)

            if abs(sentiment_score) > self.params["sentiment_threshold"] and article_count >= 3:
                event_detected = True
                # 情绪方向
                news_score = sentiment_score * self.params["news_weight"]
                # 文章数量加成
                if article_count > 10:
                    news_score *= 1.5

        # === 2. 财报事件 ===
        if earnings_info:
            surprise = earnings_info.get("earnings_surprise_pct", 0)
            if abs(surprise) > 5:  # 财报超预期5%以上
                event_detected = True
                earnings_score = (surprise / 10) * self.params["earnings_weight"]

        # === 3. 宏观事件 ===
        high_impact_events = [e for e in macro_events if e.get("impact") == "high"]
        if high_impact_events:
            event_detected = True
            for event in high_impact_events:
                # Fed利率决议等重大事件
                event_type = event.get("event_type", "")
                if event_type == "FOMC":
                    macro_score += 0.3 * self.params["macro_weight"]
                elif event_type == "CPI":
                    macro_score += 0.2 * self.params["macro_weight"]
                elif event_type == "Employment":
                    macro_score += 0.15 * self.params["macro_weight"]

            # 宏观事件方向性判断
            for event in high_impact_events:
                actual = event.get("actual_value")
                forecast = event.get("forecast_value")
                if actual is not None and forecast is not None:
                    delta = (actual - forecast) / max(abs(forecast), 0.001)
                    if event.get("event_type") == "CPI":
                        # CPI高于预期 -> 加息预期 -> 利空
                        macro_score -= delta * 0.5
                    elif event.get("event_type") == "Employment":
                        # 就业好于预期 -> 经济好 -> 利好
                        macro_score += delta * 0.3

        # === 4. 成交量异动 ===
        vol_ratio = latest.get("volume_ratio", 1.0)
        volume_surge = vol_ratio > self.params["volume_surge_pct"] if vol_ratio else False

        if volume_surge:
            event_detected = True

        # === 综合决策 ===
        total_score = news_score + earnings_score + macro_score

        # 需要事件触发 + 方向一致
        if not event_detected:
            return StrategySignal(
                symbol=symbol, strategy_name=self.name,
                signal_type=SignalType.HOLD, signal_strength=0.0,
                reason="无重大事件触发"
            )

        # 成交量确认
        volume_confidence = min((vol_ratio - 1) / 2, 1.0) if volume_surge else 0.0

        if total_score > 0.2 and volume_confidence > 0:
            signal_type = SignalType.BUY
            signal_strength = min(total_score + volume_confidence * 0.2, 1.0)
        elif total_score < -0.2 and volume_confidence > 0:
            signal_type = SignalType.SELL
            signal_strength = min(abs(total_score) + volume_confidence * 0.2, 1.0)
        else:
            signal_type = SignalType.HOLD
            signal_strength = 0.0

        # 止损止盈
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

        reason = (
            f"新闻={news_score:.2f}, 财报={earnings_score:.2f}, 宏观={macro_score:.2f}, "
            f"量比={vol_ratio:.2f}, 事件触发={'是' if event_detected else '否'}"
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
                "news_score": round(news_score, 3),
                "earnings_score": round(earnings_score, 3),
                "macro_score": round(macro_score, 3),
                "volume_ratio": round(vol_ratio, 2),
                "event_detected": event_detected,
                "high_impact_events": len(high_impact_events),
            }
        )

        self.last_signal = signal
        return signal

    def calculate_position_size(self, account_value: float, price: float,
                                 risk_pct: float = 0.015, atr: float = None) -> float:
        """事件驱动仓位 - 事件驱动给更小仓位（风险更大）"""
        if not atr or atr <= 0:
            atr = price * 0.02

        stop_distance = atr * self.params["atr_stop_multiplier"]
        if stop_distance <= 0:
            return 0

        dollar_risk = account_value * risk_pct  # 事件驱动只用1.5%风险
        shares = dollar_risk / stop_distance
        max_shares = (account_value * 0.06) / price  # 事件驱动单只最多6%

        return min(shares, max_shares)
