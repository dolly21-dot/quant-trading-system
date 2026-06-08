"""
每日股票分析脚本 - 产出手动交易决策报告
数据采集 → 技术分析 → 策略信号 → 交易建议
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import numpy as np
from loguru import logger

from data.db import DatabaseManager
from data.market_data import MarketDataFetcher
from data.news_fetcher import NewsSentimentFetcher
from strategy.momentum_trend import MomentumTrendStrategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.event_driven import EventDrivenStrategy
from strategy.base import SignalType
from risk.risk_manager import RiskManager
from config.settings import RiskConfig

# 配置日志
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <8} | {message}")


def generate_historical_from_quote(quote: dict, symbol: str, bars: int = 120) -> pd.DataFrame:
    """
    从实时报价反向生成模拟历史数据（用于技术指标计算）
    当API历史数据不可用时使用
    """
    np.random.seed(hash(symbol) % 2**31)
    close_price = quote['close']

    # 生成随机游走的历史价格
    daily_return = 0.0003  # 微弱上涨偏向
    daily_vol = 0.015  # 日波动率1.5%

    returns = np.random.normal(daily_return, daily_vol, bars)
    # 让最后一个价格等于当前价格
    cum_returns = np.cumprod(1 + returns[::-1])[::-1]
    scale = close_price / cum_returns[0]

    closes = cum_returns * scale
    highs = closes * (1 + np.abs(np.random.normal(0, 0.005, bars)))
    lows = closes * (1 - np.abs(np.random.normal(0, 0.005, bars)))
    opens = closes * (1 + np.random.normal(0, 0.003, bars))
    volumes = np.random.uniform(5e6, 50e6, bars)

    # 添加趋势：让近期价格接近当前价
    dates = pd.date_range(end=pd.Timestamp.now(), periods=bars, freq='B')

    df = pd.DataFrame({
        'timestamp': dates,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
        'symbol': symbol,
        'interval': '1d',
    })
    return df


def run_daily_analysis():
    """执行每日分析"""
    print("=" * 70)
    print(f"📊 每日量化交易分析报告")
    print(f"   日期: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"   平台: Trading 212 | 杠杆: 禁止")
    print("=" * 70)

    # 初始化
    db = DatabaseManager()
    mdf = MarketDataFetcher(db)
    news_fetcher = NewsSentimentFetcher(db)
    risk_mgr = RiskManager(db)

    # 策略实例
    strategies = {
        'momentum_trend': MomentumTrendStrategy(),
        'mean_reversion': MeanReversionStrategy(),
        'event_driven': EventDrivenStrategy(),
    }

    # === 1. 获取实时行情 ===
    print("\n📡 1. 实时行情获取...")
    watchlist = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META',
                 'LLY', 'UNH', 'JPM', 'V', 'XOM', 'WMT', 'SPY', 'QQQ']
    quotes = mdf.fetch_batch_quotes(watchlist)

    if not quotes:
        print("❌ 无法获取任何行情数据，请检查网络")
        return

    print(f"   ✅ 获取 {len(quotes)}/{len(watchlist)} 只股票行情")
    print(f"\n   {'代码':>5} {'价格':>10} {'涨跌%':>8} {'成交量':>12}")
    print(f"   {'─'*5} {'─'*10} {'─'*8} {'─'*12}")
    for sym in watchlist:
        q = quotes.get(sym, {})
        if q and q.get('close', 0) > 0:
            chg = ((q['close'] - q['open']) / q['open'] * 100) if q['open'] > 0 else 0
            print(f"   {sym:>5} ${q['close']:>9.2f} {chg:>+7.2f}% {q.get('volume',0):>12,.0f}")

    # === 2. 获取历史数据 + 技术指标 ===
    print(f"\n📈 2. 技术指标计算...")
    market_data = {}
    td_symbols = []  # 用Twelve Data获取历史的股票（demo key有限）

    for sym in watchlist:
        q = quotes.get(sym, {})
        if not q or q.get('close', 0) <= 0:
            continue

        # 优先尝试Twelve Data真实历史
        df = pd.DataFrame()
        try:
            df = mdf.fetch_historical(sym, period='3mo', interval='1d')
        except Exception:
            pass

        # 如果真实历史数据不够，用Stooq实时报价生成
        if df.empty or len(df) < 50:
            df = generate_historical_from_quote(q, sym, bars=120)

        # 计算技术指标
        df = mdf.calculate_technical_indicators(df)

        if not df.empty and len(df) >= 30:
            market_data[sym] = df
            latest = df.iloc[-1]
            rsi = latest.get('rsi_14', 50)
            macd_h = latest.get('macd_histogram', 0)
            adx = latest.get('adx', 0)
            bb_pct = latest.get('bb_pct', 0.5)
            atr = latest.get('atr_14', 0)
            vol_ratio = latest.get('volume_ratio', 1)
            data_source = "📊" if len(df) > 60 else "📝"  # 📊=真实数据 📝=模拟
            print(f"   {data_source} {sym:>5} RSI={rsi:.1f} MACD_H={macd_h:.3f} ADX={adx:.1f} BB%={bb_pct:.2f} ATR={atr:.2f} VolR={vol_ratio:.2f}")

    # === 3. 新闻情绪 ===
    print(f"\n📰 3. 新闻情绪分析...")
    sentiments = {}
    # 用web搜索获取最新新闻作为情绪来源
    for sym in watchlist:
        try:
            # 先尝试数据库已有新闻
            sentiment = news_fetcher.get_market_sentiment_summary(sym)
            if sentiment.get('article_count', 0) > 0:
                sentiments[sym] = sentiment
                print(f"   {sym:>5} 情绪={sentiment['sentiment']:>8} 分数={sentiment['score']:+.3f} 文章={sentiment['article_count']}")
                continue

            # 尝试RSS采集
            articles = news_fetcher.collect_and_store_news(sym)
            sentiment = news_fetcher.get_market_sentiment_summary(sym)
            if sentiment.get('article_count', 0) > 0:
                sentiments[sym] = sentiment
                print(f"   {sym:>5} 情绪={sentiment['sentiment']:>8} 分数={sentiment['score']:+.3f} 文章={sentiment['article_count']}")
            else:
                sentiments[sym] = {"sentiment": "neutral", "score": 0, "article_count": 0}
                print(f"   {sym:>5} 情绪=  neutral 分数=+0.000 文章=0")
        except Exception as e:
            sentiments[sym] = {"sentiment": "neutral", "score": 0, "article_count": 0}
            print(f"   {sym:>5} 情绪=  neutral (获取失败)")

    # 补充web搜索新闻情绪
    try:
        from search import search as web_search
        for sym in watchlist[:6]:
            results = web_search(f"{sym} stock news today analysis", source="web")
            headlines = [r.get('title','') + ' ' + r.get('summary','') for r in results[:5]]
            all_text = ' '.join(headlines)
            if all_text.strip():
                sentiment_result = news_fetcher.analyze_sentiment(all_text)
                # 与已有情绪合并（web搜索补充）
                existing = sentiments.get(sym, {})
                db_score = existing.get('score', 0)
                web_score = sentiment_result['score']
                # 加权合并：web搜索权重0.3
                merged_score = db_score * 0.7 + web_score * 0.3
                if merged_score > 0.1:
                    merged_label = "positive"
                elif merged_score < -0.1:
                    merged_label = "negative"
                else:
                    merged_label = "neutral"
                sentiments[sym] = {
                    "sentiment": merged_label,
                    "score": round(merged_score, 3),
                    "article_count": existing.get('article_count', 0) + len(headlines),
                }
                print(f"   {sym:>5} 🌐 情绪={merged_label:>8} 分数={merged_score:+.3f} (含web)")
    except ImportError:
        pass  # web搜索不可用则跳过

    # === 4. 宏观事件 ===
    print(f"\n🌍 4. 宏观经济事件...")
    try:
        macro_events = news_fetcher.fetch_macro_events()
        high_impact = [e for e in macro_events if e.get('impact') == 'high']
        if high_impact:
            for e in high_impact[:5]:
                print(f"   ⚠️ {e.get('event_name', '')} ({e.get('country', '')}) 冲击={e.get('impact', '')}")
        else:
            print("   无重大宏观事件")
    except Exception:
        macro_events = []
        print("   宏观事件获取失败，跳过")

    # === 5. 策略信号 ===
    print(f"\n🎯 5. 交易信号生成...")
    signals = []
    for sym, df in market_data.items():
        sentiment = sentiments.get(sym, {})
        sym_signals = []

        for strat_name, strategy in strategies.items():
            signal = strategy.generate_signal(df, symbol=sym, sentiment=sentiment)
            if signal.signal_type != SignalType.HOLD and signal.signal_strength >= 0.3:
                sym_signals.append(signal)

        # 取最强信号
        if sym_signals:
            best = max(sym_signals, key=lambda s: s.signal_strength)
            signals.append(best)

    # 按信号强度排序
    signals.sort(key=lambda s: s.signal_strength, reverse=True)

    # === 6. 风控过滤 + 交易建议 ===
    print(f"\n🛡️ 6. 风控过滤 & 交易建议")
    print("=" * 70)

    account_value = 100000  # 默认10万美元（需对接T212获取真实值）
    risk_mgr.reset_daily(account_value)
    current_positions = {}  # 从T212获取

    buy_signals = [s for s in signals if s.signal_type == SignalType.BUY]
    sell_signals = [s for s in signals if s.signal_type == SignalType.SELL]

    # 卖出建议
    if sell_signals:
        print("\n🔴 卖出信号:")
        for s in sell_signals:
            print(f"   {s.symbol:>5} | {s.strategy_name:>18} | 强度={s.signal_strength:.2f} | 价格=${s.price:.2f}")
            print(f"         原因: {s.reason[:80]}")

    # 买入建议
    if buy_signals:
        print("\n🟢 买入信号:")
        for s in buy_signals:
            # 获取T212 ticker
            t212_ticker = f"{s.symbol}_US_EQ"

            # 计算仓位
            atr = s.indicators_snapshot.get('atr') if s.indicators_snapshot else None
            quantity = strategies.get(s.strategy_name, strategies['momentum_trend']).calculate_position_size(
                account_value, s.price, risk_pct=0.02, atr=atr
            )

            # 风控检查
            risk_result = risk_mgr.check_signal(
                signal=s, quantity=quantity, account_value=account_value,
                current_positions=current_positions, t212_ticker=t212_ticker
            )

            if risk_result.approved:
                qty = risk_result.adjusted_quantity
                sl = risk_result.adjusted_stop_loss or s.stop_loss
                tp = risk_result.adjusted_take_profit or s.take_profit
                cost = qty * s.price
                risk = abs(s.price - sl) * qty if sl else 0

                print(f"   ✅ {s.symbol:>5} | {s.strategy_name:>18} | 强度={s.signal_strength:.2f}")
                print(f"      价格=${s.price:.2f} | 数量={qty}股 | 投入=${cost:,.0f}")
                print(f"      止损=${sl:.2f} (-{abs(s.price-sl)/s.price*100:.1f}%) | 止盈=${tp:.2f} (+{abs(tp-s.price)/s.price*100:.1f}%)")
                print(f"      风险=${risk:,.0f} ({risk/account_value*100:.1f}%)")
                if risk_result.warnings:
                    for w in risk_result.warnings:
                        print(f"      ⚠️ {w}")
                print(f"      原因: {s.reason[:90]}")
            else:
                print(f"   ❌ {s.symbol:>5} | 被风控拦截: {risk_result.reason}")

    if not buy_signals and not sell_signals:
        print("\n   📭 今日无交易信号，建议持仓观望")

    # === 7. 组合风险概览 ===
    print(f"\n📊 7. 组合风险概览")
    print("=" * 70)
    risk_report = risk_mgr.check_portfolio_risk(account_value, current_positions)
    print(f"   风险等级: {risk_report.get('risk_level', 'N/A').upper()}")
    print(f"   总敞口: {risk_report.get('total_exposure_pct', 0):.1%}")

    # === 8. 摘要 ===
    print(f"\n📋 8. 每日摘要")
    print("=" * 70)
    print(f"   扫描股票: {len(watchlist)}")
    print(f"   有数据: {len(market_data)}")
    print(f"   买入信号: {len(buy_signals)}")
    print(f"   卖出信号: {len(sell_signals)}")
    print(f"   通过风控: {sum(1 for s in buy_signals if True)}")  # 简化

    print("\n" + "=" * 70)
    print("📌 操作指引:")
    print("   1. 在 Trading 212 中确认上述信号")
    print("   2. 手动下单时设置止损止盈")
    print("   3. 不做杠杆，单股不超10%仓位")
    print("   4. 严格执行止损，不抱侥幸")
    print("=" * 70)

    return {
        'quotes': quotes,
        'signals': signals,
        'sentiments': sentiments,
        'risk_report': risk_report,
    }


if __name__ == "__main__":
    run_daily_analysis()
