"""
绩效分析模块 - 交易绩效统计与可视化
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from data.db import DatabaseManager


class PerformanceAnalyzer:
    """绩效分析器"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()

    def calculate_portfolio_metrics(self, start_date: datetime = None,
                                     end_date: datetime = None) -> dict:
        """计算组合绩效指标"""
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        trades = self.db.get_trades_by_date_range(start_date, end_date)

        if not trades:
            return self._empty_metrics()

        # 按策略分组
        by_strategy = {}
        for trade in trades:
            strategy = trade.strategy_name or "unknown"
            if strategy not in by_strategy:
                by_strategy[strategy] = []
            by_strategy[strategy].append(trade)

        # 全组合指标
        all_metrics = self._compute_metrics(trades)

        # 各策略指标
        strategy_metrics = {}
        for strategy, strategy_trades in by_strategy.items():
            strategy_metrics[strategy] = self._compute_metrics(strategy_trades)

        all_metrics["strategy_breakdown"] = strategy_metrics
        all_metrics["period"] = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        }
        return all_metrics

    def _compute_metrics(self, trades: list) -> dict:
        """计算绩效指标"""
        if not trades:
            return self._empty_metrics()

        # 只统计已成交的
        filled = [t for t in trades if t.status == "filled"]
        if not filled:
            return self._empty_metrics()

        # 基本统计
        total_trades = len(filled)
        buys = [t for t in filled if t.side == "BUY"]
        sells = [t for t in filled if t.side == "SELL"]

        # 计算盈亏（需要匹配买卖对）
        pnl_list = []
        for trade in filled:
            if hasattr(trade, "pnl") and trade.pnl is not None:
                pnl_list.append(trade.pnl)

        # 简化：使用已实现的PnL
        returns = []
        for t in filled:
            if hasattr(t, "return_pct") and t.return_pct is not None:
                returns.append(t.return_pct)

        if not returns:
            returns = [0.0]  # 避免除零

        returns_arr = np.array(returns)
        winning = returns_arr[returns_arr > 0]
        losing = returns_arr[returns_arr < 0]

        win_rate = len(winning) / max(total_trades, 1)
        avg_win = np.mean(winning) if len(winning) > 0 else 0
        avg_loss = np.mean(np.abs(losing)) if len(losing) > 0 else 0

        # Profit Factor
        gross_profit = np.sum(winning) if len(winning) > 0 else 0
        gross_loss = np.sum(np.abs(losing)) if len(losing) > 0 else 0.001
        profit_factor = gross_profit / gross_loss

        # Sharpe Ratio (简化)
        if len(returns_arr) > 1 and returns_arr.std() > 0:
            sharpe = (returns_arr.mean() / returns_arr.std()) * np.sqrt(252)
        else:
            sharpe = 0

        # Max Drawdown (从累计收益序列计算)
        cumulative = np.cumsum(returns_arr)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0

        # 期望值
        expected_value = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        return {
            "total_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(float(win_rate), 3),
            "avg_win_pct": round(float(avg_win), 3),
            "avg_loss_pct": round(float(avg_loss), 3),
            "profit_factor": round(float(profit_factor), 3),
            "sharpe_ratio": round(float(sharpe), 3),
            "max_drawdown_pct": round(float(max_drawdown), 3),
            "total_return_pct": round(float(np.sum(returns_arr)), 3),
            "expected_value": round(float(expected_value), 4),
            "avg_daily_trades": round(total_trades / max(30, 1), 1),
        }

    def generate_daily_report(self) -> dict:
        """生成日度报告"""
        end = datetime.now(timezone.utc)
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)

        metrics = self.calculate_portfolio_metrics(start, end)

        # 今日持仓
        positions = []
        # TODO: 从 Trading212 获取实时持仓

        report = {
            "report_type": "daily",
            "report_date": end.isoformat(),
            "portfolio_metrics": metrics,
            "summary": self._generate_summary_text(metrics),
            "recommendations": self._generate_recommendations(metrics),
        }
        return report

    def generate_weekly_report(self) -> dict:
        """生成周度报告"""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)

        metrics = self.calculate_portfolio_metrics(start, end)

        report = {
            "report_type": "weekly",
            "report_date": end.isoformat(),
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "portfolio_metrics": metrics,
            "strategy_comparison": self._compare_strategies(metrics),
            "summary": self._generate_summary_text(metrics),
            "recommendations": self._generate_recommendations(metrics),
        }
        return report

    def _compare_strategies(self, metrics: dict) -> dict:
        """策略对比"""
        breakdown = metrics.get("strategy_breakdown", {})
        if not breakdown:
            return {}

        # 按Sharpe排序
        ranked = sorted(
            breakdown.items(),
            key=lambda x: x[1].get("sharpe_ratio", 0),
            reverse=True
        )

        comparison = {}
        for rank, (name, data) in enumerate(ranked, 1):
            comparison[name] = {
                "rank": rank,
                "sharpe": data.get("sharpe_ratio", 0),
                "return": data.get("total_return_pct", 0),
                "win_rate": data.get("win_rate", 0),
                "trades": data.get("total_trades", 0),
            }
        return comparison

    def _generate_summary_text(self, metrics: dict) -> str:
        """生成摘要文字"""
        if not metrics or metrics.get("total_trades", 0) == 0:
            return "本期无已成交交易。"

        lines = [
            f"交易{metrics['total_trades']}笔, "
            f"胜率{metrics.get('win_rate', 0):.1%}, "
            f"总回报{metrics.get('total_return_pct', 0):.2f}%, "
            f"Sharpe={metrics.get('sharpe_ratio', 0):.2f}, "
            f"最大回撤{metrics.get('max_drawdown_pct', 0):.2f}%, "
            f"盈利因子{metrics.get('profit_factor', 0):.2f}"
        ]
        return "\n".join(lines)

    def _generate_recommendations(self, metrics: dict) -> list[str]:
        """生成建议"""
        recs = []

        if metrics.get("win_rate", 0) < 0.4:
            recs.append("⚠️ 胜率低于40%，建议检查入场条件是否过于宽松")

        if metrics.get("profit_factor", 0) < 1.0:
            recs.append("🚨 盈利因子<1，策略亏损中，建议暂停或调整参数")

        if metrics.get("max_drawdown_pct", 0) > 10:
            recs.append("⚠️ 最大回撤>10%，建议收紧止损或降低仓位")

        if metrics.get("sharpe_ratio", 0) < 0.5:
            recs.append("⚠️ Sharpe<0.5，风险调整后收益不佳")

        if metrics.get("total_trades", 0) < 5:
            recs.append("📊 交易样本不足，暂不做参数调整")

        # 策略级建议
        breakdown = metrics.get("strategy_breakdown", {})
        for name, data in breakdown.items():
            if data.get("profit_factor", 0) < 0.8:
                recs.append(f"🔴 策略{name}盈利因子<0.8，建议禁用或重调")
            if data.get("win_rate", 0) < 0.3:
                recs.append(f"🟡 策略{name}胜率<30%，检查信号过滤条件")

        return recs

    @staticmethod
    def _empty_metrics() -> dict:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "total_return_pct": 0.0,
            "expected_value": 0.0,
            "strategy_breakdown": {},
        }
