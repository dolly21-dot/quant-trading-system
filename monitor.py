"""
系统监控面板 - 实时展示系统状态、持仓、策略表现
纯终端输出，无Web依赖，轻量快速
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger


class SystemMonitor:
    """系统监控面板"""

    def __init__(self):
        self.db = None
        self.strategy_manager = None
        self.risk_manager = None
        self.t212 = None

    def _init_modules(self):
        """延迟初始化（避免import时环境问题）"""
        if self.db is not None:
            return
        from data.db import DatabaseManager
        from strategy.strategy_manager import StrategyManager
        from risk.risk_manager import RiskManager
        from execution.trading212 import Trading212Client

        self.db = DatabaseManager()
        self.strategy_manager = StrategyManager()
        self.risk_manager = RiskManager(self.db)
        self.t212 = Trading212Client()

    def display_dashboard(self):
        """显示完整监控面板"""
        self._init_modules()
        self._clear_screen()
        self._print_header()
        self._print_account()
        self._print_positions()
        self._print_strategy_status()
        self._print_risk_status()
        self._print_recent_signals()
        self._print_latest_review()
        self._print_footer()

    # ============================================================
    # 各区块
    # ============================================================

    def _clear_screen(self):
        print("\033[2J\033[H", end="")

    def _print_header(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║            🏦 量化交易系统 - 监控面板                        ║
║            {now:<50}║
╚══════════════════════════════════════════════════════════════╝
""")

    def _print_account(self):
        """账户信息"""
        print("┌─ 💰 账户 ─────────────────────────────────────────────────┐")
        try:
            summary = self.t212.get_account_summary()
            if summary:
                total = summary.get("total", "N/A")
                free = summary.get("free", "N/A")
                invested = summary.get("invested", "N/A")
                pnl = summary.get("ppl", "N/A")
                print(f"│  总资产: ${total:>12}  │  可用: ${free:>12}            │")
                print(f"│  已投资: ${invested:>12}  │  盈亏: ${pnl:>12}            │")
            else:
                print(f"│  ⚠️ API未连接 (Demo模式/无API Key)                        │")
        except Exception:
            print(f"│  ⚠️ 账户信息获取失败                                      │")
        print("└────────────────────────────────────────────────────────────┘")

    def _print_positions(self):
        """持仓概览"""
        print("\n┌─ 📊 持仓 ─────────────────────────────────────────────────┐")
        try:
            positions = self.t212.get_open_positions()
            if positions:
                print(f"│  {'代码':<8} {'数量':>6} {'均价':>10} {'现价':>10} {'盈亏%':>8}    │")
                print(f"│  {'─'*8} {'─'*6} {'─'*10} {'─'*10} {'─'*8}    │")
                for pos in positions[:10]:
                    ticker = pos.get("ticker", "?")[:8]
                    qty = pos.get("quantity", 0)
                    avg = pos.get("averagePrice", 0)
                    cur = pos.get("currentPrice", 0)
                    pnl_pct = pos.get("pplPercentage", 0)
                    icon = "🟢" if pnl_pct >= 0 else "🔴"
                    print(f"│  {ticker:<8} {qty:>6} {avg:>10.2f} {cur:>10.2f} {icon}{pnl_pct:>7.2f}%│")
            else:
                print(f"│  📭 当前无持仓                                            │")
        except Exception:
            print(f"│  ⚠️ 持仓信息获取失败                                      │")
        print("└────────────────────────────────────────────────────────────┘")

    def _print_strategy_status(self):
        """策略状态"""
        print("\n┌─ 🧠 策略状态 ─────────────────────────────────────────────┐")
        try:
            status = self.strategy_manager.get_strategy_status()
            print(f"│  {'策略':<30} {'状态':>4} {'信号':>6} {'强度':>5}              │")
            print(f"│  {'─'*30} {'─'*4} {'─'*6} {'─'*5}              │")

            for key, data in list(status.items())[:12]:
                name = key[:30]
                enabled = "🟢" if data.get("enabled") else "🔴"
                last_sig = data.get("last_signal", {})
                sig_type = last_sig.get("type", "-") or "-"
                sig_str = last_sig.get("strength", "-") or "-"
                sig_str = f"{sig_str:.2f}" if isinstance(sig_str, float) else sig_str

                print(f"│  {name:<30} {enabled:>4} {sig_type:>6} {sig_str:>5}              │")

        except Exception as e:
            print(f"│  ⚠️ 策略信息获取失败: {e}                                │")
        print("└────────────────────────────────────────────────────────────┘")

    def _print_risk_status(self):
        """风控状态"""
        print("\n┌─ ⛔ 风控状态 ─────────────────────────────────────────────┐")
        try:
            from config.settings import RiskConfig
            paused = self.risk_manager.trading_paused
            status = "🚨 已熔断(暂停交易)" if paused else "✅ 正常"
            print(f"│  交易状态: {status:<40}│")
            print(f"│  单股上限: {RiskConfig.MAX_POSITION_PCT:.0%}   "
                  f"单笔风险: {RiskConfig.MAX_PORTFOLIO_RISK_PCT:.0%}   "
                  f"日回撤: {RiskConfig.MAX_DAILY_DRAWDOWN_PCT:.0%}      │")
            print(f"│  杠杆: 🚫 禁止                                          │")
        except Exception:
            print(f"│  ⚠️ 风控信息获取失败                                      │")
        print("└────────────────────────────────────────────────────────────┘")

    def _print_recent_signals(self):
        """最近信号"""
        print("\n┌─ 📡 最近信号 ─────────────────────────────────────────────┐")
        try:
            session = self.db.get_session()
            from data.db import StrategySignal as SignalModel
            recent = session.query(SignalModel).order_by(
                SignalModel.created_at.desc()
            ).limit(5).all()

            if recent:
                for sig in recent:
                    icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(sig.signal_type, "❓")
                    ts = sig.created_at.strftime("%m/%d %H:%M") if sig.created_at else "?"
                    print(f"│  {icon} {ts} {sig.symbol:<6} {sig.strategy_name:<18} "
                          f"{sig.signal_type:<4} str={sig.signal_strength:.2f}   │")
            else:
                print(f"│  暂无信号记录                                             │")
            session.close()
        except Exception:
            print(f"│  暂无信号记录                                             │")
        print("└────────────────────────────────────────────────────────────┘")

    def _print_latest_review(self):
        """最新复盘"""
        print("\n┌─ 📋 最新复盘 ─────────────────────────────────────────────┐")
        try:
            report = self.db.get_latest_reflection("daily")
            if report:
                date = report.report_date.strftime("%Y-%m-%d") if report.report_date else "?"
                print(f"│  日期: {date:<44}│")
                print(f"│  收益: {report.portfolio_return:>8.2f}%  "
                      f"回撤: {report.portfolio_drawdown:>8.2f}%  "
                      f"Sharpe: {report.sharpe_ratio:>6.3f}      │")
                print(f"│  胜率: {report.win_rate:>8.1%}  "
                      f"盈亏比: {report.profit_factor:>8.3f}  "
                      f"交易数: {report.total_trades:>4}        │")
            else:
                print(f"│  暂无复盘记录                                             │")
        except Exception:
            print(f"│  暂无复盘记录                                             │")
        print("└────────────────────────────────────────────────────────────┘")

    def _print_footer(self):
        print(f"""
┌─ 操作 ─────────────────────────────────────────────────────┐
│  [R] 刷新  [B] 回测  [S] 手动扫描  [Q] 退出                │
└────────────────────────────────────────────────────────────┘
""")

    # ============================================================
    # 交互模式
    # ============================================================

    def interactive_loop(self, refresh_interval: int = 30):
        """交互式监控循环"""
        import time

        print("🖥️ 监控面板启动 (按Ctrl+C退出)\n")

        while True:
            try:
                self.display_dashboard()
                print("⏳ 下次刷新: 30秒后 (或按Enter立即刷新)")

                # 非阻塞等待
                import select
                start = time.time()
                while time.time() - start < refresh_interval:
                    if select.select([sys.stdin], [], [], 1.0)[0]:
                        cmd = sys.stdin.readline().strip().upper()
                        if cmd == "Q":
                            print("👋 退出监控")
                            return
                        elif cmd == "R":
                            break
                        elif cmd == "B":
                            print("📊 运行快速回测...")
                            self._quick_backtest()
                            break
                        elif cmd == "S":
                            print("🔍 运行手动扫描...")
                            break
                        else:
                            break
                    time.sleep(0.5)

            except KeyboardInterrupt:
                print("\n👋 退出监控")
                return

    def _quick_backtest(self):
        """快速回测"""
        try:
            from strategy.momentum_trend import MomentumTrendStrategy
            from reflection.backtester import Backtester
            from data.market_data import MarketDataFetcher
            import numpy as np

            # 使用模拟数据快速回测
            np.random.seed(42)
            n = 300
            import pandas as pd
            from datetime import timedelta
            dates = [datetime(2025,1,1) + timedelta(days=i) for i in range(n)]
            close = 180 + np.cumsum(np.random.randn(n) * 0.5)

            df = pd.DataFrame({
                'timestamp': dates, 'open': close, 'high': close + 1,
                'low': close - 1, 'close': close,
                'volume': np.random.randint(5e6, 5e7, n).astype(float),
                'symbol': 'AAPL', 'interval': '1d'
            })
            mdf = MarketDataFetcher()
            df = mdf.calculate_technical_indicators(df)

            bt = Backtester(initial_cash=100000)
            mt = MomentumTrendStrategy()
            result = bt.run_backtest(mt, 'AAPL', data=df)

            print(f"\n  回测结果: 收益={result.total_return_pct:.2f}% "
                  f"Sharpe={result.sharpe_ratio:.2f} "
                  f"胜率={result.win_rate:.1%} "
                  f"交易={result.total_trades}笔\n")
        except Exception as e:
            print(f"  回测失败: {e}")


if __name__ == "__main__":
    monitor = SystemMonitor()
    monitor.interactive_loop()
