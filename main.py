"""
量化交易系统 - 主入口
集成所有模块，提供完整的自动化交易能力

架构:
  数据层 -> 策略层 -> 风控层 -> 执行层 -> 复盘层
         ↑_________________________↓ (自反思反馈环)
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

# 项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import SystemConfig, RiskConfig
from data.db import DatabaseManager
from data.market_data import MarketDataFetcher
from data.news_fetcher import NewsSentimentFetcher
from strategy.strategy_manager import StrategyManager
from execution.trading212 import Trading212Client
from execution.order_manager import OrderManager
from risk.risk_manager import RiskManager
from reflection.performance import PerformanceAnalyzer
from reflection.strategy_review import StrategyReviewer
from reflection.backtester import Backtester
from scheduler import TradingScheduler


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

        # 执行层
        self.t212 = Trading212Client(environment="demo" if demo else "live")
        self.order_manager = OrderManager(self.t212, self.db)

        # 风控层
        self.risk_manager = RiskManager(self.db)

        # 复盘层
        self.performance_analyzer = PerformanceAnalyzer(self.db)
        self.strategy_reviewer = StrategyReviewer(self.db, self.strategy_manager)
        self.backtester = Backtester()

        # 状态
        self.is_running = False
        self.last_scan_time = None

        logger.info("✅ 系统初始化完成")

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
        account_value = self.t212.get_account_value()
        cash = self.t212.get_account_cash()
        logger.info(f"   账户: ${account_value:,.2f} | 现金: ${cash:,.2f}")

        # 2. 重置日度风控
        self.risk_manager.reset_daily(account_value)

        # 3. 更新历史数据
        for symbol in self.strategy_manager.stock_configs:
            self.market_data.fetch_historical(symbol, period="3mo", interval="1d")
            logger.info(f"   数据更新: {symbol}")

        # 4. 获取宏观事件
        macro_events = self.news_fetcher.fetch_macro_events()
        high_impact = [e for e in macro_events if e.get("impact") == "high"]
        if high_impact:
            logger.warning(f"   ⚠️ 今日重大宏观事件: {len(high_impact)}个")
            for event in high_impact:
                logger.warning(f"      {event.get('event_name')} ({event.get('country')})")

        # 5. 采集新闻
        self.collect_news()

        logger.info("✅ 盘前准备完成")

    def intraday_scan(self):
        """盘中策略扫描 - 核心交易决策"""
        if not self.is_running:
            return

        logger.info("🔍 盘中策略扫描...")

        # 1. 获取当前账户状态
        account_value = self.t212.get_account_value()
        current_positions = self.order_manager.get_active_positions_summary()

        # 2. 更新日内风控
        self.risk_manager.update_daily_pnl(account_value)
        if self.risk_manager.trading_paused:
            logger.warning("⛔ 交易暂停（回撤熔断），跳过扫描")
            return

        # 3. 获取行情数据
        market_data = {}
        for symbol in self.strategy_manager.stock_configs:
            df = self.market_data.fetch_historical(symbol, period="5d", interval="5m")
            if not df.empty:
                df = self.market_data.calculate_technical_indicators(df)
                market_data[symbol] = df

        # 4. 获取新闻情绪
        sentiments = {}
        for symbol in self.strategy_manager.stock_configs:
            sentiments[symbol] = self.news_fetcher.get_market_sentiment_summary(symbol)

        # 5. 生成信号
        signals = self.strategy_manager.get_all_signals(
            market_data=market_data,
            sentiments=sentiments,
        )

        # 6. 逐信号处理：风控 -> 下单
        for signal in signals:
            config = self.strategy_manager.stock_configs.get(signal.symbol)
            if not config:
                continue

            t212_ticker = config.get("ticker", f"{signal.symbol}_US_EQ")

            # 计算仓位
            atr = signal.indicators_snapshot.get("atr") if signal.indicators_snapshot else None
            quantity = self.strategy_manager.calculate_position(
                signal.symbol, account_value, signal.price, atr
            )

            if quantity <= 0:
                continue

            # 风控检查
            risk_result = self.risk_manager.check_signal(
                signal=signal,
                quantity=quantity,
                account_value=account_value,
                current_positions=current_positions,
                t212_ticker=t212_ticker,
            )

            if not risk_result.approved:
                logger.info(f"   ❌ 风控拦截: {signal.symbol} - {risk_result.reason}")
                continue

            # 应用风控调整
            signal.stop_loss = risk_result.adjusted_stop_loss or signal.stop_loss
            signal.take_profit = risk_result.adjusted_take_profit or signal.take_profit
            quantity = risk_result.adjusted_quantity

            # 执行下单
            order = self.order_manager.submit_signal(
                signal, t212_ticker, quantity, account_value
            )

            if order:
                # 记录信号
                self.db.insert_signal({
                    "symbol": signal.symbol,
                    "strategy_name": signal.strategy_name,
                    "signal_type": signal.signal_type.value,
                    "signal_strength": signal.signal_strength,
                    "price_at_signal": signal.price,
                    "indicators_snapshot": signal.indicators_snapshot,
                    "executed": True,
                    "trade_id": order.db_trade_id,
                })

        self.last_scan_time = datetime.now(timezone.utc)
        logger.info("✅ 盘中扫描完成")

    def post_market_routine(self):
        """盘后数据同步"""
        logger.info("🌆 盘后数据同步...")

        # 1. 同步持仓
        self.order_manager.sync_positions()

        # 2. 更新订单状态
        for key in list(self.order_manager.active_orders.keys()):
            self.order_manager.update_order_status(key)

        # 3. 更新公司基本面
        for symbol in self.strategy_manager.stock_configs:
            info = self.market_data.fetch_company_info(symbol)
            logger.debug(f"   基本面更新: {symbol}")

        logger.info("✅ 盘后同步完成")

    def monitor_positions(self):
        """持仓监控 - 检查止损止盈"""
        positions = self.order_manager.get_active_positions_summary()

        if not positions:
            return

        account_value = self.t212.get_account_value()

        for ticker, pos in positions.items():
            pnl_pct = pos.get("pnl_pct", 0)

            # 大额亏损预警
            if pnl_pct < -5:
                logger.warning(f"🚨 持仓亏损预警: {ticker} {pnl_pct:.1f}%")

            # 大额盈利提醒
            if pnl_pct > 10:
                logger.info(f"💰 持仓大幅盈利: {ticker} {pnl_pct:.1f}%")

        # 组合风险检查
        risk_report = self.risk_manager.check_portfolio_risk(account_value, positions)
        if risk_report["risk_level"] == "high":
            logger.warning(f"⚠️ 组合风险偏高: {risk_report}")

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
            logger.info("📋 每日复盘完成")
            return report
        except Exception as e:
            logger.error(f"每日复盘失败: {e}")
            return {}

    def weekly_reflection(self):
        """周度复盘"""
        try:
            report = self.strategy_reviewer.weekly_reflection()
            logger.info("📊 周度复盘完成")
            return report
        except Exception as e:
            logger.error(f"周度复盘失败: {e}")
            return {}

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
            # 测试所有配置的股票策略
            for sym, config in self.strategy_manager.stock_configs.items():
                s_name = config.get("strategy", "momentum_trend")
                params = config.get("params", {})
                strategy_map = {
                    "momentum_trend": MomentumTrendStrategy,
                    "mean_reversion": MeanReversionStrategy,
                }
                strategy_class = strategy_map.get(s_name, MomentumTrendStrategy)
                strategies_to_test.append((strategy_class(params), sym))

        results = {}
        for strategy, sym in strategies_to_test:
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

        return results

    # ============================================================
    # 系统控制
    # ============================================================

    def start(self):
        """启动系统"""
        self.is_running = True
        logger.info("🚀 量化交易系统启动")

        # 执行一次盘前准备
        self.pre_market_routine()

        # 启动调度器
        scheduler = TradingScheduler(self)
        scheduler.start()

    def stop(self):
        """停止系统"""
        self.is_running = False
        logger.info("🛑 量化交易系统停止")

    def get_status(self) -> dict:
        """获取系统状态"""
        account_value = 0
        try:
            account_value = self.t212.get_account_value()
        except Exception:
            pass

        return {
            "is_running": self.is_running,
            "mode": "demo" if self.demo else "live",
            "account_value": account_value,
            "watchlist_size": len(self.strategy_manager.stock_configs),
            "strategies": self.strategy_manager.get_strategy_status(),
            "last_scan": self.last_scan_time.isoformat() if self.last_scan_time else None,
        }


# ============================================================
# 命令行入口
# ============================================================

def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo", help="运行模式")
    parser.add_argument("--action", choices=["start", "backtest", "status", "scan", "reflect"],
                       default="start", help="执行动作")
    parser.add_argument("--symbol", type=str, help="指定股票")
    parser.add_argument("--strategy", type=str, help="指定策略")
    parser.add_argument("--period", type=str, default="1y", help="回测周期")

    args = parser.parse_args()

    system = QuantTradingSystem(demo=(args.mode == "demo"))

    if args.action == "start":
        system.start()

    elif args.action == "backtest":
        results = system.run_backtest(
            strategy_name=args.strategy,
            symbol=args.symbol,
            period=args.period,
        )
        for key, result in results.items():
            print(f"\n{'='*50}")
            print(f"策略: {result['strategy']} | 标的: {result['symbol']}")
            print(f"收益: {result['total_return_pct']:.2f}% | Sharpe: {result['sharpe_ratio']:.2f}")
            print(f"最大回撤: {result['max_drawdown_pct']:.2f}% | 胜率: {result['win_rate']:.1%}")
            print(f"盈利因子: {result['profit_factor']:.2f} | 交易数: {result['total_trades']}")

    elif args.action == "status":
        status = system.get_status()
        print(f"\n系统状态: {'运行中' if status['is_running'] else '已停止'}")
        print(f"模式: {status['mode']}")
        print(f"账户价值: ${status['account_value']:,.2f}")
        print(f"监控股票: {status['watchlist_size']}只")

    elif args.action == "scan":
        system.intraday_scan()

    elif args.action == "reflect":
        report = system.daily_reflection()
        print(f"\n复盘完成: 收益={report.get('portfolio_return', 0):.2f}%")


if __name__ == "__main__":
    main()
