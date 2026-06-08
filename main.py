"""
量化交易系统 - 主入口
集成所有模块，提供完整的自动化交易能力

架构:
  数据层 -> 策略层 -> 仲裁层 -> 风控层 -> 执行层 -> 复盘层
         ↑___________________________________↓ (自反思反馈环)
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

# 项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import SystemConfig, RiskConfig
from data.db import DatabaseManager
from data.market_data import MarketDataFetcher
from data.news_fetcher import NewsSentimentFetcher
from strategy.strategy_manager import StrategyManager
from strategy.signal_arbitrator import SignalArbitrator, MarketRegime, ArbitratedSignal
from strategy.base import StrategySignal, SignalType
from execution.trading212 import Trading212Client
from execution.order_manager import OrderManager
from risk.risk_manager import RiskManager
from reflection.performance import PerformanceAnalyzer
from reflection.strategy_review import StrategyReviewer
from reflection.backtester import Backtester
from notification.notifier import (
    NotificationManager, NotificationMessage, NotificationLevel, NotificationType
)
from scheduler import TradingScheduler

# 别名 - 保持代码简洁
Notifier = NotificationManager


class QuantTradingSystem:
    """量化交易系统 - 主控类"""

    def __init__(self, demo: bool = True):
        """
        Args:
            demo: 是否使用模拟环境（默认True，安全第一）
        """
        self.demo = demo
        self._setup_logging()

        # === 初始化各模块 ===
        logger.info("=" * 60)
        logger.info("🏗️  量化交易系统初始化...")
        logger.info(f"   模式: {'模拟(DEMO)' if demo else '实盘(LIVE)'}")
        logger.info(f"   杠杆: 禁止")
        logger.info("=" * 60)

        # 数据层
        self.db = DatabaseManager()
        self.market_data = MarketDataFetcher(self.db)
        self.news_fetcher = NewsSentimentFetcher(self.db)

        # 策略层
        self.strategy_manager = StrategyManager()

        # 仲裁层
        self.arbitrator = SignalArbitrator(
            strategies=self.strategy_manager.strategies
        )

        # 执行层
        self.t212 = Trading212Client(environment="demo" if demo else "live")
        self.order_manager = OrderManager(self.t212, self.db)

        # 风控层
        self.risk_manager = RiskManager(self.db)

        # 复盘层
        self.performance_analyzer = PerformanceAnalyzer(self.db)
        self.strategy_reviewer = StrategyReviewer(self.db, self.strategy_manager)
        self.backtester = Backtester()

        # 通知
        self.notifier = NotificationManager()

        # 状态
        self.is_running = False
        self.last_scan_time = None
        self.start_time = None
        self.scan_count = 0
        self.trade_count_today = 0

        logger.info("✅ 系统初始化完成 (含信号仲裁器 + 通知系统)")

    def _setup_logging(self):
        """配置日志"""
        logger.remove()
        logger.add(
            sys.stderr,
            level=SystemConfig.LOG_LEVEL,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        )
        logger.add(
            SystemConfig.LOG_FILE,
            level="DEBUG",
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8",
        )

    # ============================================================
    # 核心交易流程
    # ============================================================

    def pre_market_routine(self):
        """盘前准备"""
        logger.info("🌅 盘前准备开始...")

        # 1. 同步账户信息
        account_value = self._get_account_value()
        cash = self._get_account_cash()
        logger.info(f"   账户: ${account_value:,.2f} | 现金: ${cash:,.2f}")

        # 2. 重置日度风控
        self.risk_manager.reset_daily(account_value)
        self.trade_count_today = 0

        # 3. 更新历史数据
        for symbol in self.strategy_manager.stock_configs:
            try:
                self.market_data.fetch_historical(symbol, period="3mo", interval="1d")
                logger.info(f"   数据更新: {symbol}")
            except Exception as e:
                logger.warning(f"   数据更新失败 {symbol}: {e}")

        # 4. 判断市场环境 → 设置仲裁器环境权重
        market_regime = self._detect_market_regime()
        self.arbitrator.set_market_regime(market_regime)

        # 5. 获取宏观事件
        try:
            macro_events = self.news_fetcher.fetch_macro_events()
            high_impact = [e for e in macro_events if e.get("impact") == "high"]
            if high_impact:
                logger.warning(f"   ⚠️ 今日重大宏观事件: {len(high_impact)}个")
                for event in high_impact:
                    logger.warning(f"      {event.get('event_name')} ({event.get('country')})")
                self.notifier.notify(
                    title="重大宏观事件提醒",
                    body=f"今日{len(high_impact)}个高影响力事件: " +
                         ", ".join(e.get("event_name", "") for e in high_impact[:5]),
                    level=NotificationLevel.WARNING,
                    notification_type=NotificationType.RISK_ALERT,
                )
        except Exception as e:
            logger.warning(f"宏观事件获取失败: {e}")

        # 6. 采集新闻
        self.collect_news()

        # 7. 盘前通知
        self.notifier.notify(
            title="盘前准备完成",
            body=f"账户${account_value:,.2f} | 环境={market_regime.value} | "
                 f"监控{len(self.strategy_manager.stock_configs)}只股票",
            level=NotificationLevel.INFO,
            notification_type=NotificationType.SYSTEM_STATUS,
        )

        logger.info("✅ 盘前准备完成")

    def intraday_scan(self):
        """盘中策略扫描 - 核心交易决策（含信号仲裁）"""
        if not self.is_running:
            return

        self.scan_count += 1
        logger.info(f"🔍 盘中策略扫描 #{self.scan_count}...")

        # 1. 获取当前账户状态
        account_value = self._get_account_value()
        try:
            current_positions = self.order_manager.get_active_positions_summary()
        except Exception:
            current_positions = {}

        # 2. 更新日内风控
        try:
            self.risk_manager.update_daily_pnl(account_value)
        except Exception:
            pass
        if getattr(self.risk_manager, 'trading_paused', False):
            logger.warning("⛔ 交易暂停（回撤熔断），跳过扫描")
            return

        # 3. 获取行情数据 + 计算指标
        market_data = {}
        for symbol in self.strategy_manager.stock_configs:
            try:
                df = self.market_data.fetch_historical(symbol, period="5d", interval="5m")
                if df is not None and not df.empty:
                    df = self.market_data.calculate_technical_indicators(df)
                    market_data[symbol] = df
            except Exception:
                # 5分钟数据可能不可用，回退到日线
                try:
                    df = self.market_data.fetch_historical(symbol, period="3mo", interval="1d")
                    if df is not None and not df.empty:
                        df = self.market_data.calculate_technical_indicators(df)
                        market_data[symbol] = df
                except Exception as e:
                    logger.warning(f"   行情获取失败 {symbol}: {e}")

        # 4. 获取新闻情绪
        sentiments = {}
        for symbol in self.strategy_manager.stock_configs:
            try:
                sentiments[symbol] = self.news_fetcher.get_market_sentiment_summary(symbol)
            except Exception:
                sentiments[symbol] = {}

        # 5. 生成原始信号（每只股票可能产生多个策略信号）
        raw_signals_by_symbol: dict[str, list[StrategySignal]] = {}
        for symbol in self.strategy_manager.stock_configs:
            config = self.strategy_manager.stock_configs.get(symbol)
            if not config:
                continue

            data = market_data.get(symbol)
            if data is None or (hasattr(data, 'empty') and data.empty):
                continue

            # 用配置的策略生成信号
            try:
                signal = self.strategy_manager.generate_signal(
                    symbol, data,
                    sentiment=sentiments.get(symbol, {}),
                    macro_events=[] if self.scan_count > 1 else [],
                )
                if signal and signal.signal_type != SignalType.HOLD:
                    raw_signals_by_symbol.setdefault(symbol, []).append(signal)
            except Exception as e:
                logger.warning(f"   信号生成失败 {symbol}: {e}")

        if not raw_signals_by_symbol:
            logger.info("   无有效交易信号")
            return

        # 6. 信号仲裁 - 解决同股票多策略冲突
        arbitrated_signals = []
        for symbol, signals in raw_signals_by_symbol.items():
            # 检查持仓状态
            config = self.strategy_manager.stock_configs.get(symbol, {})
            t212_ticker = config.get("ticker", f"{symbol}_US_EQ")
            has_position = False
            position_pnl_pct = 0.0
            if t212_ticker in current_positions:
                has_position = True
                position_pnl_pct = current_positions[t212_ticker].get("pnl_pct", 0.0)

            # 仲裁
            try:
                arbitrated = self.arbitrator.arbitrate(
                    signals=signals,
                    has_position=has_position,
                    position_pnl_pct=position_pnl_pct,
                )

                if arbitrated and arbitrated.signal_type != SignalType.HOLD:
                    arbitrated_signals.append(arbitrated)
                    logger.info(
                        f"  ⚖️ 仲裁: {symbol} → {arbitrated.signal_type.value} "
                        f"强度={arbitrated.signal_strength:.3f} "
                        f"置信度={arbitrated.confidence:.3f} | "
                        f"投票={arbitrated.strategy_votes}"
                    )
            except Exception as e:
                logger.warning(f"   仲裁失败 {symbol}: {e}")

        # 按信号强度排序
        arbitrated_signals.sort(key=lambda s: s.signal_strength, reverse=True)

        # 7. 逐信号处理：风控 -> 下单
        executed_count = 0
        for arb_signal in arbitrated_signals:
            config = self.strategy_manager.stock_configs.get(arb_signal.symbol)
            if not config:
                continue

            t212_ticker = config.get("ticker", f"{arb_signal.symbol}_US_EQ")

            # 将仲裁信号转为标准策略信号（兼容下游接口）
            signal = StrategySignal(
                symbol=arb_signal.symbol,
                strategy_name="arbitrated",
                signal_type=arb_signal.signal_type,
                signal_strength=arb_signal.signal_strength,
                price=arb_signal.price,
                stop_loss=arb_signal.stop_loss,
                take_profit=arb_signal.take_profit,
                reason=arb_signal.reason,
            )

            # 计算仓位
            atr = None  # TODO: 从指标快照获取
            quantity = self.strategy_manager.calculate_position(
                arb_signal.symbol, account_value, arb_signal.price, atr
            )

            if quantity <= 0:
                continue

            # 风控检查
            try:
                risk_result = self.risk_manager.check_signal(
                    signal=signal,
                    quantity=quantity,
                    account_value=account_value,
                    current_positions=current_positions,
                    t212_ticker=t212_ticker,
                )
            except Exception as e:
                logger.warning(f"   风控检查失败 {arb_signal.symbol}: {e}")
                continue

            if not risk_result.approved:
                logger.info(f"   ❌ 风控拦截: {arb_signal.symbol} - {risk_result.reason}")
                continue

            # 应用风控调整
            signal.stop_loss = risk_result.adjusted_stop_loss or signal.stop_loss
            signal.take_profit = risk_result.adjusted_take_profit or signal.take_profit
            quantity = risk_result.adjusted_quantity

            # 执行下单
            try:
                order = self.order_manager.submit_signal(
                    signal, t212_ticker, quantity, account_value
                )

                if order:
                    executed_count += 1
                    self.trade_count_today += 1

                    # 记录信号
                    try:
                        self.db.insert_signal({
                            "symbol": signal.symbol,
                            "strategy_name": "arbitrated",
                            "signal_type": signal.signal_type.value,
                            "signal_strength": signal.signal_strength,
                            "price_at_signal": signal.price,
                            "indicators_snapshot": {
                                "strategy_votes": arb_signal.strategy_votes,
                                "confidence": arb_signal.confidence,
                            },
                            "executed": True,
                        })
                    except Exception:
                        pass

                    # 交易通知
                    self.notifier.notify(
                        title=f"交易执行: {signal.signal_type.value} {signal.symbol}",
                        body=f"数量={quantity} | 价格={signal.price:.2f} | "
                             f"SL={signal.stop_loss} | TP={signal.take_profit}\n"
                             f"仲裁投票: {arb_signal.strategy_votes}\n"
                             f"原因: {arb_signal.reason[:100]}",
                        level=NotificationLevel.INFO,
                        notification_type=NotificationType.TRADE_SIGNAL,
                    )
            except Exception as e:
                logger.error(f"   下单失败 {arb_signal.symbol}: {e}")

        self.last_scan_time = datetime.now(timezone.utc)
        logger.info(f"✅ 盘中扫描完成: {len(arbitrated_signals)}个仲裁信号, {executed_count}笔成交")

    def post_market_routine(self):
        """盘后数据同步"""
        logger.info("🌆 盘后数据同步...")

        # 1. 同步持仓
        try:
            self.order_manager.sync_positions()
        except Exception as e:
            logger.warning(f"持仓同步失败: {e}")

        # 2. 更新订单状态
        for key in list(self.order_manager.active_orders.keys()):
            try:
                self.order_manager.update_order_status(key)
            except Exception:
                pass

        # 3. 更新公司基本面
        for symbol in self.strategy_manager.stock_configs:
            try:
                self.market_data.fetch_company_info(symbol)
            except Exception:
                pass

        # 4. 盘后通知
        self.notifier.notify(
            title="盘后同步完成",
            body=f"今日交易: {self.trade_count_today}笔 | 扫描: {self.scan_count}次",
            level=NotificationLevel.INFO,
            notification_type=NotificationType.SYSTEM_STATUS,
        )

        logger.info("✅ 盘后同步完成")

    def monitor_positions(self):
        """持仓监控 - 检查止损止盈"""
        try:
            positions = self.order_manager.get_active_positions_summary()
        except Exception:
            return

        if not positions:
            return

        account_value = self._get_account_value()

        for ticker, pos in positions.items():
            pnl_pct = pos.get("pnl_pct", 0)

            # 大额亏损预警
            if pnl_pct < -5:
                logger.warning(f"🚨 持仓亏损预警: {ticker} {pnl_pct:.1f}%")
                self.notifier.notify(
                    title=f"⚠️ 亏损预警: {ticker}",
                    body=f"当前亏损 {pnl_pct:.1f}%，请关注止损位",
                    level=NotificationLevel.WARNING,
                    notification_type=NotificationType.RISK_ALERT,
                )

            # 大额盈利提醒
            if pnl_pct > 10:
                logger.info(f"💰 持仓大幅盈利: {ticker} {pnl_pct:.1f}%")

        # 组合风险检查
        try:
            risk_report = self.risk_manager.check_portfolio_risk(account_value, positions)
            if risk_report.get("risk_level") == "high":
                logger.warning(f"⚠️ 组合风险偏高: {risk_report}")
                self.notifier.notify(
                    title="组合风险偏高",
                    body=f"敞口={risk_report.get('total_exposure_pct', 0):.1%} | "
                         f"集中度={risk_report.get('concentration_risk', 0):.1%}",
                    level=NotificationLevel.WARNING,
                    notification_type=NotificationType.RISK_ALERT,
                )
        except Exception:
            pass

    def collect_news(self):
        """采集新闻"""
        for symbol in self.strategy_manager.stock_configs:
            try:
                self.news_fetcher.collect_and_store_news(symbol)
            except Exception as e:
                logger.warning(f"新闻采集失败 {symbol}: {e}")

    def daily_reflection(self):
        """每日复盘"""
        try:
            report = self.strategy_reviewer.daily_reflection()

            # 复盘通知
            self.notifier.notify(
                title="📋 每日复盘完成",
                body=(
                    f"收益: {report.get('portfolio_return', 0):.2f}% | "
                    f"Sharpe: {report.get('sharpe_ratio', 0):.2f} | "
                    f"胜率: {report.get('win_rate', 0):.1%} | "
                    f"交易: {report.get('total_trades', 0)}笔\n"
                    f"行动项: {report.get('action_items', [])}"
                ),
                level=NotificationLevel.INFO,
                notification_type=NotificationType.DAILY_REVIEW,
            )

            logger.info("📋 每日复盘完成")
            return report
        except Exception as e:
            logger.error(f"每日复盘失败: {e}")
            self.notifier.notify(
                title="❌ 每日复盘失败",
                body=str(e),
                level=NotificationLevel.CRITICAL,
                notification_type=NotificationType.ERROR,
            )
            return {}

    def weekly_reflection(self):
        """周度复盘"""
        try:
            report = self.strategy_reviewer.weekly_reflection()

            self.notifier.notify(
                title="📈 周度复盘完成",
                body=f"收益: {report.get('portfolio_return', 0):.2f}% | "
                     f"Sharpe: {report.get('sharpe_ratio', 0):.2f}",
                level=NotificationLevel.INFO,
                notification_type=NotificationType.WEEKLY_REVIEW,
            )

            logger.info("📊 周度复盘完成")
            return report
        except Exception as e:
            logger.error(f"周度复盘失败: {e}")
            return {}

    # ============================================================
    # 市场环境检测
    # ============================================================

    def _detect_market_regime(self) -> MarketRegime:
        """检测市场环境"""
        try:
            spy_data = self.market_data.fetch_historical("SPY", period="6mo", interval="1d")
            if spy_data is None or (hasattr(spy_data, 'empty') and spy_data.empty) or len(spy_data) < 50:
                return MarketRegime.UNKNOWN

            close = spy_data["close"]
            current = close.iloc[-1]
            sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else close.mean()

            if len(close) >= 200:
                sma_200 = close.rolling(200).mean().iloc[-1]
            else:
                sma_200 = close.mean()

            if current > sma_50 > sma_200:
                regime = MarketRegime.BULL
            elif current < sma_50 < sma_200:
                regime = MarketRegime.BEAR
            else:
                regime = MarketRegime.SIDEWAYS

            logger.info(f"   市场环境: {regime.value} (SPY={current:.2f}, SMA50={sma_50:.2f})")
            return regime

        except Exception as e:
            logger.warning(f"市场环境检测失败: {e}")
            return MarketRegime.UNKNOWN

    # ============================================================
    # 回测
    # ============================================================

    def run_backtest(self, strategy_name: str = None, symbol: str = None,
                     period: str = "1y") -> dict:
        """运行回测"""
        from strategy.momentum_trend import MomentumTrendStrategy
        from strategy.mean_reversion import MeanReversionStrategy

        strategies_to_test = []

        if strategy_name and symbol:
            config = self.strategy_manager.stock_configs.get(symbol, {})
            params = config.get("params", {})
            strategy_map = {
                "momentum_trend": MomentumTrendStrategy,
                "mean_reversion": MeanReversionStrategy,
            }
            strategy_class = strategy_map.get(strategy_name, MomentumTrendStrategy)
            strategies_to_test.append((strategy_class(params), symbol))
        else:
            strategy_map = {
                "momentum_trend": MomentumTrendStrategy,
                "mean_reversion": MeanReversionStrategy,
            }
            for sym, config in self.strategy_manager.stock_configs.items():
                s_name = config.get("strategy", "momentum_trend")
                params = config.get("params", {})
                strategy_class = strategy_map.get(s_name, MomentumTrendStrategy)
                strategies_to_test.append((strategy_class(params), sym))

        results = {}
        for strategy, sym in strategies_to_test:
            try:
                result = self.backtester.run_backtest(strategy, sym, period=period)
                results[f"{sym}_{strategy.name}"] = {
                    "symbol": sym,
                    "strategy": strategy.name,
                    "total_return_pct": result.total_return_pct,
                    "sharpe_ratio": result.sharpe_ratio,
                    "max_drawdown_pct": result.max_drawdown_pct,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "total_trades": result.total_trades,
                }
            except Exception as e:
                logger.warning(f"回测失败 {sym}_{strategy.name}: {e}")
                results[f"{sym}_{strategy.name}"] = {"error": str(e)}

        return results

    # ============================================================
    # 辅助方法
    # ============================================================

    def _get_account_value(self) -> float:
        """获取账户总值（容错）"""
        try:
            val = self.t212.get_account_value()
            if val and isinstance(val, (int, float)):
                return float(val)
        except Exception:
            pass
        return 100000.0  # 默认值

    def _get_account_cash(self) -> float:
        """获取账户现金（容错）"""
        try:
            cash = self.t212.get_account_cash()
            if cash and isinstance(cash, dict):
                return float(cash.get("free", 100000))
            elif isinstance(cash, (int, float)):
                return float(cash)
        except Exception:
            pass
        return 100000.0

    # ============================================================
    # 系统控制
    # ============================================================

    def start(self):
        """启动系统"""
        self.is_running = True
        self.start_time = datetime.now(timezone.utc)
        logger.info("🚀 量化交易系统启动")

        # 启动通知
        self.notifier.notify(
            title="🚀 量化交易系统已启动",
            body=f"模式={'DEMO' if self.demo else 'LIVE'} | "
                 f"监控{len(self.strategy_manager.stock_configs)}只股票 | "
                 f"杠杆=禁止",
            level=NotificationLevel.INFO,
            notification_type=NotificationType.SYSTEM_STATUS,
        )

        # 执行一次盘前准备
        self.pre_market_routine()

        # 启动调度器
        scheduler = TradingScheduler(self)
        scheduler.start()

    def stop(self):
        """停止系统"""
        self.is_running = False
        logger.info("🛑 量化交易系统停止")

        self.notifier.notify(
            title="🛑 量化交易系统已停止",
            body=f"运行时长: {(datetime.now(timezone.utc) - self.start_time) if self.start_time else 'N/A'}",
            level=NotificationLevel.WARNING,
            notification_type=NotificationType.SYSTEM_STATUS,
        )

    def get_status(self) -> dict:
        """获取系统状态"""
        account_value = self._get_account_value()

        uptime = None
        if self.start_time:
            uptime = str(datetime.now(timezone.utc) - self.start_time).split(".")[0]

        return {
            "is_running": self.is_running,
            "mode": "demo" if self.demo else "live",
            "uptime": uptime,
            "account_value": account_value,
            "watchlist_size": len(self.strategy_manager.stock_configs),
            "strategies": self.strategy_manager.get_strategy_status(),
            "market_regime": self.arbitrator.market_regime.value if hasattr(self.arbitrator, 'market_regime') else "unknown",
            "trading_paused": getattr(self.risk_manager, 'trading_paused', False),
            "scan_count": self.scan_count,
            "trade_count_today": self.trade_count_today,
            "last_scan": self.last_scan_time.isoformat() if self.last_scan_time else None,
        }

    def display_dashboard(self):
        """显示监控面板"""
        from monitor import SystemMonitor
        monitor = SystemMonitor()
        # 共享已有实例
        monitor.db = self.db
        monitor.strategy_manager = self.strategy_manager
        monitor.risk_manager = self.risk_manager
        monitor.t212 = self.t212
        monitor.display_dashboard()


# ============================================================
# 命令行入口
# ============================================================

def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo", help="运行模式")
    parser.add_argument("--action",
                       choices=["start", "backtest", "status", "scan", "reflect",
                                "monitor", "check", "daily"],
                       default="start", help="执行动作")
    parser.add_argument("--symbol", type=str, help="指定股票")
    parser.add_argument("--strategy", type=str, help="指定策略")
    parser.add_argument("--period", type=str, default="1y", help="回测周期")

    args = parser.parse_args()

    # === 健康检查 ===
    if args.action == "check":
        from health_check import HealthChecker
        checker = HealthChecker()
        healthy = checker.run_all()
        sys.exit(0 if healthy else 1)

    # === 每日分析报告 ===
    if args.action == "daily":
        try:
            from daily_analysis import run_daily_analysis
            run_daily_analysis()
        except ImportError:
            logger.error("daily_analysis 模块未找到")
        return

    system = QuantTradingSystem(demo=(args.mode == "demo"))

    if args.action == "start":
        system.start()

    elif args.action == "backtest":
        results = system.run_backtest(
            strategy_name=args.strategy,
            symbol=args.symbol,
            period=args.period,
        )
        print(f"\n{'='*60}")
        print("📊 回测结果汇总")
        print(f"{'='*60}")
        for key, result in results.items():
            if "error" in result:
                print(f"  ❌ {key}: {result['error']}")
            else:
                print(f"  ✅ {key}: 收益={result['total_return_pct']:.2f}% | "
                      f"Sharpe={result['sharpe_ratio']:.2f} | "
                      f"回撤={result['max_drawdown_pct']:.2f}% | "
                      f"胜率={result['win_rate']:.1%} | "
                      f"交易数={result['total_trades']}")
        print(f"{'='*60}")

    elif args.action == "status":
        status = system.get_status()
        print(f"\n系统状态: {'运行中' if status['is_running'] else '已停止'}")
        print(f"模式: {status['mode']}")
        print(f"账户价值: ${status['account_value']:,.2f}")
        print(f"监控股票: {status['watchlist_size']}只")
        print(f"市场环境: {status['market_regime']}")
        print(f"交易暂停: {status['trading_paused']}")

    elif args.action == "scan":
        system.intraday_scan()

    elif args.action == "reflect":
        report = system.daily_reflection()
        print(f"\n复盘完成: 收益={report.get('portfolio_return', 0):.2f}%")

    elif args.action == "monitor":
        system.display_dashboard()


if __name__ == "__main__":
    main()
