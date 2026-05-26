"""
回测引擎 - 在历史数据上验证策略
"""
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import SystemConfig
from data.market_data import MarketDataFetcher
from strategy.base import BaseStrategy, StrategySignal, SignalType
from strategy.momentum_trend import MomentumTrendStrategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.event_driven import EventDrivenStrategy


@dataclass
class BacktestTrade:
    """回测交易记录"""
    symbol: str
    side: str  # BUY, SELL
    entry_date: datetime
    entry_price: float
    exit_date: Optional[datetime] = None
    exit_price: Optional[float] = None
    quantity: float = 0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: float = 0
    pnl_pct: float = 0
    holding_days: int = 0
    exit_reason: str = ""  # signal, stop_loss, take_profit, end_of_period


@dataclass
class BacktestResult:
    """回测结果"""
    strategy_name: str
    symbol: str
    initial_cash: float
    final_value: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    avg_holding_days: float
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


class Backtester:
    """回测引擎"""

    def __init__(self, initial_cash: float = None):
        self.initial_cash = initial_cash or SystemConfig.BACKTEST_INITIAL_CASH
        self.market_data = MarketDataFetcher()

    def run_backtest(self, strategy: BaseStrategy, symbol: str,
                     start_date: str = None, end_date: str = None,
                     period: str = "1y", interval: str = "1d",
                     data: pd.DataFrame = None) -> BacktestResult:
        """
        运行回测
        Args:
            strategy: 策略实例
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期
            interval: K线频率
            data: 可选，直接传入DataFrame（跳过数据获取）
        """
        logger.info(f"📊 开始回测: {strategy.name} | {symbol} | 周期={period}")

        # 1. 获取数据
        if data is not None and not data.empty:
            df = data.copy()
        else:
            df = self.market_data.fetch_historical(symbol, period=period, interval=interval)
        if df.empty:
            logger.error("无法获取回测数据")
            return self._empty_result(strategy.name, symbol)

        # 计算技术指标
        df = self.market_data.calculate_technical_indicators(df)
        if df.empty or len(df) < 50:
            logger.error("数据不足或指标计算失败")
            return self._empty_result(strategy.name, symbol)

        # 2. 回测循环
        cash = self.initial_cash
        position = 0  # 当前持仓数量
        entry_price = 0
        stop_loss = None
        take_profit = None
        entry_date = None
        trades: list[BacktestTrade] = []
        equity_curve: list[float] = [cash]

        # 滑点与手续费
        slippage_pct = 0.001  # 0.1%滑点
        commission_pct = 0.0005  # 0.05%手续费（Trading 212无佣金，但保留）

        for i in range(50, len(df)):  # 从第50行开始，确保有足够历史数据
            row = df.iloc[i]
            current_price = row["close"]
            current_date = row.get("timestamp", df.index[i])

            # 获取到当前为止的数据（模拟真实情况）
            historical = df.iloc[:i + 1].copy()

            # 检查止损/止盈
            if position > 0:
                # 止损检查
                if stop_loss and current_price <= stop_loss:
                    exit_price = stop_loss * (1 - slippage_pct)
                    pnl = (exit_price - entry_price) * position
                    pnl_pct = (exit_price / entry_price - 1) * 100
                    holding_days = (current_date - entry_date).days if hasattr(current_date, "days") else 0

                    trades.append(BacktestTrade(
                        symbol=symbol, side="SELL",
                        entry_date=entry_date, entry_price=entry_price,
                        exit_date=current_date, exit_price=exit_price,
                        quantity=position, stop_loss=stop_loss,
                        take_profit=take_profit,
                        pnl=pnl, pnl_pct=pnl_pct,
                        holding_days=holding_days,
                        exit_reason="stop_loss"
                    ))

                    cash += exit_price * position * (1 - commission_pct)
                    position = 0
                    stop_loss = None
                    take_profit = None
                    entry_date = None
                    continue

                # 止盈检查
                if take_profit and current_price >= take_profit:
                    exit_price = take_profit * (1 - slippage_pct)
                    pnl = (exit_price - entry_price) * position
                    pnl_pct = (exit_price / entry_price - 1) * 100
                    holding_days = (current_date - entry_date).days if hasattr(current_date, "days") else 0

                    trades.append(BacktestTrade(
                        symbol=symbol, side="SELL",
                        entry_date=entry_date, entry_price=entry_price,
                        exit_date=current_date, exit_price=exit_price,
                        quantity=position, stop_loss=stop_loss,
                        take_profit=take_profit,
                        pnl=pnl, pnl_pct=pnl_pct,
                        holding_days=holding_days,
                        exit_reason="take_profit"
                    ))

                    cash += exit_price * position * (1 - commission_pct)
                    position = 0
                    stop_loss = None
                    take_profit = None
                    entry_date = None
                    continue

            # 生成信号
            signal = strategy.generate_signal(historical, symbol=symbol)

            # 执行交易
            if signal.signal_type == SignalType.BUY and position == 0:
                # 买入
                buy_price = current_price * (1 + slippage_pct)
                shares = int(cash * 0.95 / buy_price)  # 用95%现金买入
                if shares > 0:
                    cost = buy_price * shares * (1 + commission_pct)
                    if cost <= cash:
                        cash -= cost
                        position = shares
                        entry_price = buy_price
                        stop_loss = signal.stop_loss
                        take_profit = signal.take_profit
                        entry_date = current_date

            elif signal.signal_type == SignalType.SELL and position > 0:
                # 卖出
                exit_price = current_price * (1 - slippage_pct)
                pnl = (exit_price - entry_price) * position
                pnl_pct = (exit_price / entry_price - 1) * 100
                holding_days = (current_date - entry_date).days if hasattr(current_date, "days") else 0

                trades.append(BacktestTrade(
                    symbol=symbol, side="SELL",
                    entry_date=entry_date, entry_price=entry_price,
                    exit_date=current_date, exit_price=exit_price,
                    quantity=position, stop_loss=stop_loss,
                    take_profit=take_profit,
                    pnl=pnl, pnl_pct=pnl_pct,
                    holding_days=holding_days,
                    exit_reason="signal"
                ))

                cash += exit_price * position * (1 - commission_pct)
                position = 0
                stop_loss = None
                take_profit = None
                entry_date = None

            # 更新权益曲线
            current_value = cash + (position * current_price if position > 0 else 0)
            equity_curve.append(current_value)

        # 期末平仓
        if position > 0:
            last_price = df.iloc[-1]["close"]
            pnl = (last_price - entry_price) * position
            pnl_pct = (last_price / entry_price - 1) * 100

            trades.append(BacktestTrade(
                symbol=symbol, side="SELL",
                entry_date=entry_date, entry_price=entry_price,
                exit_date=df.iloc[-1].get("timestamp", None),
                exit_price=last_price, quantity=position,
                pnl=pnl, pnl_pct=pnl_pct,
                exit_reason="end_of_period"
            ))
            cash += last_price * position

        # 3. 计算回测结果
        final_value = cash
        total_return_pct = (final_value / self.initial_cash - 1) * 100

        # 计算统计指标
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        win_rate = len(winning) / max(len(trades), 1)

        gross_profit = sum(t.pnl for t in winning) if winning else 0
        gross_loss = abs(sum(t.pnl for t in losing)) if losing else 0.001
        profit_factor = gross_profit / gross_loss

        # Sharpe
        equity_arr = np.array(equity_curve)
        returns = np.diff(equity_arr) / equity_arr[:-1]
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

        # Max Drawdown
        peak = np.maximum.accumulate(equity_arr)
        drawdown = (peak - equity_arr) / peak
        max_dd = np.max(drawdown) * 100 if len(drawdown) > 0 else 0

        avg_win = np.mean([t.pnl_pct for t in winning]) if winning else 0
        avg_loss = np.mean([abs(t.pnl_pct) for t in losing]) if losing else 0
        avg_holding = np.mean([t.holding_days for t in trades]) if trades else 0

        result = BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            initial_cash=self.initial_cash,
            final_value=round(final_value, 2),
            total_return_pct=round(total_return_pct, 2),
            sharpe_ratio=round(float(sharpe), 3),
            max_drawdown_pct=round(float(max_dd), 2),
            win_rate=round(float(win_rate), 3),
            profit_factor=round(float(profit_factor), 3),
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_win_pct=round(float(avg_win), 2),
            avg_loss_pct=round(float(avg_loss), 2),
            avg_holding_days=round(float(avg_holding), 1),
            trades=trades,
            equity_curve=equity_curve,
        )

        logger.info(
            f"📊 回测完成: {strategy.name} | {symbol} | "
            f"收益={total_return_pct:.2f}% | Sharpe={sharpe:.2f} | "
            f"最大回撤={max_dd:.2f}% | 胜率={win_rate:.1%} | "
            f"交易={len(trades)}笔"
        )

        return result

    def _empty_result(self, strategy_name: str, symbol: str) -> BacktestResult:
        return BacktestResult(
            strategy_name=strategy_name, symbol=symbol,
            initial_cash=self.initial_cash, final_value=self.initial_cash,
            total_return_pct=0, sharpe_ratio=0, max_drawdown_pct=0,
            win_rate=0, profit_factor=0, total_trades=0,
            winning_trades=0, losing_trades=0,
            avg_win_pct=0, avg_loss_pct=0, avg_holding_days=0,
        )

    def print_report(self, result: BacktestResult):
        """打印回测报告"""
        print(f"""
╔══════════════════════════════════════════════════════════╗
║                    回测报告                              ║
╠══════════════════════════════════════════════════════════╣
║ 策略: {result.strategy_name:<45}║
║ 标的: {result.symbol:<45}║
╠══════════════════════════════════════════════════════════╣
║ 初始资金: ${result.initial_cash:>10,.2f}                              ║
║ 最终价值: ${result.final_value:>10,.2f}                              ║
║ 总收益:   {result.total_return_pct:>8.2f}%                                ║
║ Sharpe:   {result.sharpe_ratio:>8.3f}                                  ║
║ 最大回撤: {result.max_drawdown_pct:>8.2f}%                                ║
║ 胜率:     {result.win_rate:>8.1%}                                  ║
║ 盈利因子: {result.profit_factor:>8.3f}                                  ║
╠══════════════════════════════════════════════════════════╣
║ 总交易: {result.total_trades:>5}  盈利: {result.winning_trades:>5}  亏损: {result.losing_trades:>5}      ║
║ 平均盈利: {result.avg_win_pct:>7.2f}%  平均亏损: {result.avg_loss_pct:>7.2f}%          ║
║ 平均持仓: {result.avg_holding_days:>7.1f}天                                   ║
╚══════════════════════════════════════════════════════════╝
        """)
