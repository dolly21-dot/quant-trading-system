"""
订单管理器 - 统一管理订单生命周期
从策略信号到实际下单的中间层，集成风控检查
"""
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

from loguru import logger

from execution.trading212 import Trading212Client
from strategy.base import StrategySignal, SignalType
from data.db import DatabaseManager


@dataclass
class ManagedOrder:
    """管理中的订单"""
    signal: StrategySignal
    t212_ticker: str
    quantity: float
    order_type: str  # market, limit, stop
    limit_price: float = None
    status: str = "pending"  # pending, submitted, filled, cancelled, rejected
    t212_order_id: str = None
    db_trade_id: int = None
    created_at: datetime = None
    filled_at: datetime = None
    fill_price: float = None


class OrderManager:
    """订单管理器"""

    def __init__(self, t212_client: Trading212Client = None, db: DatabaseManager = None):
        self.t212 = t212_client or Trading212Client()
        self.db = db or DatabaseManager()
        self.active_orders: dict[str, ManagedOrder] = {}  # order_key -> ManagedOrder
        self.max_open_orders = 20  # 最大挂单数

    def submit_signal(self, signal: StrategySignal, t212_ticker: str,
                      quantity: float, account_value: float) -> Optional[ManagedOrder]:
        """
        提交策略信号，转换为实际订单
        Returns:
            ManagedOrder 或 None（如果被风控拦截）
        """
        # 生成订单键
        order_key = f"{signal.symbol}_{signal.strategy_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # 1. 验证信号
        if signal.signal_type == SignalType.HOLD:
            logger.debug(f"信号为HOLD，跳过: {signal.symbol}")
            return None

        # 2. 验证Trading 212端
        validation = self.t212.validate_order(t212_ticker, quantity)
        if not validation["valid"]:
            for issue in validation["issues"]:
                logger.warning(f"订单验证失败: {issue}")
            return None

        # 3. 数量取整
        quantity = max(int(quantity), 1)  # 至少1股

        # 4. 创建ManagedOrder
        managed = ManagedOrder(
            signal=signal,
            t212_ticker=t212_ticker,
            quantity=quantity,
            order_type="market",  # 默认市价单
            created_at=datetime.now(timezone.utc),
        )

        # 5. 根据信号类型决定订单类型
        if signal.signal_type == SignalType.BUY:
            result = self._execute_buy(managed, account_value)
        elif signal.signal_type == SignalType.SELL:
            result = self._execute_sell(managed)
        elif signal.signal_type == SignalType.EXIT:
            result = self._execute_exit(managed)
        else:
            return None

        if result:
            self.active_orders[order_key] = managed
            # 记录到数据库
            self._record_trade(managed)

        return managed if result else None

    def _execute_buy(self, order: ManagedOrder, account_value: float) -> bool:
        """执行买入"""
        signal = order.signal
        side = "BUY"

        # 现金检查
        cash = self.t212.get_account_cash()
        estimated_cost = signal.price * order.quantity
        if estimated_cost > cash * 0.95:  # 保留5%现金
            logger.warning(
                f"资金不足: 需要${estimated_cost:.2f}, 可用${cash:.2f}"
            )
            return False

        # 下市价单
        result = self.t212.place_market_order(
            ticker=order.t212_ticker,
            quantity=order.quantity,
            side=side,
            take_profit=signal.take_profit,
            stop_loss=signal.stop_loss,
        )

        if result.get("error"):
            order.status = "rejected"
            logger.error(f"买入被拒: {signal.symbol} | {result.get('detail', '未知原因')}")
            return False

        order.t212_order_id = str(result.get("id", ""))
        order.status = "submitted"
        logger.info(
            f"✅ 买入已提交: {signal.symbol} x{order.quantity} @ {signal.price:.2f} "
            f"| SL={signal.stop_loss} TP={signal.take_profit} | T212 ID={order.t212_order_id}"
        )
        return True

    def _execute_sell(self, order: ManagedOrder) -> bool:
        """执行卖出"""
        signal = order.signal

        # 检查是否持有
        position = self.t212.get_position_for_ticker(order.t212_ticker)
        if not position:
            logger.warning(f"未持有 {signal.symbol}，无法卖出")
            return False

        holding_qty = float(position.get("quantity", 0))
        if holding_qty <= 0:
            logger.warning(f"持仓为0: {signal.symbol}")
            return False

        # 卖出全部或部分
        sell_qty = min(order.quantity, holding_qty)
        order.quantity = sell_qty

        result = self.t212.place_market_order(
            ticker=order.t212_ticker,
            quantity=sell_qty,
            side="SELL",
        )

        if result.get("error"):
            order.status = "rejected"
            return False

        order.t212_order_id = str(result.get("id", ""))
        order.status = "submitted"
        logger.info(
            f"✅ 卖出已提交: {signal.symbol} x{sell_qty} @ ~{signal.price:.2f} | T212 ID={order.t212_order_id}"
        )
        return True

    def _execute_exit(self, order: ManagedOrder) -> bool:
        """执行清仓"""
        position = self.t212.get_position_for_ticker(order.t212_ticker)
        if not position:
            return False

        holding_qty = float(position.get("quantity", 0))
        if holding_qty <= 0:
            return False

        order.quantity = holding_qty

        result = self.t212.place_market_order(
            ticker=order.t212_ticker,
            quantity=holding_qty,
            side="SELL",
        )

        if result.get("error"):
            order.status = "rejected"
            return False

        order.t212_order_id = str(result.get("id", ""))
        order.status = "submitted"
        logger.info(f"✅ 清仓已提交: {order.signal.symbol} x{holding_qty}")
        return True

    def _record_trade(self, order: ManagedOrder):
        """记录交易到数据库"""
        signal = order.signal
        trade_data = {
            "trade_id": order.t212_order_id or f"local_{id(order)}",
            "symbol": signal.symbol,
            "t212_ticker": order.t212_ticker,
            "side": signal.signal_type.value,
            "order_type": order.order_type,
            "quantity": order.quantity,
            "price": signal.price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "strategy_name": signal.strategy_name,
            "signal_reason": signal.reason,
            "status": order.status,
        }
        order.db_trade_id = self.db.insert_trade(trade_data)

    def update_order_status(self, order_key: str):
        """更新订单状态（查询T212）"""
        order = self.active_orders.get(order_key)
        if not order or not order.t212_order_id:
            return

        try:
            t212_order = self.t212.get_order_by_id(int(order.t212_order_id))
            if t212_order:
                new_status = t212_order.get("status", "").lower()
                if new_status != order.status:
                    order.status = new_status
                    if new_status == "filled":
                        order.filled_at = datetime.now(timezone.utc)
                        order.fill_price = t212_order.get("fillPrice", order.signal.price)
                    self.db.update_trade_status(
                        order.db_trade_id, new_status, order.filled_at
                    )
                    logger.info(f"订单状态更新: {order_key} -> {new_status}")
        except Exception as e:
            logger.error(f"更新订单状态失败: {e}")

    def check_stop_losses(self):
        """检查止损/止盈触发（由T212自动执行，此处做状态同步）"""
        for key in list(self.active_orders.keys()):
            self.update_order_status(key)

    def get_active_positions_summary(self) -> dict:
        """获取活跃持仓摘要"""
        positions = self.t212.get_open_positions()
        summary = {}
        for pos in positions:
            ticker = pos.get("ticker", "")
            summary[ticker] = {
                "quantity": float(pos.get("quantity", 0)),
                "avg_price": float(pos.get("averagePrice", 0)),
                "current_price": float(pos.get("currentPrice", 0)),
                "pnl": float(pos.get("ppl", 0)),
                "pnl_pct": float(pos.get("pplPercentage", 0)),
                "market_value": float(pos.get("value", 0)),
            }
        return summary

    def sync_positions(self):
        """同步持仓快照到数据库"""
        positions = self.t212.get_open_positions()
        account_value = self.t212.get_account_value()

        for pos in positions:
            ticker = pos.get("ticker", "")
            market_value = float(pos.get("value", 0))
            self.db.session_scope() if hasattr(self.db, "session_scope") else None

            snapshot = {
                "symbol": ticker.split("_")[0] if "_" in ticker else ticker,
                "t212_ticker": ticker,
                "quantity": float(pos.get("quantity", 0)),
                "avg_entry_price": float(pos.get("averagePrice", 0)),
                "current_price": float(pos.get("currentPrice", 0)),
                "unrealized_pnl": float(pos.get("ppl", 0)),
                "unrealized_pnl_pct": float(pos.get("pplPercentage", 0)),
                "market_value": market_value,
                "weight_in_portfolio": market_value / account_value if account_value > 0 else 0,
            }
            logger.debug(f"持仓同步: {ticker} x{snapshot['quantity']} PnL={snapshot['unrealized_pnl_pct']:.2f}%")
