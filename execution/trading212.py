"""
Trading 212 API 客户端 - 适配Trading 212平台
支持: 账户查询、下单(市价/限价/止损)、持仓管理、历史查询
文档: https://docs.trading212.com/api
"""
import json
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

from config.settings import Trading212Config


class Trading212Client:
    """Trading 212 API 客户端"""

    def __init__(self, api_key: str = None, environment: str = None):
        self.api_key = api_key or Trading212Config.API_KEY
        self.environment = environment or Trading212Config.ENVIRONMENT
        self.base_url = Trading212Config.base_url()
        self.headers = Trading212Config.headers()

        # 使用httpx异步/同步客户端
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=self.headers,
            timeout=30.0,
        )

        logger.info(f"Trading 212 客户端初始化: 环境={self.environment}")

    # ============================================================
    # 账户相关
    # ============================================================

    def get_account_summary(self) -> dict:
        """获取账户概要"""
        try:
            response = self._client.get("/equity/account/summary")
            response.raise_for_status()
            data = response.json()
            logger.debug(f"账户余额: {data.get('free', 'N/A')}")
            return data
        except httpx.HTTPStatusError as e:
            logger.error(f"获取账户概要失败: {e.response.status_code} {e.response.text}")
            return {}
        except Exception as e:
            logger.error(f"获取账户概要异常: {e}")
            return {}

    def get_account_cash(self) -> float:
        """获取可用现金"""
        summary = self.get_account_summary()
        return float(summary.get("free", 0))

    def get_account_value(self) -> float:
        """获取账户总价值"""
        summary = self.get_account_summary()
        return float(summary.get("total", 0))

    # ============================================================
    # 交易工具
    # ============================================================

    def get_instruments(self) -> list[dict]:
        """获取所有可交易工具"""
        try:
            response = self._client.get("/equity/metadata/instruments")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取交易工具失败: {e}")
            return []

    def get_exchange_schedules(self) -> list[dict]:
        """获取交易所时间表"""
        try:
            response = self._client.get("/equity/metadata/exchanges")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取交易所时间表失败: {e}")
            return []

    # ============================================================
    # 下单
    # ============================================================

    def place_market_order(self, ticker: str, quantity: float,
                           side: str = "BUY", take_profit: float = None,
                           stop_loss: float = None) -> dict:
        """
        下市价单
        Args:
            ticker: Trading 212 instrument ticker (e.g., "AAPL_US_EQ")
            quantity: 数量（必须为整数，部分股票支持小数）
            side: BUY 或 SELL
            take_profit: 止盈价
            stop_loss: 止损价
        """
        payload = {
            "instrumentTicker": ticker,
            "quantity": quantity,
        }
        if take_profit:
            payload["takeProfit"] = round(take_profit, 2)
        if stop_loss:
            payload["stopLoss"] = round(stop_loss, 2)

        endpoint = "/equity/orders/market"
        if side.upper() == "SELL":
            # 卖出：检查是否持有该股票
            positions = self.get_open_positions()
            holding = [p for p in positions if p.get("ticker") == ticker]
            if holding:
                payload["instrumentTicker"] = ticker

        try:
            response = self._client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(f"市价单已提交: {side} {quantity} {ticker} | ID={data.get('id')}")
            return data
        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            logger.error(f"市价单失败: {e.response.status_code} {error_text}")
            return {"error": True, "status": e.response.status_code, "detail": error_text}
        except Exception as e:
            logger.error(f"市价单异常: {e}")
            return {"error": True, "detail": str(e)}

    def place_limit_order(self, ticker: str, quantity: float, limit_price: float,
                          side: str = "BUY", take_profit: float = None,
                          stop_loss: float = None, time_validity: str = "GTC") -> dict:
        """
        下限价单
        Args:
            ticker: Trading 212 ticker
            quantity: 数量
            limit_price: 限价
            side: BUY/SELL
            take_profit: 止盈
            stop_loss: 止损
            time_validity: GTC(直到取消), DAY(当日有效)
        """
        payload = {
            "instrumentTicker": ticker,
            "quantity": quantity,
            "limitPrice": round(limit_price, 2),
            "timeValidity": time_validity,
        }
        if take_profit:
            payload["takeProfit"] = round(take_profit, 2)
        if stop_loss:
            payload["stopLoss"] = round(stop_loss, 2)

        try:
            response = self._client.post("/equity/orders/limit", json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(f"限价单已提交: {side} {quantity} {ticker} @ {limit_price}")
            return data
        except httpx.HTTPStatusError as e:
            logger.error(f"限价单失败: {e.response.status_code} {e.response.text}")
            return {"error": True, "status": e.response.status_code}
        except Exception as e:
            logger.error(f"限价单异常: {e}")
            return {"error": True, "detail": str(e)}

    def place_stop_order(self, ticker: str, quantity: float, stop_price: float,
                         side: str = "BUY") -> dict:
        """
        下止损单
        """
        payload = {
            "instrumentTicker": ticker,
            "quantity": quantity,
            "stopPrice": round(stop_price, 2),
        }

        try:
            response = self._client.post("/equity/orders/stop", json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(f"止损单已提交: {side} {quantity} {ticker} @ stop={stop_price}")
            return data
        except httpx.HTTPStatusError as e:
            logger.error(f"止损单失败: {e.response.status_code} {e.response.text}")
            return {"error": True, "status": e.response.status_code}
        except Exception as e:
            logger.error(f"止损单异常: {e}")
            return {"error": True, "detail": str(e)}

    def place_stop_limit_order(self, ticker: str, quantity: float,
                               stop_price: float, limit_price: float,
                               side: str = "BUY") -> dict:
        """
        下止损限价单
        """
        payload = {
            "instrumentTicker": ticker,
            "quantity": quantity,
            "stopPrice": round(stop_price, 2),
            "limitPrice": round(limit_price, 2),
        }

        try:
            response = self._client.post("/equity/orders/stop-limit", json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(f"止损限价单已提交: {side} {quantity} {ticker}")
            return data
        except httpx.HTTPStatusError as e:
            logger.error(f"止损限价单失败: {e.response.status_code}")
            return {"error": True, "status": e.response.status_code}
        except Exception as e:
            logger.error(f"止损限价单异常: {e}")
            return {"error": True, "detail": str(e)}

    def cancel_order(self, order_id: int) -> dict:
        """取消订单"""
        try:
            response = self._client.delete(f"/equity/orders/{order_id}")
            response.raise_for_status()
            logger.info(f"订单已取消: {order_id}")
            return {"success": True, "order_id": order_id}
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            return {"error": True, "detail": str(e)}

    # ============================================================
    # 订单与持仓查询
    # ============================================================

    def get_open_orders(self) -> list[dict]:
        """获取所有挂单"""
        try:
            response = self._client.get("/equity/orders")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取挂单失败: {e}")
            return []

    def get_order_by_id(self, order_id: int) -> dict:
        """获取订单详情"""
        try:
            response = self._client.get(f"/equity/orders/{order_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取订单详情失败: {e}")
            return {}

    def get_open_positions(self) -> list[dict]:
        """获取所有持仓"""
        try:
            response = self._client.get("/equity/portfolio")
            response.raise_for_status()
            positions = response.json()
            logger.debug(f"当前持仓: {len(positions)} 个")
            return positions
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def get_position_for_ticker(self, ticker: str) -> Optional[dict]:
        """获取特定股票的持仓"""
        positions = self.get_open_positions()
        for pos in positions:
            if pos.get("ticker") == ticker:
                return pos
        return None

    # ============================================================
    # 历史记录
    # ============================================================

    def get_order_history(self, cursor: int = 0, limit: int = 50,
                          ticker: str = None) -> list[dict]:
        """获取历史订单"""
        params = {"cursor": cursor, "limit": limit}
        if ticker:
            params["ticker"] = ticker

        try:
            response = self._client.get("/equity/history/orders", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("items", [])
        except Exception as e:
            logger.error(f"获取历史订单失败: {e}")
            return []

    def get_dividend_history(self, cursor: int = 0, limit: int = 50) -> list[dict]:
        """获取分红历史"""
        params = {"cursor": cursor, "limit": limit}
        try:
            response = self._client.get("/equity/history/dividends", params=params)
            response.raise_for_status()
            return response.json().get("items", [])
        except Exception as e:
            logger.error(f"获取分红历史失败: {e}")
            return []

    def get_transaction_history(self, cursor: int = 0, limit: int = 50) -> list[dict]:
        """获取交易历史"""
        params = {"cursor": cursor, "limit": limit}
        try:
            response = self._client.get("/equity/history/transactions", params=params)
            response.raise_for_status()
            return response.json().get("items", [])
        except Exception as e:
            logger.error(f"获取交易历史失败: {e}")
            return []

    def request_csv_report(self, time_from: str = None, time_to: str = None) -> dict:
        """请求CSV报告"""
        params = {}
        if time_from:
            params["timeFrom"] = time_from
        if time_to:
            params["timeTo"] = time_to

        try:
            response = self._client.post("/equity/history/exports", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"请求CSV报告失败: {e}")
            return {}

    # ============================================================
    # Pies (组合投资)
    # ============================================================

    def get_pies(self) -> list[dict]:
        """获取所有投资组合(Pies)"""
        try:
            response = self._client.get("/equity/pies")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"获取Pies失败: {e}")
            return []

    # ============================================================
    # 工具方法
    # ============================================================

    def is_market_open(self, ticker: str) -> bool:
        """检查市场是否开盘"""
        # 简单判断：根据ticker后缀推断交易所
        exchanges = self.get_exchange_schedules()
        # TODO: 根据实际交易所ID匹配
        return True  # 默认假设开盘

    def get_ticker_info(self, ticker: str) -> Optional[dict]:
        """获取ticker对应的交易工具信息"""
        instruments = self._instruments_cache if hasattr(self, "_instruments_cache") else None
        if not instruments:
            instruments = self.get_instruments()
            self._instruments_cache = instruments

        for inst in instruments:
            if inst.get("ticker") == ticker:
                return inst
        return None

    def validate_order(self, ticker: str, quantity: float) -> dict:
        """
        验证订单是否合规
        检查: ticker是否存在、数量是否合法、是否允许交易
        """
        issues = []

        # 检查ticker是否存在
        ticker_info = self.get_ticker_info(ticker)
        if not ticker_info:
            issues.append(f"Ticker {ticker} 不存在或不可交易")

        # 检查数量
        if quantity <= 0:
            issues.append(f"无效数量: {quantity}")

        # 检查是否为杠杆产品 (CFD等)
        if ticker_info and ticker_info.get("type") == "CFD":
            issues.append(f"禁止交易CFD/杠杆产品: {ticker}")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "ticker_info": ticker_info,
        }

    def close(self):
        """关闭客户端"""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
