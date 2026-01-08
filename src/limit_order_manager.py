"""
限价单管理器模块
在斐波那契网格相邻点位预挂买卖限价单，捕捉快速价格波动（wick）
"""
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime

from okx_client import OKXClient
from fibonacci_strategy import FibonacciStrategyEngine, FibonacciConfig, PRICE_OFFSETS
from telegram_notifier import TelegramNotifier
from database import TradingDatabase


# 价格随机偏移小数部分 (.2, .3, .6, .7)
ALLOWED_OFFSETS = [0.2, 0.3, 0.6, 0.7]


def get_random_offset() -> float:
    """获取随机价格偏移"""
    return random.choice(ALLOWED_OFFSETS)


def adjust_buy_price(base_price: float) -> float:
    """
    调整买入价格：略低于基准价格
    例如: $130.00 -> $129.2 / $129.3 / $129.6 / $129.7
    """
    offset = get_random_offset()
    return round(base_price - 1 + offset, 1)


def adjust_sell_price(base_price: float) -> float:
    """
    调整卖出价格：略高于基准价格
    例如: $133.00 -> $133.2 / $133.3 / $133.6 / $133.7
    """
    offset = get_random_offset()
    return round(base_price + offset, 1)


@dataclass
class LimitOrder:
    """限价单信息"""
    order_id: str           # OKX 订单 ID
    client_order_id: str    # 客户端订单 ID
    side: str               # buy / sell
    price: float            # 挂单价格
    quantity: int           # 数量
    fib_level: float        # 对应的斐波那契级别
    fib_price: float        # 原始斐波那契价格
    status: str = "live"    # live / filled / canceled
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: datetime = None


class LimitOrderManager:
    """限价单管理器"""
    
    def __init__(
        self,
        okx_client: OKXClient,
        strategy_engine: FibonacciStrategyEngine,
        telegram: TelegramNotifier,
        database: TradingDatabase,
        symbol: str = "SOL-USDT-SWAP"
    ):
        self.client = okx_client
        self.strategy = strategy_engine
        self.telegram = telegram
        self.db = database
        self.symbol = symbol
        self.logger = logging.getLogger(__name__)
        
        # 当前活跃的限价单
        self.active_buy_order: Optional[LimitOrder] = None
        self.active_sell_order: Optional[LimitOrder] = None
        
        # 订单 ID 计数器
        self._order_counter = int(time.time())
    
    def _generate_client_order_id(self, side: str) -> str:
        """生成客户端订单 ID"""
        self._order_counter += 1
        return f"fib_{side}_{self._order_counter}"
    
    def get_adjacent_fib_levels(
        self, 
        current_price: float
    ) -> Tuple[Optional[Tuple], Optional[Tuple]]:
        """
        获取当前价格相邻的斐波那契点位
        
        Returns:
            (lower_level, upper_level)
            每个 level 是 (index, fib_level, fib_price, target_position) 或 None
        """
        fib_levels = self.strategy.fib_levels
        
        lower_level = None
        upper_level = None
        
        for i, (level, fib_price, target_pos) in enumerate(fib_levels):
            if fib_price < current_price:
                lower_level = (i, level, fib_price, target_pos)
            elif fib_price > current_price and upper_level is None:
                upper_level = (i, level, fib_price, target_pos)
                break
        
        return lower_level, upper_level
    
    def calculate_order_quantity(
        self,
        current_position: int,
        target_position: int,
        side: str
    ) -> int:
        """
        计算订单数量
        
        Args:
            current_position: 当前持仓
            target_position: 目标持仓
            side: buy / sell
            
        Returns:
            订单数量
        """
        if side == "buy":
            # 买入：目标持仓 - 当前持仓
            qty = target_position - current_position
            return max(0, qty)
        else:
            # 卖出：当前持仓 - 目标持仓
            qty = current_position - target_position
            return max(0, qty)
    
    def place_limit_buy_order(
        self,
        price: float,
        quantity: int,
        fib_level: float,
        fib_price: float
    ) -> Optional[LimitOrder]:
        """
        下买入限价单
        
        Args:
            price: 调整后的挂单价格
            quantity: 数量
            fib_level: 斐波那契级别
            fib_price: 原始斐波那契价格
            
        Returns:
            LimitOrder 或 None
        """
        if quantity <= 0:
            self.logger.info(f"买入数量为 0，跳过挂单")
            return None
        
        client_order_id = self._generate_client_order_id("buy")
        
        self.logger.info(f"下买入限价单: 价格=${price:.1f}, 数量={quantity}, Fib={fib_level:.3f}")
        
        try:
            result = self.client.place_order(
                inst_id=self.symbol,
                td_mode="cross",
                side="buy",
                order_type="limit",
                sz=str(quantity),
                px=str(price)
            )
            
            if result.get("code") == "0" and result.get("data"):
                order_id = result["data"][0].get("ordId", "")
                
                order = LimitOrder(
                    order_id=order_id,
                    client_order_id=client_order_id,
                    side="buy",
                    price=price,
                    quantity=quantity,
                    fib_level=fib_level,
                    fib_price=fib_price,
                    status="live"
                )
                
                self.logger.info(f"买入限价单已挂: ordId={order_id}, 价格=${price:.1f}")
                return order
            else:
                error_msg = result.get("msg", "未知错误")
                self.logger.error(f"买入限价单失败: {error_msg}")
                return None
                
        except Exception as e:
            self.logger.error(f"下买入限价单异常: {e}")
            return None
    
    def place_limit_sell_order(
        self,
        price: float,
        quantity: int,
        fib_level: float,
        fib_price: float
    ) -> Optional[LimitOrder]:
        """
        下卖出限价单
        
        Args:
            price: 调整后的挂单价格
            quantity: 数量
            fib_level: 斐波那契级别
            fib_price: 原始斐波那契价格
            
        Returns:
            LimitOrder 或 None
        """
        if quantity <= 0:
            self.logger.info(f"卖出数量为 0，跳过挂单")
            return None
        
        client_order_id = self._generate_client_order_id("sell")
        
        self.logger.info(f"下卖出限价单: 价格=${price:.1f}, 数量={quantity}, Fib={fib_level:.3f}")
        
        try:
            result = self.client.place_order(
                inst_id=self.symbol,
                td_mode="cross",
                side="sell",
                order_type="limit",
                sz=str(quantity),
                px=str(price),
                reduce_only=True  # 卖出只减仓
            )
            
            if result.get("code") == "0" and result.get("data"):
                order_id = result["data"][0].get("ordId", "")
                
                order = LimitOrder(
                    order_id=order_id,
                    client_order_id=client_order_id,
                    side="sell",
                    price=price,
                    quantity=quantity,
                    fib_level=fib_level,
                    fib_price=fib_price,
                    status="live"
                )
                
                self.logger.info(f"卖出限价单已挂: ordId={order_id}, 价格=${price:.1f}")
                return order
            else:
                error_msg = result.get("msg", "未知错误")
                self.logger.error(f"卖出限价单失败: {error_msg}")
                return None
                
        except Exception as e:
            self.logger.error(f"下卖出限价单异常: {e}")
            return None
    
    def cancel_order(self, order: LimitOrder) -> bool:
        """
        撤销订单
        
        Args:
            order: 要撤销的订单
            
        Returns:
            是否成功
        """
        if not order or order.status != "live":
            return True
        
        try:
            result = self.client.cancel_order(
                inst_id=self.symbol,
                ord_id=order.order_id
            )
            
            if result.get("code") == "0":
                order.status = "canceled"
                self.logger.info(f"订单已撤销: {order.side} ordId={order.order_id}")
                return True
            else:
                error_msg = result.get("msg", "未知错误")
                # 订单可能已经成交或不存在
                if "Order does not exist" in error_msg or "51400" in str(result.get("code", "")):
                    self.logger.warning(f"订单不存在，可能已成交: {order.order_id}")
                    return True
                self.logger.error(f"撤单失败: {error_msg}")
                return False
                
        except Exception as e:
            self.logger.error(f"撤单异常: {e}")
            return False
    
    def check_order_status(self, order: LimitOrder) -> str:
        """
        检查订单状态
        
        Args:
            order: 要检查的订单
            
        Returns:
            状态: live / filled / canceled / partially_filled
        """
        if not order:
            return "none"
        
        try:
            result = self.client.get_order(
                inst_id=self.symbol,
                ord_id=order.order_id
            )
            
            if result.get("code") == "0" and result.get("data"):
                order_data = result["data"][0]
                state = order_data.get("state", "")
                
                # OKX 订单状态映射
                # live: 等待成交
                # partially_filled: 部分成交
                # filled: 完全成交
                # canceled: 已撤销
                
                if state == "filled":
                    return "filled"
                elif state == "partially_filled":
                    return "partially_filled"
                elif state == "canceled":
                    return "canceled"
                elif state == "live":
                    return "live"
                else:
                    return state
            else:
                # 订单可能不存在
                return "unknown"
                
        except Exception as e:
            self.logger.error(f"查询订单状态异常: {e}")
            return "error"
    
    def update_orders(
        self,
        current_price: float,
        current_position: int
    ) -> Dict:
        """
        更新限价单
        
        根据当前价格和持仓，在相邻斐波那契点位挂买卖限价单
        
        Args:
            current_price: 当前价格
            current_position: 当前持仓
            
        Returns:
            更新结果
        """
        result = {
            "buy_order": None,
            "sell_order": None,
            "filled_orders": [],
            "canceled_orders": []
        }
        
        # 检查价格是否在范围内
        if not self.strategy.is_price_in_range(current_price):
            self.logger.info(f"价格 ${current_price:.2f} 超出范围，取消所有挂单")
            self._cancel_all_orders()
            return result
        
        # 获取相邻的斐波那契点位
        lower_level, upper_level = self.get_adjacent_fib_levels(current_price)
        
        self.logger.info(f"当前价格: ${current_price:.2f}, 持仓: {current_position}")
        if lower_level:
            self.logger.info(f"  下方点位: Fib {lower_level[1]:.3f} @ ${lower_level[2]:.2f}")
        if upper_level:
            self.logger.info(f"  上方点位: Fib {upper_level[1]:.3f} @ ${upper_level[2]:.2f}")
        
        # 处理买入限价单（在下方点位）
        if lower_level:
            _, fib_level, fib_price, target_pos = lower_level
            buy_price = adjust_buy_price(fib_price)
            buy_qty = self.calculate_order_quantity(current_position, target_pos, "buy")
            
            # 检查是否需要更新买入订单
            need_new_buy = self._should_update_buy_order(buy_price, buy_qty, fib_level)
            
            if need_new_buy and buy_qty > 0:
                # 先取消旧订单
                if self.active_buy_order:
                    self.cancel_order(self.active_buy_order)
                    result["canceled_orders"].append(self.active_buy_order)
                
                # 下新订单
                new_order = self.place_limit_buy_order(buy_price, buy_qty, fib_level, fib_price)
                if new_order:
                    self.active_buy_order = new_order
                    result["buy_order"] = new_order
        else:
            # 没有下方点位，取消买入订单
            if self.active_buy_order:
                self.cancel_order(self.active_buy_order)
                result["canceled_orders"].append(self.active_buy_order)
                self.active_buy_order = None
        
        # 处理卖出限价单（在上方点位）
        if upper_level and current_position > 0:
            _, fib_level, fib_price, target_pos = upper_level
            sell_price = adjust_sell_price(fib_price)
            sell_qty = self.calculate_order_quantity(current_position, target_pos, "sell")
            
            # 检查是否需要更新卖出订单
            need_new_sell = self._should_update_sell_order(sell_price, sell_qty, fib_level)
            
            if need_new_sell and sell_qty > 0:
                # 先取消旧订单
                if self.active_sell_order:
                    self.cancel_order(self.active_sell_order)
                    result["canceled_orders"].append(self.active_sell_order)
                
                # 下新订单
                new_order = self.place_limit_sell_order(sell_price, sell_qty, fib_level, fib_price)
                if new_order:
                    self.active_sell_order = new_order
                    result["sell_order"] = new_order
        else:
            # 没有上方点位或无持仓，取消卖出订单
            if self.active_sell_order:
                self.cancel_order(self.active_sell_order)
                result["canceled_orders"].append(self.active_sell_order)
                self.active_sell_order = None
        
        return result
    
    def _should_update_buy_order(
        self,
        new_price: float,
        new_qty: int,
        new_fib_level: float
    ) -> bool:
        """检查是否需要更新买入订单"""
        if not self.active_buy_order:
            return True
        
        # 如果斐波那契级别变了，需要更新
        if abs(self.active_buy_order.fib_level - new_fib_level) > 0.001:
            return True
        
        # 如果数量变了，需要更新
        if self.active_buy_order.quantity != new_qty:
            return True
        
        return False
    
    def _should_update_sell_order(
        self,
        new_price: float,
        new_qty: int,
        new_fib_level: float
    ) -> bool:
        """检查是否需要更新卖出订单"""
        if not self.active_sell_order:
            return True
        
        # 如果斐波那契级别变了，需要更新
        if abs(self.active_sell_order.fib_level - new_fib_level) > 0.001:
            return True
        
        # 如果数量变了，需要更新
        if self.active_sell_order.quantity != new_qty:
            return True
        
        return False
    
    def _cancel_all_orders(self):
        """取消所有活跃订单"""
        if self.active_buy_order:
            self.cancel_order(self.active_buy_order)
            self.active_buy_order = None
        
        if self.active_sell_order:
            self.cancel_order(self.active_sell_order)
            self.active_sell_order = None
    
    def check_filled_orders(self, current_position: int) -> List[LimitOrder]:
        """
        检查订单是否成交
        
        Args:
            current_position: 当前持仓（用于计算利润）
            
        Returns:
            成交的订单列表
        """
        filled_orders = []
        
        # 检查买入订单
        if self.active_buy_order:
            status = self.check_order_status(self.active_buy_order)
            if status == "filled":
                self.active_buy_order.status = "filled"
                self.active_buy_order.filled_at = datetime.now()
                filled_orders.append(self.active_buy_order)
                
                # 发送 Telegram 通知
                self._notify_order_filled(self.active_buy_order, current_position)
                
                # 记录到数据库
                self._record_trade(self.active_buy_order)
                
                self.active_buy_order = None
        
        # 检查卖出订单
        if self.active_sell_order:
            status = self.check_order_status(self.active_sell_order)
            if status == "filled":
                self.active_sell_order.status = "filled"
                self.active_sell_order.filled_at = datetime.now()
                filled_orders.append(self.active_sell_order)
                
                # 发送 Telegram 通知
                self._notify_order_filled(self.active_sell_order, current_position)
                
                # 记录到数据库
                self._record_trade(self.active_sell_order)
                
                self.active_sell_order = None
        
        return filled_orders
    
    def _notify_order_filled(self, order: LimitOrder, current_position: int):
        """
        发送订单成交通知
        
        Args:
            order: 成交的订单
            current_position: 成交后的持仓
        """
        if not self.telegram:
            return
        
        try:
            if order.side == "buy":
                # 买入通知
                new_position = current_position + order.quantity
                self.telegram.send_fibonacci_trade_notification(
                    action="BUY",
                    price=order.price,
                    quantity=order.quantity,
                    target_position=new_position,
                    current_position=new_position
                )
            else:
                # 卖出通知 - 计算利润
                new_position = current_position - order.quantity
                
                # 从数据库获取平均成本计算利润
                profit = self._calculate_profit(order)
                
                self.telegram.send_fibonacci_trade_notification(
                    action="SELL",
                    price=order.price,
                    quantity=order.quantity,
                    target_position=new_position,
                    current_position=new_position,
                    profit=profit
                )
                
        except Exception as e:
            self.logger.error(f"发送通知失败: {e}")
    
    def _calculate_profit(self, order: LimitOrder) -> float:
        """
        计算卖出利润
        
        Args:
            order: 卖出订单
            
        Returns:
            利润金额
        """
        if not self.db:
            return 0.0
        
        try:
            # 获取平均成本
            total_qty, avg_cost = self.db.get_total_position(self.symbol)
            if avg_cost and avg_cost > 0:
                profit = (order.price - avg_cost) * order.quantity
                return round(profit, 2)
        except Exception as e:
            self.logger.error(f"计算利润失败: {e}")
        
        return 0.0
    
    def _record_trade(self, order: LimitOrder):
        """
        记录交易到数据库
        
        Args:
            order: 成交的订单
        """
        if not self.db:
            return
        
        try:
            if order.side == "buy":
                self.db.record_buy(
                    symbol=self.symbol,
                    entry_price=order.price,
                    quantity=order.quantity,
                    direction="LONG",
                    notes=f"限价单成交 Fib {order.fib_level:.3f}"
                )
            else:
                self.db.record_sell_fifo(
                    symbol=self.symbol,
                    exit_price=order.price,
                    quantity=order.quantity,
                    direction="LONG"
                )
        except Exception as e:
            self.logger.error(f"记录交易失败: {e}")
    
    def get_status(self) -> Dict:
        """获取限价单管理器状态"""
        return {
            "active_buy_order": {
                "order_id": self.active_buy_order.order_id if self.active_buy_order else None,
                "price": self.active_buy_order.price if self.active_buy_order else None,
                "quantity": self.active_buy_order.quantity if self.active_buy_order else None,
                "fib_level": self.active_buy_order.fib_level if self.active_buy_order else None
            } if self.active_buy_order else None,
            "active_sell_order": {
                "order_id": self.active_sell_order.order_id if self.active_sell_order else None,
                "price": self.active_sell_order.price if self.active_sell_order else None,
                "quantity": self.active_sell_order.quantity if self.active_sell_order else None,
                "fib_level": self.active_sell_order.fib_level if self.active_sell_order else None
            } if self.active_sell_order else None
        }
    
    def sync_with_exchange(self):
        """
        与交易所同步订单状态
        
        启动时调用，检查是否有未完成的订单
        """
        try:
            result = self.client.get_orders_pending(inst_type="SWAP", inst_id=self.symbol)
            
            if result.get("code") == "0" and result.get("data"):
                pending_orders = result["data"]
                self.logger.info(f"发现 {len(pending_orders)} 个未完成订单")
                
                for order_data in pending_orders:
                    side = order_data.get("side", "")
                    order_id = order_data.get("ordId", "")
                    price = float(order_data.get("px", 0))
                    qty = int(float(order_data.get("sz", 0)))
                    
                    self.logger.info(f"  {side} ordId={order_id}, 价格=${price:.1f}, 数量={qty}")
                    
                    # 可以选择取消这些订单或恢复跟踪
                    # 这里选择取消，让系统重新挂单
                    self.client.cancel_order(inst_id=self.symbol, ord_id=order_id)
                    self.logger.info(f"  已取消旧订单: {order_id}")
            else:
                self.logger.info("没有未完成的订单")
                
        except Exception as e:
            self.logger.error(f"同步订单状态失败: {e}")


# 测试代码
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=== 限价单管理器测试 ===")
    
    # 测试价格调整
    print("\n价格调整测试:")
    for base_price in [130.0, 133.0, 137.0, 142.0]:
        buy_price = adjust_buy_price(base_price)
        sell_price = adjust_sell_price(base_price)
        print(f"  基准价格 ${base_price:.2f} -> 买入 ${buy_price:.1f}, 卖出 ${sell_price:.1f}")
    
    # 测试斐波那契点位查找
    print("\n斐波那契点位查找测试:")
    config = FibonacciConfig(price_min=100.0, price_max=160.0, max_position=40)
    engine = FibonacciStrategyEngine(config)
    
    # 模拟管理器（不连接交易所）
    class MockOKXClient:
        pass
    
    manager = LimitOrderManager(
        okx_client=MockOKXClient(),
        strategy_engine=engine,
        telegram=None,
        database=None
    )
    
    test_prices = [105.0, 115.0, 125.0, 135.0, 145.0, 155.0]
    for price in test_prices:
        lower, upper = manager.get_adjacent_fib_levels(price)
        print(f"\n  当前价格: ${price:.2f}")
        if lower:
            print(f"    下方: Fib {lower[1]:.3f} @ ${lower[2]:.2f}, 目标持仓 {lower[3]}")
        if upper:
            print(f"    上方: Fib {upper[1]:.3f} @ ${upper[2]:.2f}, 目标持仓 {upper[3]}")
