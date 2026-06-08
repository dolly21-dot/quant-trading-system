"""
市场数据采集模块 - 多源数据获取
数据源: Stooq (实时+历史), Twelve Data (历史), Trading 212 (实时)
yfinance 作为后备（可能被限频）
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests
from loguru import logger

from config.settings import DataConfig
from data.db import DatabaseManager


# Stooq股票代码映射
STOOQ_SYMBOL_MAP = {
    'AAPL': 'aapl.us', 'MSFT': 'msft.us', 'NVDA': 'nvda.us',
    'GOOGL': 'googl.us', 'AMZN': 'amzn.us', 'META': 'meta.us',
    'LLY': 'lly.us', 'UNH': 'unh.us', 'JPM': 'jpm.us',
    'V': 'v.us', 'XOM': 'xom.us', 'WMT': 'wmt.us',
    'SPY': 'spy.us', 'QQQ': 'qqq.us', 'DIA': 'dia.us',
    'IWM': 'iwm.us', 'TSM': 'tsm.us', 'AVGO': 'avgo.us',
    'PFE': 'pfe.us', 'ABBV': 'abbv.us', 'BAC': 'bac.us',
    'GS': 'gs.us', 'MA': 'ma.us', 'CVX': 'cvx.us',
    'COP': 'cop.us', 'SLB': 'slb.us', 'COST': 'cost.us',
    'PG': 'pg.us', 'KO': 'ko.us', 'PEP': 'pep.us',
    'JNJ': 'jnj.us', 'XLK': 'xlk.us', 'XLF': 'xlf.us',
    'XLE': 'xle.us', 'XLV': 'xlv.us', 'XLI': 'xli.us',
}


class MarketDataFetcher:
    """市场数据采集器 - 多源适配"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self._td_apikey = DataConfig.ALPHA_VANTAGE_KEY or "demo"  # Twelve Data用demo

    # ============================================================
    # 实时报价 (Stooq - 免费无限)
    # ============================================================

    def fetch_realtime_price(self, symbol: str) -> Optional[float]:
        """获取实时价格 - Stooq"""
        try:
            stooq_sym = STOOQ_SYMBOL_MAP.get(symbol, f"{symbol.lower()}.us")
            url = f"https://stooq.com/q/l/?s={stooq_sym}&f=sd2t2ohlcv&h&e=csv"
            r = self._session.get(url, timeout=10)
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                close = float(row.get('Close', 0))
                if close > 0:
                    return close
            return None
        except Exception as e:
            logger.debug(f"Stooq获取{symbol}失败: {e}")
            return self._fetch_price_yfinance(symbol)

    def fetch_batch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """批量获取实时报价"""
        quotes = {}
        for symbol in symbols:
            try:
                stooq_sym = STOOQ_SYMBOL_MAP.get(symbol, f"{symbol.lower()}.us")
                url = f"https://stooq.com/q/l/?s={stooq_sym}&f=sd2t2ohlcv&h&e=csv"
                r = self._session.get(url, timeout=10)
                reader = csv.DictReader(io.StringIO(r.text))
                for row in reader:
                    close = float(row.get('Close', 0))
                    if close > 0:
                        quotes[symbol] = {
                            'date': row.get('Date', ''),
                            'open': float(row.get('Open', 0)),
                            'high': float(row.get('High', 0)),
                            'low': float(row.get('Low', 0)),
                            'close': close,
                            'volume': int(float(row.get('Volume', 0))),
                        }
            except Exception as e:
                logger.warning(f"获取{symbol}报价失败: {e}")
        return quotes

    # ============================================================
    # 历史K线 (Twelve Data / yfinance)
    # ============================================================

    def fetch_historical(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """获取历史K线 - 优先Twelve Data, 后备yfinance"""
        # 1. 尝试Twelve Data
        df = self._fetch_historical_twelvedata(symbol, period, interval)
        if not df.empty:
            # 存入数据库
            if self.db:
                records = df.to_dict("records")
                if records:
                    self.db.bulk_insert_ohlcv(records)
            return df

        # 2. 后备: yfinance
        df = self._fetch_historical_yfinance(symbol, period, interval)
        if not df.empty and self.db:
            records = df.to_dict("records")
            if records:
                self.db.bulk_insert_ohlcv(records)
        return df

    def _fetch_historical_twelvedata(self, symbol: str, period: str = "3mo",
                                      interval: str = "1d") -> pd.DataFrame:
        """从Twelve Data获取历史数据"""
        # Twelve Data interval格式: 1day (非1d)
        td_interval_map = {
            "1d": "1day", "1day": "1day",
            "1wk": "1week", "1week": "1week",
            "1mo": "1month", "1month": "1month",
            "5m": "5min", "15m": "15min", "30m": "30min",
            "1h": "1h", "4h": "4h",
        }
        td_interval = td_interval_map.get(interval, "1day")

        period_map = {"1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}
        outputsize = period_map.get(period, 90)

        try:
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": symbol,
                "interval": td_interval,
                "outputsize": min(outputsize, 500),
                "apikey": self._td_apikey,
            }
            r = self._session.get(url, params=params, timeout=15)
            data = r.json()

            if 'values' not in data:
                logger.debug(f"Twelve Data无数据 {symbol}: {data.get('message', 'unknown')}")
                return pd.DataFrame()

            rows = []
            for v in reversed(data['values']):  # API返回倒序
                try:
                    rows.append({
                        "timestamp": pd.Timestamp(v["datetime"]),
                        "open": float(v["open"]),
                        "high": float(v["high"]),
                        "low": float(v["low"]),
                        "close": float(v["close"]),
                        "volume": float(v.get("volume", 0)),
                        "symbol": symbol,
                        "interval": interval,
                    })
                except (ValueError, TypeError):
                    continue

            df = pd.DataFrame(rows)
            if not df.empty:
                logger.info(f"Twelve Data获取 {symbol}: {len(df)} 条")
            return df

        except Exception as e:
            logger.debug(f"Twelve Data获取 {symbol} 失败: {e}")
            return pd.DataFrame()

    def _fetch_historical_yfinance(self, symbol: str, period: str = "1y",
                                    interval: str = "1d") -> pd.DataFrame:
        """yfinance后备"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                return pd.DataFrame()

            df = df.reset_index()
            col_map = {
                "Date": "timestamp", "Datetime": "timestamp",
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
                "Adj Close": "adjusted_close"
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            df["symbol"] = symbol
            df["interval"] = interval
            return df
        except Exception as e:
            logger.debug(f"yfinance获取 {symbol} 失败: {e}")
            return pd.DataFrame()

    def fetch_intraday(self, symbol: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
        """获取盘中数据"""
        outputsize = days * 78  # 每天约78根5分钟K线
        try:
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": symbol,
                "interval": interval,
                "outputsize": min(outputsize, 500),
                "apikey": self._td_apikey,
            }
            r = self._session.get(url, params=params, timeout=15)
            data = r.json()
            if 'values' not in data:
                return self._fetch_historical_yfinance(symbol, f"{days}d", interval)

            rows = []
            for v in reversed(data['values']):
                try:
                    rows.append({
                        "timestamp": pd.Timestamp(v["datetime"]),
                        "open": float(v["open"]),
                        "high": float(v["high"]),
                        "low": float(v["low"]),
                        "close": float(v["close"]),
                        "volume": float(v.get("volume", 0)),
                        "symbol": symbol,
                        "interval": interval,
                    })
                except (ValueError, TypeError):
                    continue
            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()

    # ============================================================
    # 公司基本面
    # ============================================================

    def fetch_company_info(self, symbol: str) -> dict:
        """获取公司信息"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                "symbol": symbol,
                "name": info.get("longName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "pb_ratio": info.get("priceToBook"),
                "dividend_yield": info.get("dividendYield"),
                "beta": info.get("beta"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            }
        except Exception:
            return {"symbol": symbol}

    # ============================================================
    # 技术指标计算
    # ============================================================

    def calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标 - 输出包含所有常用指标的DataFrame"""
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

            # Z-Score
            df["z_score_20"] = (close - close.rolling(20).mean()) / close.rolling(20).std()

            return df

        except ImportError:
            logger.error("ta 库未安装，请运行: pip install ta")
            return df
        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return df

    def _fetch_price_yfinance(self, symbol: str) -> Optional[float]:
        """yfinance后备获取价格"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = info.get("lastPrice") if hasattr(info, "get") else None
            return float(price) if price else None
        except Exception:
            return None
