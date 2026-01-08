"""
限价单管理器模块
在斐波那契网格点位预挂买卖限价单，捕捉快速价格波动（wick）

支持一级和二级限价单：
- 一级单：相邻斐波那契点位 + 随机偏移
- 二级单：下一个斐波那契点位 + 随机偏移 ± 1U（用于捕捉急涨急跌）

二级单成交后，一级单保持不动（价格不变）
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

# 二级订单额外偏移（美元）
LEVEL2_EXTRA_OFFSET = 1.0


def get_random_offset() -> float:
    """获取随机价格偏移"""
    return random.choice(ALLOWED_OFFSETS)


def adjust_buy_price(base_price: float, is_level2: bool = False) -> float:
    """
    调整买入价格：略低于基准价格
    
    Args:
        base_price: 斐波那契基准价格
        is_level2: 是否为二级订单
        
    Returns:
        调整后的价格
        
    一级: $130.00 -> $129.2 / $129.3 / $129.6 / $129.7
    二级: 在随机偏移基础上再 -1U -> $128.2 / $128.3 / $128.6 / $128.7
    """
    offset = get_random_offset()
    price = round(base_price - 1 + offset, 1)
    
    if is_level2:
        price = round(price - LEVEL2_EXTRA_OFFSET, 1)
    
    return price


def adjust_sell_price(base_price: float, is_level2: bool = False) -> float:
    """
    调整卖出价格：略高于基准价格
    
    Args:
        base_price: 斐波那契基准价格
        is_level2: 是否为二级订单
        
    Returns:
        调整后的价格
        
    一级: $137.08 -> $137.3 / $137.4 / $137.7 / $137.8
    二级: 在随机偏移基础上再 +1U -> $138.3 / $138.4 / $138.7 / $138.8
    """
    offset = get_random_offset()
    price = round(base_price + offset, 1)
    
    if is_level2:
        price = round(price + LEVEL2_EXTRA_OFFSET, 1)
    
    return price


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
    level: int = 1          # 订单级别 (1=一级, 2=二级)
    status: str = "live"    # live / filled / canceled
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: datetime = None


class LimitOrderManager:
    """限价单管理器 - 支持一级和二级限价单"""
    
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
        
        # 当前活跃的限价单（一级）
        self.active_buy_order_l1: Optional[LimitOrder] = None
        self.active_sell_order_l1: Optional[LimitOrder] = None
        
        # 当前活跃的限价单（二级）
        self.active_buy_order_l2: Optional[LimitOrder] = None
        self.active_sell_order_l2: Optional[LimitOrder] = None
        
        # 订单 ID 计数器
        self._order_counter = int(time.time())
    
    def _generate_client_order_id(self, side: str, level: int) -> str:
        """生成客户端订单 ID"""
        self._order_counter += 1
        return f"fib_{side}_L{level}_{self._order_counter}"
    
    def get_two_adjacent_fib_levels(
        self, 
        current_price: float,
        direction: str
    ) -> Tuple[Optional[Tuple], Optional[Tuple]]:
        """
        获取当前价格相邻的两个斐波那契点位
        
        Args:
            current_price: 当前价格
            direction: "lower" 获取下方两个点位, "upper" 获取上方两个点位
        
        Returns:
            (first_level, second_level)
            每个 level 是 (index, fib_level, fib_price, target_position) 或 None
        """
        fib_levels = self.strategy.fib_levels
        
        if direction == "lower":
            # 获取下方两个点位（用于买入）
            lower_levels = []
            for i, (level, fib_price, target_pos) in enumerate(fib_levels):
                if fib_price < current_price:
                    lower_levels.append((i, level, fib_price, target_pos))
            
            # 取最近的两个（倒序取最后两个）
            if len(lower_levels) >= 2:
                return lower_levels[-1], lower_levels[-2]
            elif len(lower_levels) == 1:
                return lower_levels[-1], None
            else:
                return None, None
        
        else:  # direction == "upper"
            # 获取上方两个点位（用于卖出）
            upper_levels = []
            for i, (level, fib_price, target_pos) in enumerate(fib_levels):
                if fib_price > current_price:
                    upper_levels.append((i, level, fib_price, target_pos))
            
            # 取最近的两个（正序取前两个）
            if len(upper_levels) >= 2:
                return upper_levels[0], upper_levels[1]
            elif len(upper_levels) == 1:
                return upper_levels[0], None
            else:
                return None, None
    
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
            qty = target_position - current_position
            return max(0, qty)
        else:
            qty = current_position - target_position
            return max(0, qty)
    
    def place_limit_buy_order(
        self,
        price: float,
        quantity: int,
        fib_level: float,
        fib_price: float,
        level: int = 1
    ) -> Optional[LimitOrder]:
        """
        下买入限价单
        """
        if quantity <= 0:
            self.logger.info(f"买入数量为 0，跳过挂单 (L{level})")
            return None
        
        client_order_id = self._generate_client_order_id("buy", level)
        
        self.logger.info(f"下买入限价单 L{level}: 价格=${price:.1f}, 数量={quantity}, Fib={fib_level:.3f} @ ${fib_price:.2f}")
        
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
                    level=level,
                    status="live"
                )
                
                self.logger.info(f"买入限价单 L{level} 已挂: ordId={order_id}, 价格=${price:.1f}")
                return order
            else:
                error_msg = result.get("msg", "未知错误")
                self.logger.error(f"买入限价单 L{level} 失败: {error_msg}")
                return None
                
        except Exception as e:
            self.logger.error(f"下买入限价单 L{level} 异常: {e}")
            return None
    
    def place_limit_sell_order(
        self,
        price: float,
        quantity: int,
        fib_level: float,
        fib_price: float,
        level: int = 1
    ) -> Optional[LimitOrder]:
        """
        下卖出限价单
        """
        if quantity <= 0:
            self.logger.info(f"卖出数量为 0，跳过挂单 (L{level})")
            return None
        
        client_order_id = self._generate_client_order_id("sell", level)
        
        self.logger.info(f"下卖出限价单 L{level}: 价格=${price:.1f}, 数量={quantity}, Fib={fib_level:.3f} @ ${fib_price:.2f}")
        
        try:
            result = self.client.place_order(
                inst_id=self.symbol,
                td_mode="cross",
                side="sell",
                order_type="limit",
                sz=str(quantity),
                px=str(price),
                reduce_only=True
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
                    level=level,
                    status="live"
                )
                
                self.logger.info(f"卖出限价单 L{level} 已挂: ordId={order_id}, 价格=${price:.1f}")
                return order
            else:
                error_msg = result.get("msg", "未知错误")
                self.logger.error(f"卖出限价单 L{level} 失败: {error_msg}")
                return None
                
        except Exception as e:
            self.logger.error(f"下卖出限价单 L{level} 异常: {e}")
            return None
    
    def cancel_order(self, order: LimitOrder) -> bool:
        """撤销订单"""
        if not order or order.status != "live":
            return True
        
        try:
            result = self.client.cancel_order(
                inst_id=self.symbol,
                ord_id=order.order_id
            )
            
            if result.get("code") == "0":
                order.status = "canceled"
                self.logger.info(f"订单已撤销: {order.side} L{order.level} ordId={order.order_id}")
                return True
            else:
                error_msg = result.get("msg", "未知错误")
                if "Order does not exist" in error_msg or "51400" in str(result.get("code", "")):
                    self.logger.warning(f"订单不存在，可能已成交: {order.order_id}")
                    return True
                self.logger.error(f"撤单失败: {error_msg}")
                return False
                
        except Exception as e:
            self.logger.error(f"撤单异常: {e}")
            return False
    
    def check_order_status(self, order: LimitOrder) -> str:
        """检查订单状态"""
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
                return state
            else:
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
        更新限价单（一级和二级）
        
        一级单：相邻斐波那契点位
        二级单：下一个斐波那契点位 ± 1U
        """
        result = {
            "buy_orders": [],
            "sell_orders": [],
            "filled_orders": [],
            "canceled_orders": []
        }
        
        # 检查价格是否在范围内
        if not self.strategy.is_price_in_range(current_price):
            self.logger.info(f"价格 ${current_price:.2f} 超出范围，取消所有挂单")
            self._cancel_all_orders()
            return result
        
        self.logger.info(f"当前价格: ${current_price:.2f}, 持仓: {current_position}")
        
        # ========== 处理买入限价单 ==========
        lower_l1, lower_l2 = self.get_two_adjacent_fib_levels(current_price, "lower")
        
        if lower_l1:
            self.logger.info(f"  下方L1点位: Fib {lower_l1[1]:.3f} @ ${lower_l1[2]:.2f}, 目标 {lower_l1[3]} 张")
        if lower_l2:
            self.logger.info(f"  下方L2点位: Fib {lower_l2[1]:.3f} @ ${lower_l2[2]:.2f}, 目标 {lower_l2[3]} 张")
        
        # 一级买入单
        if lower_l1:
            _, fib_level, fib_price, target_pos = lower_l1
            buy_qty = self.calculate_order_quantity(current_position, target_pos, "buy")
            
            if buy_qty > 0:
                need_new = self._should_update_order(self.active_buy_order_l1, fib_level, buy_qty)
                
                if need_new:
                    if self.active_buy_order_l1:
                        self.cancel_order(self.active_buy_order_l1)
                        result["canceled_orders"].append(self.active_buy_order_l1)
                    
                    buy_price = adjust_buy_price(fib_price, is_level2=False)
                    new_order = self.place_limit_buy_order(buy_price, buy_qty, fib_level, fib_price, level=1)
                    if new_order:
                        self.active_buy_order_l1 = new_order
                        result["buy_orders"].append(new_order)
            else:
                if self.active_buy_order_l1:
                    self.cancel_order(self.active_buy_order_l1)
                    result["canceled_orders"].append(self.active_buy_order_l1)
                    self.active_buy_order_l1 = None
        else:
            if self.active_buy_order_l1:
                self.cancel_order(self.active_buy_order_l1)
                result["canceled_orders"].append(self.active_buy_order_l1)
                self.active_buy_order_l1 = None
        
        # 二级买入单（下一个斐波那契点位，额外 -1U）
        if lower_l2:
            _, fib_level, fib_price, target_pos = lower_l2
            # 二级单数量：从当前持仓到二级点位的目标持仓
            buy_qty = self.calculate_order_quantity(current_position, target_pos, "buy")
            
            if buy_qty > 0:
                need_new = self._should_update_order(self.active_buy_order_l2, fib_level, buy_qty)
                
                if need_new:
                    if self.active_buy_order_l2:
                        self.cancel_order(self.active_buy_order_l2)
                        result["canceled_orders"].append(self.active_buy_order_l2)
                    
                    # 二级单：随机偏移后再 -1U
                    buy_price = adjust_buy_price(fib_price, is_level2=True)
                    new_order = self.place_limit_buy_order(buy_price, buy_qty, fib_level, fib_price, level=2)
                    if new_order:
                        self.active_buy_order_l2 = new_order
                        result["buy_orders"].append(new_order)
            else:
                if self.active_buy_order_l2:
                    self.cancel_order(self.active_buy_order_l2)
                    result["canceled_orders"].append(self.active_buy_order_l2)
                    self.active_buy_order_l2 = None
        else:
            if self.active_buy_order_l2:
                self.cancel_order(self.active_buy_order_l2)
                result["canceled_orders"].append(self.active_buy_order_l2)
                self.active_buy_order_l2 = None
        
        # ========== 处理卖出限价单 ==========
        upper_l1, upper_l2 = self.get_two_adjacent_fib_levels(current_price, "upper")
        
        if upper_l1:
            self.logger.info(f"  上方L1点位: Fib {upper_l1[1]:.3f} @ ${upper_l1[2]:.2f}, 目标 {upper_l1[3]} 张")
        if upper_l2:
            self.logger.info(f"  上方L2点位: Fib {upper_l2[1]:.3f} @ ${upper_l2[2]:.2f}, 目标 {upper_l2[3]} 张")
        
        # 一级卖出单
        if upper_l1 and current_position > 0:
            _, fib_level, fib_price, target_pos = upper_l1
            sell_qty = self.calculate_order_quantity(current_position, target_pos, "sell")
            
            if sell_qty > 0:
                need_new = self._should_update_order(self.active_sell_order_l1, fib_level, sell_qty)
                
                if need_new:
                    if self.active_sell_order_l1:
                        self.cancel_order(self.active_sell_order_l1)
                        result["canceled_orders"].append(self.active_sell_order_l1)
                    
                    sell_price = adjust_sell_price(fib_price, is_level2=False)
                    new_order = self.place_limit_sell_order(sell_price, sell_qty, fib_level, fib_price, level=1)
                    if new_order:
                        self.active_sell_order_l1 = new_order
                        result["sell_orders"].append(new_order)
            else:
                if self.active_sell_order_l1:
                    self.cancel_order(self.active_sell_order_l1)
                    result["canceled_orders"].append(self.active_sell_order_l1)
                    self.active_sell_order_l1 = None
        else:
            if self.active_sell_order_l1:
                self.cancel_order(self.active_sell_order_l1)
                result["canceled_orders"].append(self.active_sell_order_l1)
                self.active_sell_order_l1 = None
        
        # 二级卖出单（下一个斐波那契点位，额外 +1U）
        if upper_l2 and current_position > 0:
            _, fib_level, fib_price, target_pos = upper_l2
            sell_qty = self.calculate_order_quantity(current_position, target_pos, "sell")
            
            if sell_qty > 0:
                need_new = self._should_update_order(self.active_sell_order_l2, fib_level, sell_qty)
                
                if need_new:
                    if self.active_sell_order_l2:
                        self.cancel_order(self.active_sell_order_l2)
                        result["canceled_orders"].append(self.active_sell_order_l2)
                    
                    # 二级单：随机偏移后再 +1U
                    sell_price = adjust_sell_price(fib_price, is_level2=True)
                    new_order = self.place_limit_sell_order(sell_price, sell_qty, fib_level, fib_price, level=2)
                    if new_order:
                        self.active_sell_order_l2 = new_order
                        result["sell_orders"].append(new_order)
            else:
                if self.active_sell_order_l2:
                    self.cancel_order(self.active_sell_order_l2)
                    result["canceled_orders"].append(self.active_sell_order_l2)
                    self.active_sell_order_l2 = None
        else:
            if self.active_sell_order_l2:
                self.cancel_order(self.active_sell_order_l2)
                result["canceled_orders"].append(self.active_sell_order_l2)
                self.active_sell_order_l2 = None
        
        return result
    
    def _should_update_order(
        self,
        order: Optional[LimitOrder],
        new_fib_level: float,
        new_qty: int
    ) -> bool:
        """检查是否需要更新订单"""
        if not order:
            return True
        
        # 如果斐波那契级别变了，需要更新
        if abs(order.fib_level - new_fib_level) > 0.001:
            return True
        
        # 如果数量变了，需要更新
        if order.quantity != new_qty:
            return True
        
        return False
    
    def _cancel_all_orders(self):
        """取消所有活跃订单"""
        if self.active_buy_order_l1:
            self.cancel_order(self.active_buy_order_l1)
            self.active_buy_order_l1 = None
        if self.active_buy_order_l2:
            self.cancel_order(self.active_buy_order_l2)
            self.active_buy_order_l2 = None
        if self.active_sell_order_l1:
            self.cancel_order(self.active_sell_order_l1)
            self.active_sell_order_l1 = None
        if self.active_sell_order_l2:
            self.cancel_order(self.active_sell_order_l2)
            self.active_sell_order_l2 = None
    
    def check_filled_orders(self, current_position: int) -> List[LimitOrder]:
        """
        检查订单是否成交
        
        二级单成交后，一级单保持不动（价格不变）
        一级单成交后，需要重新挂单
        """
        filled_orders = []
        
        # 检查一级买入订单
        if self.active_buy_order_l1:
            status = self.check_order_status(self.active_buy_order_l1)
            if status == "filled":
                self.active_buy_order_l1.status = "filled"
                self.active_buy_order_l1.filled_at = datetime.now()
                filled_orders.append(self.active_buy_order_l1)
                
                self._notify_order_filled(self.active_buy_order_l1, current_position)
                self._record_trade(self.active_buy_order_l1)
                
                # 一级单成交，清空一级和二级买入单（需要重新挂单）
                self.active_buy_order_l1 = None
                if self.active_buy_order_l2:
                    self.cancel_order(self.active_buy_order_l2)
                    self.active_buy_order_l2 = None
        
        # 检查二级买入订单
        if self.active_buy_order_l2:
            status = self.check_order_status(self.active_buy_order_l2)
            if status == "filled":
                self.active_buy_order_l2.status = "filled"
                self.active_buy_order_l2.filled_at = datetime.now()
                filled_orders.append(self.active_buy_order_l2)
                
                self._notify_order_filled(self.active_buy_order_l2, current_position)
                self._record_trade(self.active_buy_order_l2)
                
                # 二级单成交，只清空二级单，一级单保持不动
                self.active_buy_order_l2 = None
        
        # 检查一级卖出订单
        if self.active_sell_order_l1:
            status = self.check_order_status(self.active_sell_order_l1)
            if status == "filled":
                self.active_sell_order_l1.status = "filled"
                self.active_sell_order_l1.filled_at = datetime.now()
                filled_orders.append(self.active_sell_order_l1)
                
                self._notify_order_filled(self.active_sell_order_l1, current_position)
                self._record_trade(self.active_sell_order_l1)
                
                # 一级单成交，清空一级和二级卖出单（需要重新挂单）
                self.active_sell_order_l1 = None
                if self.active_sell_order_l2:
                    self.cancel_order(self.active_sell_order_l2)
                    self.active_sell_order_l2 = None
        
        # 检查二级卖出订单
        if self.active_sell_order_l2:
            status = self.check_order_status(self.active_sell_order_l2)
            if status == "filled":
                self.active_sell_order_l2.status = "filled"
                self.active_sell_order_l2.filled_at = datetime.now()
                filled_orders.append(self.active_sell_order_l2)
                
                self._notify_order_filled(self.active_sell_order_l2, current_position)
                self._record_trade(self.active_sell_order_l2)
                
                # 二级单成交，只清空二级单，一级单保持不动
                self.active_sell_order_l2 = None
        
        return filled_orders
    
    def _notify_order_filled(self, order: LimitOrder, current_position: int):
        """发送订单成交通知"""
        if not self.telegram:
            return
        
        try:
            level_tag = f"[L{order.level}]" if order.level == 2 else ""
            
            if order.side == "buy":
                new_position = current_position + order.quantity
                self.telegram.send_fibonacci_trade_notification(
                    action="BUY",
                    price=order.price,
                    quantity=order.quantity,
                    target_position=new_position,
                    current_position=new_position,
                    reason=f"{level_tag} 限价单成交" if level_tag else "限价单成交"
                )
            else:
                new_position = current_position - order.quantity
                profit = self._calculate_profit(order)
                
                self.telegram.send_fibonacci_trade_notification(
                    action="SELL",
                    price=order.price,
                    quantity=order.quantity,
                    target_position=new_position,
                    current_position=new_position,
                    profit=profit,
                    reason=f"{level_tag} 限价单成交" if level_tag else "限价单成交"
                )
                
        except Exception as e:
            self.logger.error(f"发送通知失败: {e}")
    
    def _calculate_profit(self, order: LimitOrder) -> float:
        """计算卖出利润"""
        if not self.db:
            return 0.0
        
        try:
            total_qty, avg_cost = self.db.get_total_position(self.symbol)
            if avg_cost and avg_cost > 0:
                profit = (order.price - avg_cost) * order.quantity
                return round(profit, 2)
        except Exception as e:
            self.logger.error(f"计算利润失败: {e}")
        
        return 0.0
    
    def _record_trade(self, order: LimitOrder):
        """记录交易到数据库"""
        if not self.db:
            return
        
        try:
            if order.side == "buy":
                self.db.record_buy(
                    symbol=self.symbol,
                    entry_price=order.price,
                    quantity=order.quantity,
                    direction="LONG",
                    notes=f"限价单 L{order.level} Fib {order.fib_level:.3f}"
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
        def order_info(order):
            if not order:
                return None
            return {
                "order_id": order.order_id,
                "price": order.price,
                "quantity": order.quantity,
                "fib_level": order.fib_level,
                "fib_price": order.fib_price,
                "level": order.level
            }
        
        return {
            "buy_order_l1": order_info(self.active_buy_order_l1),
            "buy_order_l2": order_info(self.active_buy_order_l2),
            "sell_order_l1": order_info(self.active_sell_order_l1),
            "sell_order_l2": order_info(self.active_sell_order_l2)
        }
    
    def sync_with_exchange(self):
        """与交易所同步订单状态"""
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
    
    print("=== 限价单管理器测试（二级订单在下一个斐波那契点位）===")
    
    # 测试斐波那契点位查找
    print("\n斐波那契点位和价格计算测试:")
    config = FibonacciConfig(price_min=100.0, price_max=160.0, max_position=40)
    engine = FibonacciStrategyEngine(config)
    
    class MockOKXClient:
        pass
    
    manager = LimitOrderManager(
        okx_client=MockOKXClient(),
        strategy_engine=engine,
        telegram=None,
        database=None
    )
    
    test_prices = [135.0]
    for price in test_prices:
        print(f"\n当前价格: ${price:.2f}")
        
        # 获取买入点位
        lower_l1, lower_l2 = manager.get_two_adjacent_fib_levels(price, "lower")
        print("\n买入侧:")
        if lower_l1:
            fib_price = lower_l1[2]
            buy_l1 = adjust_buy_price(fib_price, is_level2=False)
            print(f"  L1: Fib {lower_l1[1]:.3f} @ ${fib_price:.2f} -> 挂单 ${buy_l1:.1f}, 目标 {lower_l1[3]} 张")
        if lower_l2:
            fib_price = lower_l2[2]
            buy_l2 = adjust_buy_price(fib_price, is_level2=True)
            print(f"  L2: Fib {lower_l2[1]:.3f} @ ${fib_price:.2f} -> 挂单 ${buy_l2:.1f} (含-1U), 目标 {lower_l2[3]} 张")
        
        # 获取卖出点位
        upper_l1, upper_l2 = manager.get_two_adjacent_fib_levels(price, "upper")
        print("\n卖出侧:")
        if upper_l1:
            fib_price = upper_l1[2]
            sell_l1 = adjust_sell_price(fib_price, is_level2=False)
            print(f"  L1: Fib {upper_l1[1]:.3f} @ ${fib_price:.2f} -> 挂单 ${sell_l1:.1f}, 目标 {upper_l1[3]} 张")
        if upper_l2:
            fib_price = upper_l2[2]
            sell_l2 = adjust_sell_price(fib_price, is_level2=True)
            print(f"  L2: Fib {upper_l2[1]:.3f} @ ${fib_price:.2f} -> 挂单 ${sell_l2:.1f} (含+1U), 目标 {upper_l2[3]} 张")
    
    print("\n" + "="*60)
    print("示例：当前价格 $135，持仓 15 张")
    print("="*60)
    print("""
买入侧:
  L1: Fib 0.550 @ $133.00 -> 挂单 $132.3 (随机偏移), 目标 18 张
  L2: Fib 0.500 @ $130.00 -> 挂单 $128.3 (随机偏移-1U), 目标 20 张

卖出侧:
  L1: Fib 0.618 @ $137.08 -> 挂单 $137.3 (随机偏移), 目标 15 张
  L2: Fib 0.700 @ $142.00 -> 挂单 $143.3 (随机偏移+1U), 目标 12 张

成交逻辑:
  - L2买入成交(急跌) -> L1买入单保持不动，等回调
  - L1买入成交(正常) -> 取消L2买入单，重新挂单
  - L2卖出成交(急涨) -> L1卖出单保持不动，等回调
  - L1卖出成交(正常) -> 取消L2卖出单，重新挂单
""")
