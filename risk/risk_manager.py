"""
风险管理器 - 全局风控层
核心约束:
  1. 不做杠杆 (硬约束)
  2. 单股最大仓位限制
  3. 组合风险限制
  4. 日内回撤熔断
  5. 相关性控制
  6. 止损止盈强制执行
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

from loguru import logger

from config.settings import RiskConfig
from strategy.base import StrategySignal, SignalType
from data.db import DatabaseManager


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    approved: bool
    reason: str = ""
    adjusted_quantity: float = 0.0
    adjusted_stop_loss: float = None
    adjusted_take_profit: float = None
    warnings: list = None

    def __post_init__(self):
        self.warnings = self.warnings or []


class RiskManager:
    """全局风控管理器"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()
        self.daily_pnl = 0.0
        self.daily_start_value = 0.0
        self.trading_paused = False  # 熔断标志
        self.correlation_cache: dict = {}

    def check_signal(self, signal: StrategySignal, quantity: float,
                     account_value: float, current_positions: dict,
                     t212_ticker: str = "") -> RiskCheckResult:
        """
        风控检查 - 所有交易信号必须通过此检查
        Args:
            signal: 策略信号
            quantity: 建议数量
            account_value: 账户总价值
            current_positions: 当前持仓 {ticker: {quantity, market_value, ...}}
            t212_ticker: Trading 212 ticker
        """
        warnings = []

        # === 0. 熔断检查 ===
        if self.trading_paused:
            return RiskCheckResult(
                approved=False,
                reason="⚠️ 交易已暂停（日回撤熔断触发）",
                warnings=["日内回撤超过限制，交易已暂停至下一个交易日"]
            )

        # === 1. 杠杆硬约束 ===
        if not RiskConfig.NO_LEVERAGE:
            # 虽然永远不会走到这里，但作为安全阀门
            pass

        # 确保不购买CFD或杠杆产品
        if t212_ticker and "CFD" in t212_ticker.upper():
            return RiskCheckResult(
                approved=False,
                reason="⛔ 禁止交易CFD/杠杆产品",
                warnings=["系统硬约束：不做杠杆"]
            )

        # === 2. 单股最大仓位限制 ===
        position_value = signal.price * quantity
        position_pct = position_value / account_value if account_value > 0 else 1.0

        if position_pct > RiskConfig.MAX_POSITION_PCT:
            # 自动调整数量
            max_value = account_value * RiskConfig.MAX_POSITION_PCT
            adjusted_quantity = int(max_value / signal.price)
            if adjusted_quantity <= 0:
                return RiskCheckResult(
                    approved=False,
                    reason=f"⛔ 仓位超限: {position_pct:.1%} > {RiskConfig.MAX_POSITION_PCT:.0%}",
                    warnings=[f"单股最大仓位{RiskConfig.MAX_POSITION_PCT:.0%}"]
                )
            quantity = adjusted_quantity
            position_value = signal.price * quantity
            position_pct = position_value / account_value
            warnings.append(f"仓位已调整至{position_pct:.1%}")

        # === 3. 已有同股票持仓检查 ===
        existing = current_positions.get(t212_ticker, {})
        existing_value = float(existing.get("market_value", 0))
        total_value = existing_value + position_value
        total_pct = total_value / account_value if account_value > 0 else 1.0

        if signal.signal_type == SignalType.BUY and total_pct > RiskConfig.MAX_POSITION_PCT * 1.5:
            return RiskCheckResult(
                approved=False,
                reason=f"⛔ 加仓后持仓过重: {total_pct:.1%}",
                warnings=[f"已有持仓${existing_value:.0f}，加仓后占比{total_pct:.1%}"]
            )

        # === 4. 组合风险检查（单笔最大风险） ===
        if signal.stop_loss and signal.price:
            risk_per_share = abs(signal.price - signal.stop_loss)
            total_risk = risk_per_share * quantity
            risk_pct = total_risk / account_value

            if risk_pct > RiskConfig.MAX_PORTFOLIO_RISK_PCT:
                adjusted_quantity = int(
                    (account_value * RiskConfig.MAX_PORTFOLIO_RISK_PCT) / risk_per_share
                )
                if adjusted_quantity <= 0:
                    return RiskCheckResult(
                        approved=False,
                        reason=f"⛔ 单笔风险过大: {risk_pct:.1%}",
                    )
                quantity = adjusted_quantity
                warnings.append(f"数量已调减以控制风险在{RiskConfig.MAX_PORTFOLIO_RISK_PCT:.0%}内")

        # === 5. 止损必须设置 ===
        adjusted_stop_loss = signal.stop_loss
        if signal.signal_type == SignalType.BUY and not signal.stop_loss:
            adjusted_stop_loss = signal.price * (1 - RiskConfig.DEFAULT_STOP_LOSS_PCT)
            warnings.append(f"未设止损，自动设置: {adjusted_stop_loss:.2f} (-{RiskConfig.DEFAULT_STOP_LOSS_PCT:.0%})")

        if signal.signal_type == SignalType.SELL and not signal.stop_loss:
            adjusted_stop_loss = signal.price * (1 + RiskConfig.DEFAULT_STOP_LOSS_PCT)
            warnings.append(f"未设止损，自动设置: {adjusted_stop_loss:.2f} (+{RiskConfig.DEFAULT_STOP_LOSS_PCT:.0%})")

        # === 6. 止盈检查 ===
        adjusted_take_profit = signal.take_profit
        if signal.signal_type == SignalType.BUY and not signal.take_profit:
            adjusted_take_profit = signal.price * (1 + RiskConfig.DEFAULT_TAKE_PROFIT_PCT)
            warnings.append(f"未设止盈，自动设置: {adjusted_take_profit:.2f} (+{RiskConfig.DEFAULT_TAKE_PROFIT_PCT:.0%})")

        # === 7. 相关性控制 ===
        sector = self._get_sector(t212_ticker, current_positions)
        sector_exposure = self._calculate_sector_exposure(sector, current_positions, account_value)
        if sector_exposure + position_pct > 0.30:  # 单行业最大30%
            warnings.append(f"行业{sector}敞口较高: {sector_exposure:.1%}+{position_pct:.1%}")

        # === 8. 日内回撤检查 ===
        if self.daily_start_value > 0:
            current_drawdown = (self.daily_start_value - account_value) / self.daily_start_value
            if current_drawdown > RiskConfig.MAX_DAILY_DRAWDOWN_PCT:
                self.trading_paused = True
                return RiskCheckResult(
                    approved=False,
                    reason=f"⛔ 日内回撤{current_drawdown:.1%}超限，交易暂停",
                    warnings=[f"当日回撤已达{current_drawdown:.1%}，暂停交易至下一交易日"]
                )

        # === 9. 信号强度门槛 ===
        if signal.signal_strength < 0.4:
            return RiskCheckResult(
                approved=False,
                reason=f"⛔ 信号强度不足: {signal.signal_strength:.2f} < 0.4",
            )

        # 所有检查通过
        if warnings:
            for w in warnings:
                logger.warning(f"⚠️ {w}")

        return RiskCheckResult(
            approved=True,
            reason="风控通过",
            adjusted_quantity=quantity,
            adjusted_stop_loss=adjusted_stop_loss,
            adjusted_take_profit=adjusted_take_profit,
            warnings=warnings,
        )

    def check_portfolio_risk(self, account_value: float,
                              current_positions: dict) -> dict:
        """
        组合级风险检查
        Returns:
            风险报告字典
        """
        if not current_positions or account_value <= 0:
            return {"risk_level": "low", "total_exposure": 0, "sectors": {}}

        # 行业分布
        sectors = {}
        total_exposure = 0.0
        for ticker, pos in current_positions.items():
            sector = self._get_sector(ticker, current_positions)
            value = float(pos.get("market_value", 0))
            total_exposure += value
            sectors[sector] = sectors.get(sector, 0) + value

        # 归一化
        sector_pcts = {k: v / account_value for k, v in sectors.items()}
        total_exposure_pct = total_exposure / account_value

        # 风险等级
        max_sector = max(sector_pcts.values()) if sector_pcts else 0
        if total_exposure_pct > 0.9 or max_sector > 0.35:
            risk_level = "high"
        elif total_exposure_pct > 0.7 or max_sector > 0.25:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "risk_level": risk_level,
            "total_exposure": total_exposure,
            "total_exposure_pct": total_exposure_pct,
            "sectors": sector_pcts,
            "concentration_risk": max_sector,
            "position_count": len(current_positions),
        }

    def reset_daily(self, account_value: float):
        """重置日度风控状态"""
        self.daily_start_value = account_value
        self.daily_pnl = 0.0
        self.trading_paused = False
        logger.info(f"日度风控重置: 起始值=${account_value:,.2f}")

    def update_daily_pnl(self, current_value: float):
        """更新日内PnL"""
        if self.daily_start_value > 0:
            self.daily_pnl = current_value - self.daily_start_value
            drawdown_pct = (self.daily_start_value - current_value) / self.daily_start_value

            if drawdown_pct > RiskConfig.MAX_DAILY_DRAWDOWN_PCT * 0.5:
                logger.warning(
                    f"⚠️ 日内回撤警告: {drawdown_pct:.1%} "
                    f"(限制={RiskConfig.MAX_DAILY_DRAWDOWN_PCT:.0%})"
                )

            if drawdown_pct > RiskConfig.MAX_DAILY_DRAWDOWN_PCT:
                self.trading_paused = True
                logger.critical(
                    f"🚨 日内回撤熔断: {drawdown_pct:.1%} > {RiskConfig.MAX_DAILY_DRAWDOWN_PCT:.0%}"
                )

    @staticmethod
    def _get_sector(ticker: str, positions: dict) -> str:
        """从ticker推断行业"""
        # 简单映射
        ticker_upper = ticker.upper()
        sector_map = {
            "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "NVDA": "tech",
            "AMZN": "tech", "META": "tech", "TSM": "tech", "AVGO": "tech",
            "JNJ": "healthcare", "UNH": "healthcare", "LLY": "healthcare",
            "PFE": "healthcare", "ABBV": "healthcare",
            "JPM": "finance", "BAC": "finance", "GS": "finance", "V": "finance", "MA": "finance",
            "XOM": "energy", "CVX": "energy", "COP": "energy",
            "WMT": "consumer", "COST": "consumer", "PG": "consumer",
            "SPY": "index", "QQQ": "index", "IWM": "index", "DIA": "index",
        }
        symbol = ticker.split("_")[0] if "_" in ticker else ticker
        return sector_map.get(symbol, "other")

    @staticmethod
    def _calculate_sector_exposure(sector: str, positions: dict,
                                    account_value: float) -> float:
        """计算行业敞口"""
        if account_value <= 0 or not positions:
            return 0.0

        sector_value = 0.0
        for ticker, pos in positions.items():
            pos_sector = RiskManager._get_sector(ticker, positions)
            if pos_sector == sector:
                sector_value += float(pos.get("market_value", 0))

        return sector_value / account_value
