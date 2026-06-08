"""
Twelve Data 数据源 - 备用数据获取 + WebSocket实时行情
免费Plan: 8 credits/分钟, 800 credits/天
支持: 实时报价、历史K线、WebSocket流
API文档: https://twelvedata.com/docs
"""
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Callable

import httpx
import pandas as pd
from loguru import logger

from config.settings import DataConfig


class TwelveDataClient:
    """Twelve Data API 客户端"""

    BASE_URL = "https://api.twelvedata.com"
    WS_URL = "wss://ws.twelvedata.com/v1/quotes/price"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or DataConfig.TWELVE_DATA_KEY or ""
        self._client = httpx.Client(timeout=15.0)
        self._request_count = 0
        self._request_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_rate_limit(self):
        """检查请求限额"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._request_date:
            self._request_count = 0
            self._request_date = today

        if self._request_count >= 790:  # 留10个buffer
            logger.warning("Twelve Data 日请求限额接近上限，暂停请求")
            return False

        self._request_count += 1
        return True

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """GET请求"""
        if not self.api_key:
            logger.warning("Twelve Data API key未配置")
            return {}

        if not self._check_rate_limit():
            return {}

        params = params or {}
        params["apikey"] = self.api_key

        try:
            response = self._client.get(f"{self.BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "error":
                logger.warning(f"Twelve Data API错误: {data.get('message', '')}")
                return {}

            return data
        except Exception as e:
            logger.error(f"Twelve Data请求失败: {e}")
            return {}

    # ============================================================
    # 实时报价
    # ============================================================

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        """获取实时价格"""
        data = self._get("price", {"symbol": symbol})
        if data and "price" in data:
            return float(data["price"])
        return None

    def get_realtime_quote(self, symbol: str) -> dict:
        """获取实时完整报价"""
        return self._get("quote", {"symbol": symbol})

    def get_batch_prices(self, symbols: list[str]) -> dict[str, float]:
        """批量获取实时价格"""
        # Twelve Data支持逗号分隔的批量查询
        symbol_str = ",".join(symbols)
        data = self._get("price", {"symbol": symbol_str})

        if not data:
            return {}

        prices = {}
        if isinstance(data, dict) and "price" in data:
            # 单个结果
            prices[symbols[0]] = float(data["price"])
        else:
            # 批量结果
            for sym, val in data.items():
                if isinstance(val, dict) and "price" in val:
                    prices[sym] = float(val["price"])

        return prices

    # ============================================================
    # 历史K线
    # ============================================================

    def get_time_series(self, symbol: str, interval: str = "1day",
                        outputsize: int = 200, start_date: str = None,
                        end_date: str = None) -> pd.DataFrame:
        """
        获取历史K线数据
        Args:
            symbol: 股票代码
            interval: 1min, 5min, 15min, 30min, 1h, 1day, 1week, 1month
            outputsize: 返回数据量 (max 5000)
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": min(outputsize, 5000),
        }
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        data = self._get("time_series", params)

        if not data or "values" not in data:
            return pd.DataFrame()

        df = pd.DataFrame(data["values"])
        if df.empty:
            return df

        # 标准化列名
        col_map = {
            "datetime": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        # 类型转换
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["symbol"] = symbol
        df["interval"] = interval

        # 按时间升序排列
        df = df.sort_values("timestamp").reset_index(drop=True)

        logger.info(f"Twelve Data获取 {symbol} K线: {len(df)} 条, 频率={interval}")
        return df

    # ============================================================
    # 基本面数据
    # ============================================================

    def get_company_profile(self, symbol: str) -> dict:
        """获取公司概况"""
        return self._get("profile", {"symbol": symbol})

    def get_earnings(self, symbol: str) -> dict:
        """获取财报数据"""
        return self._get("earnings", {"symbol": symbol})

    def get_earnings_calendar(self, symbol: str = None) -> dict:
        """获取财报日历"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._get("earnings_calendar", params)

    def get_statistics(self, symbol: str) -> dict:
        """获取统计指标（PE、PB等）"""
        return self._get("statistics", {"symbol": symbol})

    # ============================================================
    # WebSocket 实时行情
    # ============================================================

    def start_websocket(self, symbols: list[str],
                        on_price: Callable[[str, float, dict], None] = None,
                        on_connect: Callable = None,
                        on_disconnect: Callable = None):
        """
        启动WebSocket实时行情流
        Args:
            symbols: 要订阅的股票列表
            on_price: 价格回调 (symbol, price, data)
            on_connect: 连接成功回调
            on_disconnect: 断开回调
        """
        if not self.api_key:
            logger.warning("Twelve Data API key未配置，无法启动WebSocket")
            return

        try:
            import websocket
        except ImportError:
            logger.error("websocket-client未安装: pip install websocket-client")
            return

        def on_message(ws, message):
            try:
                data = json.loads(message)
                event = data.get("event")

                if event == "price":
                    symbol = data.get("symbol", "")
                    price = float(data.get("price", 0))
                    if on_price:
                        on_price(symbol, price, data)
                elif event == "heartbeat":
                    pass  # 心跳忽略
                elif event == "subscribe-status":
                    logger.info(f"WebSocket订阅状态: {data}")

            except Exception as e:
                logger.error(f"WebSocket消息处理错误: {e}")

        def on_error(ws, error):
            logger.error(f"WebSocket错误: {error}")

        def on_open(ws):
            logger.info(f"Twelve Data WebSocket已连接，订阅: {symbols}")
            # 订阅价格
            subscribe_msg = {
                "action": "subscribe",
                "params": {"symbols": ",".join(symbols)}
            }
            ws.send(json.dumps(subscribe_msg))
            if on_connect:
                on_connect()

        def on_close(ws, close_status, close_msg):
            logger.warning(f"Twelve Data WebSocket断开: {close_status}")
            if on_disconnect:
                on_disconnect()

        # 启动WebSocket
        ws_url = f"{self.WS_URL}?apikey={self.api_key}"
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
            on_open=on_open,
            on_close=on_close,
        )

        # 在后台线程运行
        self._ws_thread = threading.Thread(
            target=ws.run_forever,
            kwargs={"ping_interval": 30, "ping_timeout": 10},
            daemon=True,
        )
        self._ws_thread.start()
        self._ws = ws
        logger.info(f"Twelve Data WebSocket已启动，监控 {len(symbols)} 只股票")

    def stop_websocket(self):
        """停止WebSocket"""
        if hasattr(self, "_ws") and self._ws:
            self._ws.close()
            logger.info("Twelve Data WebSocket已停止")

    # ============================================================
    # 工具方法
    # ============================================================

    def get_usage(self) -> dict:
        """查询API使用量"""
        return self._get("api_usage")

    def close(self):
        self._client.close()
        self.stop_websocket()
