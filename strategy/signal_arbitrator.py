"""
信号仲裁器 - 解决多策略对同一股票产生矛盾信号的问题

问题场景:
  策略A(动量)发出BUY信号 → 策略B(均值回归)发出SELL信号
  同一只股票，两个策略方向相反，怎么办？

仲裁规则:
  1. 加权投票: 按策略历史绩效(Sharpe)加权
  2. 信号强度阈值: 弱信号让位于强信号
  3. 市场环境适配: 牛市偏趋势，震荡市偏均值回归
  4. 冲突消解: 同方向叠加，反方向取强
  5. 持仓感知: 已持仓时偏向持有/加仓而非反转
"""
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from strategy.base import StrategySignal, SignalType, BaseStrategy


class MarketRegime(Enum):
    BULL = "bull"           # 牛市 - 趋势策略优先
    BEAR = "bear"           # 熊市 - 防守优先
    SIDEWAYS = "sideways"   # 震荡 - 均值回归优先
    UNKNOWN = "unknown"


@dataclass
class ArbitratedSignal:
    """仲裁后的最终信号"""
    symbol: str
    signal_type: SignalType
    signal_strength: float
    price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    reason: str
    contributing_signals: list   # 参与仲裁的信号列表
    strategy_votes: dict         # 各策略投票 {strategy_name: (direction, weight)}
    confidence: float            # 信号置信度 0-1
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class SignalArbitrator:
    """
    信号仲裁器
    
    核心逻辑:
    ┌─────────────────────────────────────────────────────┐
    │  多策略信号输入                                       │
    │  ┌────────┐  ┌────────┐  ┌────────┐               │
    │  │动量 BUY │  │均值SELL│  │事件 BUY│               │
    │  │str=0.7  │  │str=0.4 │  │str=0.6 │               │
    │  └───┬────┘  └───┬────┘  └───┬────┘               │
    │      └────────────┼───────────┘                     │
    │                   ▼                                  │
    │          ┌────────────────┐                          │
    │          │  策略绩效加权    │ ← Sharpe排序加权         │
    │          │  信号强度加权    │ ← 强度越大权重越高       │
    │          │  市场环境适配    │ ← 牛市偏趋势/震荡偏回归  │
    │          │  持仓状态感知    │ ← 已持仓时偏向持有       │
    │          └───────┬────────┘                          │
    │                  ▼                                   │
    │          ┌────────────────┐                          │
    │          │  最终仲裁信号    │                          │
    │          │  BUY str=0.65  │                          │
    │          │  confidence=0.8│                          │
    │          └────────────────┘                          │
    └─────────────────────────────────────────────────────┘
    """

    def __init__(self, strategies: dict[str, BaseStrategy] = None):
        self.strategies = strategies or {}
        self.market_regime = MarketRegime.UNKNOWN

        # 策略市场适配权重
        self.regime_strategy_weights = {
            MarketRegime.BULL: {
                "momentum_trend": 1.5,    # 牛市趋势策略加权
                "mean_reversion": 0.7,    # 牛市均值回归降权
                "event_driven": 1.0,
            },
            MarketRegime.BEAR: {
                "momentum_trend": 0.7,
                "mean_reversion": 1.2,
                "event_driven": 1.3,      # 熊市事件驱动加权
            },
            MarketRegime.SIDEWAYS: {
                "momentum_trend": 0.6,    # 震荡市趋势策略降权
                "mean_reversion": 1.5,    # 震荡市均值回归加权
                "event_driven": 1.0,
            },
            MarketRegime.UNKNOWN: {
                "momentum_trend": 1.0,
                "mean_reversion": 1.0,
                "event_driven": 1.0,
            },
        }

    def set_market_regime(self, regime: MarketRegime):
        """设置当前市场环境"""
        self.market_regime = regime
        logger.info(f"市场环境设定: {regime.value}")

    def arbitrate(self, signals: list[StrategySignal],
                  has_position: bool = False,
                  position_pnl_pct: float = 0.0) -> Optional[ArbitratedSignal]:
        """
        仲裁一组信号
        Args:
            signals: 同一只股票的多个策略信号
            has_position: 当前是否持有该股票
            position_pnl_pct: 当前持仓盈亏百分比
        Returns:
            仲裁后的最终信号，或None(无有效信号)
        """
        if not signals:
            return None

        symbol = signals[0].symbol

        # 过滤HOLD信号
        active_signals = [s for s in signals if s.signal_type != SignalType.HOLD]
        if not active_signals:
            return ArbitratedSignal(
                symbol=symbol,
                signal_type=SignalType.HOLD,
                signal_strength=0.0,
                price=signals[0].price,
                stop_loss=None,
                take_profit=None,
                reason="所有策略信号为HOLD",
                contributing_signals=signals,
                strategy_votes={},
                confidence=1.0,
            )

        # 只有一个有效信号 → 直接使用
        if len(active_signals) == 1:
            s = active_signals[0]
            return ArbitratedSignal(
                symbol=symbol,
                signal_type=s.signal_type,
                signal_strength=s.signal_strength,
                price=s.price,
                stop_loss=s.stop_loss,
                take_profit=s.take_profit,
                reason=f"单一策略信号: {s.strategy_name}",
                contributing_signals=signals,
                strategy_votes={s.strategy_name: (s.signal_type.value, s.signal_strength)},
                confidence=s.signal_strength,
            )

        # === 多策略仲裁 ===
        return self._weighted_voting(active_signals, has_position, position_pnl_pct)

    def _weighted_voting(self, signals: list[StrategySignal],
                          has_position: bool,
                          position_pnl_pct: float) -> ArbitratedSignal:
        """加权投票仲裁"""

        buy_weight = 0.0
        sell_weight = 0.0
        strategy_votes = {}

        for signal in signals:
            # 1. 基础权重 = 信号强度
            base_weight = signal.signal_strength

            # 2. 策略绩效权重 (根据历史Sharpe)
            performance_weight = self._get_strategy_performance_weight(signal.strategy_name)

            # 3. 市场环境适配权重
            regime_weight = self.regime_strategy_weights.get(
                self.market_regime, {}
            ).get(signal.strategy_name, 1.0)

            # 综合权重
            total_weight = base_weight * performance_weight * regime_weight

            # 投票
            if signal.signal_type == SignalType.BUY:
                buy_weight += total_weight
            elif signal.signal_type == SignalType.SELL:
                sell_weight += total_weight
            elif signal.signal_type == SignalType.EXIT:
                sell_weight += total_weight * 1.2  # EXIT权重更高

            strategy_votes[signal.strategy_name] = (
                signal.signal_type.value,
                round(total_weight, 3)
            )

        # === 持仓感知调整 ===
        if has_position:
            if position_pnl_pct > 5:
                # 盈利持仓 → 偏向持有/加仓，提高BUY阈值
                sell_weight *= 0.7
            elif position_pnl_pct < -3:
                # 亏损持仓 → 降低卖出阈值（更倾向止损）
                sell_weight *= 1.3
                buy_weight *= 0.5

        # === 最终决策 ===
        total = buy_weight + sell_weight
        if total == 0:
            final_type = SignalType.HOLD
            final_strength = 0.0
            confidence = 0.0
        elif buy_weight > sell_weight:
            final_type = SignalType.BUY
            final_strength = min(buy_weight / max(total, 0.001), 1.0)
            confidence = buy_weight / max(total, 0.001)
        elif sell_weight > buy_weight:
            final_type = SignalType.SELL
            final_strength = min(sell_weight / max(total, 0.001), 1.0)
            confidence = sell_weight / max(total, 0.001)
        else:
            final_type = SignalType.HOLD
            final_strength = 0.0
            confidence = 0.0

        # 需要绝对优势才执行（避免微小差距频繁交易）
        if confidence < 0.55 and final_type != SignalType.HOLD:
            logger.info(
                f"  仲裁结果不够明确 (confidence={confidence:.2f} < 0.55)，降级为HOLD"
            )
            final_type = SignalType.HOLD
            final_strength = max(final_strength, 0.1)

        # 合并止损止盈
        stop_loss, take_profit = self._merge_stops(signals, final_type)

        # 构建仲裁原因
        reason = self._build_reason(buy_weight, sell_weight, strategy_votes,
                                     final_type, confidence, has_position, position_pnl_pct)

        # 选取最强信号的price
        strongest_signal = max(signals, key=lambda s: s.signal_strength)

        result = ArbitratedSignal(
            symbol=signals[0].symbol,
            signal_type=final_type,
            signal_strength=round(final_strength, 3),
            price=strongest_signal.price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
            contributing_signals=signals,
            strategy_votes=strategy_votes,
            confidence=round(confidence, 3),
        )

        logger.info(
            f"⚖️ 仲裁结果: {signals[0].symbol} → {final_type.value} | "
            f"强度={final_strength:.3f} 置信度={confidence:.3f} | "
            f"买权={buy_weight:.3f} 卖权={sell_weight:.3f}"
        )

        return result

    def _get_strategy_performance_weight(self, strategy_name: str) -> float:
        """根据策略历史绩效计算权重"""
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            return 1.0

        sharpe = strategy.performance.sharpe_ratio
        win_rate = strategy.performance.win_rate

        # Sharpe权重
        if sharpe > 1.5:
            sharpe_w = 1.5
        elif sharpe > 1.0:
            sharpe_w = 1.3
        elif sharpe > 0.5:
            sharpe_w = 1.1
        elif sharpe > 0:
            sharpe_w = 1.0
        elif sharpe > -0.5:
            sharpe_w = 0.8
        else:
            sharpe_w = 0.5

        # 胜率权重
        if win_rate > 0.6:
            win_w = 1.2
        elif win_rate > 0.45:
            win_w = 1.0
        else:
            win_w = 0.7

        return sharpe_w * win_w

    def _merge_stops(self, signals: list[StrategySignal],
                      final_type: SignalType) -> tuple[Optional[float], Optional[float]]:
        """合并止损止盈 — 取最保守值"""
        stops = [s.stop_loss for s in signals if s.stop_loss]
        profits = [s.take_profit for s in signals if s.take_profit]

        stop_loss = None
        take_profit = None

        if stops:
            if final_type == SignalType.BUY:
                stop_loss = min(stops)  # 买入取最小止损（最保守）
            else:
                stop_loss = max(stops)  # 卖出取最大止损

        if profits:
            if final_type == SignalType.BUY:
                take_profit = max(profits)  # 买入取最大止盈
            else:
                take_profit = min(profits)

        return stop_loss, take_profit

    def _build_reason(self, buy_w: float, sell_w: float,
                       votes: dict, final_type: SignalType,
                       confidence: float, has_position: bool,
                       pnl_pct: float) -> str:
        """构建仲裁原因"""
        parts = [f"买权={buy_w:.3f}, 卖权={sell_w:.3f}"]

        for name, (direction, weight) in votes.items():
            parts.append(f"  {name}: {direction}(w={weight:.3f})")

        parts.append(f"市场={self.market_regime.value}")
        parts.append(f"结果={final_type.value}(conf={confidence:.3f})")

        if has_position:
            parts.append(f"持仓PnL={pnl_pct:+.1f}%")

        return " | ".join(parts)

    def batch_arbitrate(self, all_signals: dict[str, list[StrategySignal]],
                         positions: dict[str, dict] = None) -> list[ArbitratedSignal]:
        """
        批量仲裁所有股票的信号
        Args:
            all_signals: {symbol: [signal1, signal2, ...]}
            positions: {symbol: {pnl_pct, quantity, ...}}
        Returns:
            仲裁后的信号列表
        """
        positions = positions or {}
        results = []

        for symbol, signals in all_signals.items():
            pos = positions.get(symbol, {})
            has_pos = pos.get("quantity", 0) > 0
            pnl_pct = pos.get("pnl_pct", 0.0)

            arbitrated = self.arbitrate(signals, has_pos, pnl_pct)
            if arbitrated and arbitrated.signal_type != SignalType.HOLD:
                results.append(arbitrated)

        # 按置信度排序
        results.sort(key=lambda s: s.confidence, reverse=True)

        logger.info(
            f"⚖️ 批量仲裁: {len(all_signals)} 只股票, "
            f"{len(results)} 只有可执行信号"
        )
        return results
