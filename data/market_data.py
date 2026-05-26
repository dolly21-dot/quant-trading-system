"""
市场数据采集模块 - 多源数据获取
数据源: yfinance (主), Alpha Vantage (备), Trading 212 (实时)
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger

from config.settings import DataConfig
from data.db import DatabaseManager


class MarketDataFetcher:
    """市场数据采集器"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()

    def fetch_historical(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """
        获取历史K线数据
        Args:
            symbol: 股票代码 (e.g., AAPL)
            period: 时间跨度 (1d,5d,1mo,3mo,6mo,1y,2y,5y,max)
            interval: K线频率 (1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo)
        """
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)

            if df.empty:
                logger.warning(f"未获取到 {symbol} 的历史数据")
                return pd.DataFrame()

            df = df.reset_index()
            # 统一列名
            col_map = {
                "Date": "timestamp", "Datetime": "timestamp",
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
                "Adj Close": "adjusted_close"
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            df["symbol"] = symbol
            df["interval"] = interval

            # 存入数据库
            records = df.to_dict("records")
            if records and self.db:
                self.db.bulk_insert_ohlcv(records)

            logger.info(f"获取 {symbol} 历史数据: {len(df)} 条, 周期={period}, 频率={interval}")
            return df

        except Exception as e:
            logger.error(f"获取 {symbol} 历史数据失败: {e}")
            return pd.DataFrame()

    def fetch_intraday(self, symbol: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
        """获取盘中数据（yfinance最多5天）"""
        period = f"{days}d"
        return self.fetch_historical(symbol, period=period, interval=interval)

    def fetch_realtime_price(self, symbol: str) -> Optional[float]:
        """获取实时价格"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = info.get("lastPrice") if hasattr(info, "get") else None
            if price is None:
                # 备用方式
                hist = ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    price = hist["Close"].iloc[-1]
            return float(price) if price else None
        except Exception as e:
            logger.error(f"获取 {symbol} 实时价格失败: {e}")
            return None

    def fetch_batch_quotes(self, symbols: list[str]) -> dict[str, float]:
        """批量获取实时报价"""
        quotes = {}
        for symbol in symbols:
            price = self.fetch_realtime_price(symbol)
            if price:
                quotes[symbol] = price
        return quotes

    def fetch_company_info(self, symbol: str) -> dict:
        """获取公司信息与基本面"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            result = {
                "symbol": symbol,
                "name": info.get("longName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "pb_ratio": info.get("priceToBook"),
                "ps_ratio": info.get("priceToSalesTrailing12Months"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "debt_to_equity": info.get("debtToEquity"),
                "roe": info.get("returnOnEquity"),
                "roa": info.get("returnOnAssets"),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "dividend_yield": info.get("dividendYield"),
                "free_cash_flow": info.get("freeCashflow"),
                "earnings_date": info.get("earningsTimestamp"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "avg_volume": info.get("averageVolume"),
                "beta": info.get("beta"),
            }
            return result

        except Exception as e:
            logger.error(f"获取 {symbol} 公司信息失败: {e}")
            return {"symbol": symbol}

    def fetch_earnings_dates(self, symbol: str) -> list[dict]:
        """获取财报日期"""
        try:
            ticker = yf.Ticker(symbol)
            calendar = ticker.calendar
            if calendar is not None and not calendar.empty:
                return calendar.to_dict("records") if hasattr(calendar, "to_dict") else []
            return []
        except Exception as e:
            logger.error(f"获取 {symbol} 财报日期失败: {e}")
            return []

    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算技术指标 - 输出包含所有常用指标的DataFrame
        """
        if df.empty or len(df) < 30:
            logger.warning("数据不足，无法计算技术指标（至少需要30条数据）")
            return df

        try:
            import ta

            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"]

            # 移动平均线
            for period in [5, 10, 20, 30, 50, 100, 200]:
                df[f"sma_{period}"] = ta.trend.sma_indicator(close, window=period)
                df[f"ema_{period}"] = ta.trend.ema_indicator(close, window=period)

            # MACD
            macd = ta.trend.MACD(close)
            df["macd"] = macd.macd()
            df["macd_signal"] = macd.macd_signal()
            df["macd_histogram"] = macd.macd_diff()

            # RSI
            for period in [7, 14, 21]:
                df[f"rsi_{period}"] = ta.momentum.rsi(close, window=period)

            # 布林带
            bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
            df["bb_upper"] = bb.bollinger_hband()
            df["bb_middle"] = bb.bollinger_mavg()
            df["bb_lower"] = bb.bollinger_lband()
            df["bb_width"] = bb.bollinger_wband()
            df["bb_pct"] = bb.bollinger_pband()

            # ATR
            df["atr_14"] = ta.volatility.average_true_range(high, low, close, window=14)

            # ADX
            df["adx"] = ta.trend.adx(high, low, close, window=14)
            df["di_plus"] = ta.trend.adx_pos(high, low, close, window=14)
            df["di_minus"] = ta.trend.adx_neg(high, low, close, window=14)

            # 成交量指标
            df["volume_sma_20"] = volume.rolling(20).mean()
            df["volume_ratio"] = volume / df["volume_sma_20"]
            df["obv"] = ta.volume.on_balance_volume(close, volume)
            df["mfi"] = ta.volume.money_flow_index(high, low, close, volume, window=14)

            # 波动率
            df["realized_vol_20"] = close.pct_change().rolling(20).std() * np.sqrt(252)
            df["realized_vol_10"] = close.pct_change().rolling(10).std() * np.sqrt(252)

            # Stochastic
            stoch = ta.momentum.StochasticOscillator(high, low, close)
            df["stoch_k"] = stoch.stoch()
            df["stoch_d"] = stoch.stoch_signal()

            # VWAP (近似)
            typical_price = (high + low + close) / 3
            df["vwap"] = (typical_price * volume).cumsum() / volume.cumsum()

            # Z-Score
            df["z_score_20"] = (close - close.rolling(20).mean()) / close.rolling(20).std()

            logger.debug(f"技术指标计算完成: {len(df.columns)} 列")
            return df

        except ImportError:
            logger.error("ta 库未安装，请运行: pip install ta")
            return df
        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return df
