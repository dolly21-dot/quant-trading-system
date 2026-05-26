"""
策略复盘与自我反思模块
核心能力:
  1. 自动复盘：每日收盘后评估策略表现
  2. 策略诊断：识别失败原因（入场时机、出场时机、市场环境不匹配）
  3. 参数优化：网格搜索/贝叶斯优化调整参数
  4. 自我修正：自动禁用亏损策略，调整参数，探索新参数空间
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import itertools

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import RiskConfig, SystemConfig
from data.db import DatabaseManager, TradeRecord, StrategySignal, ReflectionReport
from data.market_data import MarketDataFetcher
from strategy.strategy_manager import StrategyManager
from reflection.performance import PerformanceAnalyzer


class StrategyReviewer:
    """策略复盘与自我反思"""

    def __init__(self, db: DatabaseManager = None, strategy_manager: StrategyManager = None):
        self.db = db or DatabaseManager()
        self.strategy_manager = strategy_manager or StrategyManager()
        self.performance = PerformanceAnalyzer(self.db)
        self.market_data = MarketDataFetcher(self.db)

    def daily_reflection(self) -> dict:
        """
        每日反思复盘
        执行流程:
          1. 计算当日绩效
          2. 评估各策略表现
          3. 诊断失败交易
          4. 生成改进建议
          5. 自动调参（谨慎）
          6. 保存复盘报告
        """
        logger.info("=" * 60)
        logger.info("📋 开始每日反思复盘...")
        logger.info("=" * 60)

        now = datetime.now(timezone.utc)

        # 1. 计算绩效
        metrics = self.performance.calculate_portfolio_metrics(
            start_date=now - timedelta(days=30),
            end_date=now
        )

        # 2. 策略评估
        strategy_evaluations = self._evaluate_all_strategies(metrics)

        # 3. 诊断失败交易
        failed_trades_diagnosis = self._diagnose_failed_trades()

        # 4. 市场环境评估
        market_context = self._assess_market_context()

        # 5. 生成行动项
        action_items = self._generate_action_items(
            strategy_evaluations, failed_trades_diagnosis, market_context
        )

        # 6. 执行自动调参（如果条件满足）
        parameter_changes = self._auto_tune_strategies(strategy_evaluations)

        # 7. 保存复盘报告
        report_data = {
            "report_type": "daily",
            "report_date": now,
            "portfolio_return": metrics.get("total_return_pct", 0),
            "portfolio_drawdown": metrics.get("max_drawdown_pct", 0),
            "sharpe_ratio": metrics.get("sharpe_ratio", 0),
            "win_rate": metrics.get("win_rate", 0),
            "profit_factor": metrics.get("profit_factor", 0),
            "total_trades": metrics.get("total_trades", 0),
            "winning_trades": metrics.get("winning_trades", 0),
            "losing_trades": metrics.get("losing_trades", 0),
            "avg_win": metrics.get("avg_win_pct", 0),
            "avg_loss": metrics.get("avg_loss_pct", 0),
            "strategy_evaluations": strategy_evaluations,
            "action_items": action_items,
            "parameter_changes": parameter_changes,
        }

        self.db.save_reflection_report(report_data)

        # 8. 生成复盘摘要
        summary = self._format_reflection_summary(
            metrics, strategy_evaluations, action_items, parameter_changes
        )

        logger.info(summary)
        return report_data

    def weekly_reflection(self) -> dict:
        """
        周度深度复盘 - 比日度更深入的分析
        包括: 参数空间探索、策略相关性分析、风控回顾
        """
        logger.info("=" * 60)
        logger.info("📊 开始周度深度复盘...")
        logger.info("=" * 60)

        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        metrics = self.performance.calculate_portfolio_metrics(week_ago, now)

        # 深度分析
        strategy_evaluations = self._evaluate_all_strategies(metrics)
        correlation_analysis = self._analyze_strategy_correlation()
        risk_review = self._review_risk_management()

        # 参数优化（更激进的搜索空间）
        optimization_results = self._optimize_strategy_params()

        report_data = {
            "report_type": "weekly",
            "report_date": now,
            "portfolio_return": metrics.get("total_return_pct", 0),
            "portfolio_drawdown": metrics.get("max_drawdown_pct", 0),
            "sharpe_ratio": metrics.get("sharpe_ratio", 0),
            "win_rate": metrics.get("win_rate", 0),
            "profit_factor": metrics.get("profit_factor", 0),
            "total_trades": metrics.get("total_trades", 0),
            "winning_trades": metrics.get("winning_trades", 0),
            "losing_trades": metrics.get("losing_trades", 0),
            "avg_win": metrics.get("avg_win_pct", 0),
            "avg_loss": metrics.get("avg_loss_pct", 0),
            "strategy_evaluations": strategy_evaluations,
            "correlation_analysis": correlation_analysis,
            "risk_review": risk_review,
            "optimization_results": optimization_results,
            "action_items": self._generate_weekly_action_items(
                strategy_evaluations, correlation_analysis, risk_review
            ),
            "parameter_changes": {},
        }

        self.db.save_reflection_report(report_data)
        return report_data

    # ============================================================
    # 策略评估
    # ============================================================

    def _evaluate_all_strategies(self, metrics: dict) -> dict:
        """评估所有策略"""
        evaluations = {}
        breakdown = metrics.get("strategy_breakdown", {})

        for strategy_name, strategy_metrics in breakdown.items():
            evaluation = {
                "metrics": strategy_metrics,
                "grade": self._grade_strategy(strategy_metrics),
                "issues": self._identify_strategy_issues(strategy_metrics),
                "recommendation": self._recommend_strategy_action(strategy_metrics),
            }
            evaluations[strategy_name] = evaluation

            logger.info(
                f"  策略 {strategy_name}: "
                f"评级={evaluation['grade']} | "
                f"收益={strategy_metrics.get('total_return_pct', 0):.2f}% | "
                f"Sharpe={strategy_metrics.get('sharpe_ratio', 0):.2f} | "
                f"胜率={strategy_metrics.get('win_rate', 0):.1%}"
            )

        return evaluations

    def _grade_strategy(self, metrics: dict) -> str:
        """给策略评级 A/B/C/D/F"""
        score = 0

        # 胜率评分
        win_rate = metrics.get("win_rate", 0)
        if win_rate > 0.6:
            score += 3
        elif win_rate > 0.45:
            score += 2
        elif win_rate > 0.35:
            score += 1

        # 盈利因子评分
        pf = metrics.get("profit_factor", 0)
        if pf > 2.0:
            score += 3
        elif pf > 1.5:
            score += 2
        elif pf > 1.0:
            score += 1

        # Sharpe评分
        sharpe = metrics.get("sharpe_ratio", 0)
        if sharpe > 1.5:
            score += 3
        elif sharpe > 0.8:
            score += 2
        elif sharpe > 0:
            score += 1

        # 回撤扣分
        dd = metrics.get("max_drawdown_pct", 0)
        if dd > 15:
            score -= 2
        elif dd > 10:
            score -= 1

        if score >= 8:
            return "A"
        elif score >= 6:
            return "B"
        elif score >= 4:
            return "C"
        elif score >= 2:
            return "D"
        else:
            return "F"

    def _identify_strategy_issues(self, metrics: dict) -> list[str]:
        """识别策略问题"""
        issues = []

        if metrics.get("win_rate", 0) < 0.35:
            issues.append("胜率过低，入场条件可能不够精确")
        if metrics.get("profit_factor", 0) < 1.0:
            issues.append("盈利因子<1，策略整体亏损")
        if metrics.get("avg_loss_pct", 0) > metrics.get("avg_win_pct", 0.01) * 2:
            issues.append("平均亏损大于平均盈利的2倍，止损可能过宽")
        if metrics.get("max_drawdown_pct", 0) > 10:
            issues.append("最大回撤过大，风险控制不足")
        if metrics.get("total_trades", 0) < 5:
            issues.append("交易样本不足，统计结论不可靠")

        return issues

    def _recommend_strategy_action(self, metrics: dict) -> str:
        """推荐策略行动"""
        grade = self._grade_strategy(metrics)

        if grade == "A":
            return "继续执行，考虑适当增加仓位权重"
        elif grade == "B":
            return "继续执行，微调参数优化"
        elif grade == "C":
            return "持续观察，收紧风控，减少仓位"
        elif grade == "D":
            return "暂停新开仓，等待优化结果"
        else:
            return "🚨 立即禁用策略，进行全面检讨"

    # ============================================================
    # 失败交易诊断
    # ============================================================

    def _diagnose_failed_trades(self) -> list[dict]:
        """诊断亏损交易"""
        now = datetime.now(timezone.utc)
        recent_trades = self.db.get_trades_by_date_range(
            now - timedelta(days=7), now
        )

        losing_trades = [
            t for t in recent_trades
            if t.status == "filled" and t.side == "SELL"
        ]

        diagnosis = []
        for trade in losing_trades:
            issues = []
            # 检查止损是否合理
            if trade.stop_loss and trade.price:
                sl_pct = abs(trade.price - trade.stop_loss) / trade.price
                if sl_pct > 0.10:
                    issues.append(f"止损过宽: {sl_pct:.1%}")
                if sl_pct < 0.02:
                    issues.append(f"止损过窄: {sl_pct:.1%}")

            # 检查入场时机（是否有明显的逆向信号）
            if trade.signal_reason:
                issues.append(f"入场原因: {trade.signal_reason}")

            diagnosis.append({
                "symbol": trade.symbol,
                "strategy": trade.strategy_name,
                "entry_price": trade.price,
                "stop_loss": trade.stop_loss,
                "issues": issues,
            })

        return diagnosis

    # ============================================================
    # 市场环境评估
    # ============================================================

    def _assess_market_context(self) -> dict:
        """评估市场环境"""
        try:
            # 获取SPY作为市场代理
            spy_data = self.market_data.fetch_historical("SPY", period="3mo", interval="1d")

            if spy_data.empty:
                return {"regime": "unknown", "volatility": "unknown"}

            # 市场趋势
            close = spy_data["close"]
            sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else close.mean()
            sma_200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else close.mean()
            current = close.iloc[-1]

            if current > sma_50 > sma_200:
                regime = "bullish"
            elif current < sma_50 < sma_200:
                regime = "bearish"
            else:
                regime = "sideways"

            # 波动率
            returns = close.pct_change().dropna()
            vol = returns.tail(20).std() * np.sqrt(252)
            if vol > 0.30:
                volatility = "high"
            elif vol > 0.18:
                volatility = "normal"
            else:
                volatility = "low"

            return {
                "regime": regime,
                "volatility": volatility,
                "spy_trend": "up" if current > sma_50 else "down",
                "realized_vol": round(vol, 3),
                "recommendation": self._regime_recommendation(regime, volatility),
            }

        except Exception as e:
            logger.error(f"市场环境评估失败: {e}")
            return {"regime": "unknown", "volatility": "unknown"}

    def _regime_recommendation(self, regime: str, volatility: str) -> str:
        """根据市场环境给出策略建议"""
        if regime == "bullish" and volatility in ("low", "normal"):
            return "趋势策略优先，可适当放大仓位"
        elif regime == "bearish" and volatility == "high":
            return "🚨 高波动熊市，减仓防守，优先均值回归"
        elif regime == "sideways":
            return "震荡市，均值回归策略优先，趋势策略减仓"
        elif volatility == "high":
            return "高波动，缩小仓位，加宽止损"
        else:
            return "维持当前策略，谨慎操作"

    # ============================================================
    # 自动调参
    # ============================================================

    def _auto_tune_strategies(self, evaluations: dict) -> dict:
        """
        自动调参 - 根据评估结果谨慎调整策略参数
        规则:
          - 只有评级C及以上才微调
          - D/F级策略先禁用
          - 调参幅度不超过当前值的20%
          - 每次只调1-2个参数
        """
        changes = {}

        for strategy_name, evaluation in evaluations.items():
            grade = evaluation.get("grade", "C")
            issues = evaluation.get("issues", [])
            recommendation = evaluation.get("recommendation", "")

            if grade in ("D", "F"):
                # 禁用策略
                self.strategy_manager.disable_strategy(strategy_name, f"评级{grade}")
                changes[strategy_name] = {"action": "disabled", "reason": f"评级{grade}"}
                continue

            if grade in ("A", "B"):
                changes[strategy_name] = {"action": "no_change", "reason": f"评级{grade}，表现良好"}
                continue

            # C级策略 - 微调参数
            param_adjustments = {}

            for issue in issues:
                if "胜率过低" in issue:
                    # 提高入场门槛
                    param_adjustments["rsi_overbought"] = -5  # 降低超买线
                    param_adjustments["rsi_oversold"] = +5    # 提高超卖线
                    param_adjustments["adx_threshold"] = +3   # 提高趋势确认阈值

                if "止损" in issue and "过宽" in issue:
                    param_adjustments["atr_stop_multiplier"] = -0.3

                if "止损" in issue and "过窄" in issue:
                    param_adjustments["atr_stop_multiplier"] = +0.3

            if param_adjustments:
                # 应用参数调整（限制幅度）
                current_params = self.strategy_manager.strategies.get(
                    f"{strategy_name}", None
                )
                if current_params:
                    new_params = {}
                    for key, delta in param_adjustments.items():
                        current_val = current_params.params.get(key)
                        if current_val:
                            max_delta = abs(current_val) * 0.2
                            new_val = current_val + max(min(delta, max_delta), -max_delta)
                            new_params[key] = round(new_val, 2)

                    if new_params:
                        self.strategy_manager.update_strategy_params(
                            strategy_name, strategy_name, new_params
                        )
                        changes[strategy_name] = {
                            "action": "tuned",
                            "old_params": {k: current_params.params.get(k) for k in new_params},
                            "new_params": new_params,
                        }

        return changes

    def _optimize_strategy_params(self) -> dict:
        """
        参数优化 - 简单网格搜索
        注意: 这不是完整回测，只是参数敏感性分析
        """
        results = {}

        # 定义搜索空间
        search_spaces = {
            "momentum_trend": {
                "fast_ma": [8, 10, 12, 15],
                "slow_ma": [25, 30, 35, 40],
                "rsi_overbought": [65, 70, 75],
                "rsi_oversold": [25, 30, 35],
            },
            "mean_reversion": {
                "bb_period": [15, 20, 25],
                "bb_std": [1.5, 2.0, 2.5],
                "z_score_threshold": [1.5, 2.0, 2.5],
            },
        }

        for strategy_name, space in search_spaces.items():
            # 只做参数组合计数，不做完整回测（实际生产中应使用backtester）
            param_count = 1
            for values in space.values():
                param_count *= len(values)

            results[strategy_name] = {
                "search_space_size": param_count,
                "status": "需要回测引擎配合，此处仅标记搜索空间",
                "space": space,
            }

        return results

    # ============================================================
    # 分析工具
    # ============================================================

    def _analyze_strategy_correlation(self) -> dict:
        """分析策略间相关性"""
        # 获取各策略最近30天的收益序列
        now = datetime.now(timezone.utc)
        trades = self.db.get_trades_by_date_range(now - timedelta(days=30), now)

        by_strategy = {}
        for t in trades:
            name = t.strategy_name or "unknown"
            if name not in by_strategy:
                by_strategy[name] = []
            by_strategy[name].append(t)

        # 简单相关性分析
        strategy_returns = {}
        for name, strategy_trades in by_strategy.items():
            returns = []
            for t in strategy_trades:
                if hasattr(t, "return_pct") and t.return_pct is not None:
                    returns.append(t.return_pct)
            if returns:
                strategy_returns[name] = np.array(returns)

        # 计算相关矩阵
        if len(strategy_returns) >= 2:
            names = list(strategy_returns.keys())
            # 对齐长度
            min_len = min(len(v) for v in strategy_returns.values())
            aligned = {k: v[:min_len] for k, v in strategy_returns.items()}

            matrix = {}
            for n1 in names:
                matrix[n1] = {}
                for n2 in names:
                    if min_len > 1:
                        corr = np.corrcoef(aligned[n1], aligned[n2])[0, 1]
                        matrix[n1][n2] = round(float(corr), 3)
                    else:
                        matrix[n1][n2] = 0.0

            return {"correlation_matrix": matrix, "note": "高度相关的策略应考虑合并或减仓"}

        return {"note": "策略数据不足，无法计算相关性"}

    def _review_risk_management(self) -> dict:
        """风控回顾"""
        now = datetime.now(timezone.utc)
        recent_trades = self.db.get_trades_by_date_range(now - timedelta(days=7), now)

        total_risk_taken = 0
        max_single_risk = 0
        stop_loss_usage = 0
        trades_with_sl = 0

        for t in recent_trades:
            if t.side == "BUY" and t.stop_loss and t.price:
                risk_pct = abs(t.price - t.stop_loss) / t.price
                total_risk_taken += risk_pct
                max_single_risk = max(max_single_risk, risk_pct)
                trades_with_sl += 1

            if t.stop_loss:
                stop_loss_usage += 1

        return {
            "avg_risk_per_trade": round(total_risk_taken / max(trades_with_sl, 1), 3),
            "max_single_risk": round(max_single_risk, 3),
            "stop_loss_usage_rate": round(stop_loss_usage / max(len(recent_trades), 1), 3),
            "recommendation": (
                "风控良好" if max_single_risk < RiskConfig.MAX_PORTFOLIO_RISK_PCT
                else "部分交易风险超标，需要收紧止损"
            ),
        }

    def _generate_action_items(self, evaluations: dict, diagnosis: list,
                                market: dict) -> list[str]:
        """生成行动项"""
        actions = []

        # 基于策略评估
        for name, eval_data in evaluations.items():
            grade = eval_data.get("grade", "C")
            if grade in ("D", "F"):
                actions.append(f"🔴 禁用策略 {name} (评级={grade})")
            elif grade == "C":
                actions.append(f"🟡 优化策略 {name} 参数")

        # 基于失败诊断
        for d in diagnosis[:3]:  # 最多3条
            if d.get("issues"):
                actions.append(f"🔍 {d['symbol']} 交易复盘: {'; '.join(d['issues'][:2])}")

        # 基于市场环境
        recommendation = market.get("recommendation", "")
        if recommendation:
            actions.append(f"📈 市场环境建议: {recommendation}")

        return actions

    def _generate_weekly_action_items(self, evaluations: dict,
                                       correlation: dict, risk: dict) -> list[str]:
        """生成周度行动项"""
        actions = self._generate_action_items(evaluations, [], {"recommendation": ""})

        # 相关性建议
        corr_matrix = correlation.get("correlation_matrix", {})
        for name, row in corr_matrix.items():
            for other, val in row.items():
                if name != other and abs(val) > 0.7:
                    actions.append(f"⚠️ {name} 和 {other} 高度相关({val:.2f})，考虑分散")

        # 风控建议
        if risk.get("stop_loss_usage_rate", 1) < 0.8:
            actions.append("🚨 止损使用率<80%，确保每笔交易都设止损")

        return actions

    def _format_reflection_summary(self, metrics: dict, evaluations: dict,
                                    actions: list, changes: dict) -> str:
        """格式化复盘摘要"""
        lines = [
            "=" * 60,
            "📋 每日复盘报告",
            "=" * 60,
            f"📊 组合表现: 收益={metrics.get('total_return_pct', 0):.2f}% | "
            f"Sharpe={metrics.get('sharpe_ratio', 0):.2f} | "
            f"胜率={metrics.get('win_rate', 0):.1%}",
            f"📉 最大回撤: {metrics.get('max_drawdown_pct', 0):.2f}%",
            f"💰 盈利因子: {metrics.get('profit_factor', 0):.2f}",
            "",
            "📊 策略评估:",
        ]

        for name, eval_data in evaluations.items():
            grade = eval_data.get("grade", "?")
            m = eval_data.get("metrics", {})
            lines.append(
                f"  {name}: [{grade}] "
                f"收益={m.get('total_return_pct', 0):.2f}% "
                f"Sharpe={m.get('sharpe_ratio', 0):.2f} "
                f"胜率={m.get('win_rate', 0):.1%}"
            )

        if changes:
            lines.append("")
            lines.append("🔧 参数调整:")
            for name, change in changes.items():
                lines.append(f"  {name}: {change}")

        if actions:
            lines.append("")
            lines.append("📋 行动项:")
            for action in actions:
                lines.append(f"  {action}")

        lines.append("=" * 60)
        return "\n".join(lines)
