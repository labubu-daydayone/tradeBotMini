"""
SOL 全仓合约交易策略模块
实现网格分批买入、价格-利润线性关系和动态杠杆策略
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple, List
from enum import Enum
from datetime import datetime

from config import TradingStrategy, GridConfig


class PriceZone(Enum):
    """价格区间"""
    HIGH = "high"      # 120 - 150
    LOW = "low"        # 90 - 120
    UNSAFE = "unsafe"  # < 90 或 > 150 (不交易)


class DropType(Enum):
    """跌幅类型"""
    NONE = "none"      # 无跌幅或跌幅不足
    NORMAL = "normal"  # 正常跌幅 (3.2-3.6美元)
    LARGE = "large"    # 大跌幅 (≥5美元)


@dataclass
class GridBuySignal:
    """网格买入信号"""
    should_buy: bool
    quantity: int  # 买入张数
    drop_type: DropType
    drop_amount: float  # 跌幅金额
    reason: str


@dataclass
class GridSellSignal:
    """网格卖出信号"""
    should_sell: bool
    sell_quantity: int  # 卖出张数
    reserve_quantity: int  # 保留张数
    is_reserve_sell: bool  # 是否是保留仓位卖出
    target_price: float  # 目标卖出价格
    reason: str


@dataclass
class PositionState:
    """持仓状态"""
    total_quantity: float = 0.0  # 总持仓张数
    avg_price: float = 0.0  # 平均开仓价格
    total_value: float = 0.0  # 持仓总价值
    unrealized_pnl: float = 0.0  # 未实现盈亏
    reserved_quantity: float = 0.0  # 保留仓位张数（等更高价卖出）


class TradingStrategyEngine:
    """交易策略引擎"""
    
    def __init__(self, config: TradingStrategy):
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # 价格记录
        self.last_buy_price: float = 0.0  # 上次买入价格
        self.highest_price: float = 0.0   # 记录的最高价格
        self.position_state = PositionState()
        
    def is_price_safe(self, price: float) -> bool:
        """检查价格是否在安全交易范围内"""
        return self.config.safe_price_min <= price <= self.config.safe_price_max
    
    def get_price_zone(self, price: float) -> PriceZone:
        """判断价格所在区间"""
        if not self.is_price_safe(price):
            return PriceZone.UNSAFE
        
        if price >= self.config.price_threshold:
            return PriceZone.HIGH
        return PriceZone.LOW
    
    def get_max_contract_amount(self, price: float) -> float:
        """
        获取最大合约金额限制
        
        Args:
            price: 当前价格
            
        Returns:
            最大合约金额 (USDT)
        """
        zone = self.get_price_zone(price)
        capital = self.config.capital
        
        if zone == PriceZone.HIGH:
            return capital * self.config.high_price_leverage_ratio
        elif zone == PriceZone.LOW:
            return capital * self.config.low_price_leverage_ratio
        else:
            return 0.0
    
    def check_position_limit(
        self,
        current_price: float,
        current_position_qty: float,
        buy_quantity: int
    ) -> Tuple[bool, str]:
        """
        检查买入是否超过本金限制
        
        Args:
            current_price: 当前价格
            current_position_qty: 当前持仓张数
            buy_quantity: 计划买入张数
            
        Returns:
            (是否可以买入, 原因)
        """
        max_amount = self.get_max_contract_amount(current_price)
        if max_amount == 0:
            return False, "价格超出安全范围"
        
        current_value = current_position_qty * current_price
        new_value = buy_quantity * current_price
        total_value = current_value + new_value
        
        if total_value > max_amount:
            return False, (
                f"超出本金限制: 当前持仓 ${current_value:.2f} + "
                f"新买入 ${new_value:.2f} = ${total_value:.2f} > "
                f"最大限额 ${max_amount:.2f}"
            )
        
        return True, f"本金检查通过: ${total_value:.2f} / ${max_amount:.2f}"
    
    def calculate_drop_type(self, current_price: float, reference_price: float) -> Tuple[DropType, float]:
        """
        计算跌幅类型
        
        Args:
            current_price: 当前价格
            reference_price: 参考价格（上次买入价或最高价）
            
        Returns:
            (跌幅类型, 跌幅金额)
        """
        if reference_price == 0:
            return DropType.NONE, 0.0
        
        drop = reference_price - current_price
        
        if drop < 0:
            # 价格上涨，不是跌幅
            return DropType.NONE, drop
        
        grid = self.config.grid
        
        if drop >= grid.large_drop:
            return DropType.LARGE, drop
        elif grid.normal_drop_min <= drop <= grid.normal_drop_max:
            return DropType.NORMAL, drop
        else:
            return DropType.NONE, drop
    
    def get_grid_buy_quantity(self, price: float, drop_type: DropType) -> int:
        """
        根据价格区间和跌幅类型获取买入张数
        
        Args:
            price: 当前价格
            drop_type: 跌幅类型
            
        Returns:
            买入张数
        """
        zone = self.get_price_zone(price)
        grid = self.config.grid
        
        if zone == PriceZone.UNSAFE or drop_type == DropType.NONE:
            return 0
        
        if zone == PriceZone.HIGH:
            if drop_type == DropType.LARGE:
                return grid.high_price_large_qty
            else:
                return grid.high_price_normal_qty
        else:  # LOW
            if drop_type == DropType.LARGE:
                return grid.low_price_large_qty
            else:
                return grid.low_price_normal_qty
    
    def generate_buy_signal(
        self,
        current_price: float,
        current_position_qty: float
    ) -> GridBuySignal:
        """
        生成网格买入信号
        
        Args:
            current_price: 当前价格
            current_position_qty: 当前持仓张数
            
        Returns:
            GridBuySignal
        """
        # 检查价格安全性
        if not self.is_price_safe(current_price):
            return GridBuySignal(
                should_buy=False,
                quantity=0,
                drop_type=DropType.NONE,
                drop_amount=0,
                reason=f"价格 ${current_price:.2f} 超出安全范围"
            )
        
        # 确定参考价格
        reference_price = self.last_buy_price if self.last_buy_price > 0 else self.highest_price
        
        if reference_price == 0:
            # 首次交易，记录当前价格作为参考
            self.highest_price = current_price
            return GridBuySignal(
                should_buy=False,
                quantity=0,
                drop_type=DropType.NONE,
                drop_amount=0,
                reason="首次运行，记录价格作为参考"
            )
        
        # 更新最高价
        if current_price > self.highest_price:
            self.highest_price = current_price
        
        # 计算跌幅
        drop_type, drop_amount = self.calculate_drop_type(current_price, reference_price)
        
        if drop_type == DropType.NONE:
            return GridBuySignal(
                should_buy=False,
                quantity=0,
                drop_type=drop_type,
                drop_amount=drop_amount,
                reason=f"跌幅 ${drop_amount:.2f} 不满足买入条件"
            )
        
        # 获取买入张数
        quantity = self.get_grid_buy_quantity(current_price, drop_type)
        
        if quantity == 0:
            return GridBuySignal(
                should_buy=False,
                quantity=0,
                drop_type=drop_type,
                drop_amount=drop_amount,
                reason="买入张数为0"
            )
        
        # 检查本金限制
        can_buy, limit_reason = self.check_position_limit(
            current_price, current_position_qty, quantity
        )
        
        if not can_buy:
            return GridBuySignal(
                should_buy=False,
                quantity=quantity,
                drop_type=drop_type,
                drop_amount=drop_amount,
                reason=f"本金限制: {limit_reason}"
            )
        
        zone = self.get_price_zone(current_price)
        drop_type_cn = "大跌" if drop_type == DropType.LARGE else "正常跌幅"
        zone_cn = "高价区间" if zone == PriceZone.HIGH else "低价区间"
        
        return GridBuySignal(
            should_buy=True,
            quantity=quantity,
            drop_type=drop_type,
            drop_amount=drop_amount,
            reason=f"{zone_cn} {drop_type_cn} ${drop_amount:.2f}，买入 {quantity} 张"
        )
    
    def generate_sell_signal(
        self,
        current_price: float,
        position_qty: float,
        avg_entry_price: float,
        reserved_qty: float = 0
    ) -> GridSellSignal:
        """
        生成网格卖出信号
        
        Args:
            current_price: 当前价格
            position_qty: 当前持仓张数
            avg_entry_price: 平均开仓价格
            reserved_qty: 已保留的张数
            
        Returns:
            GridSellSignal
        """
        if position_qty <= 0:
            return GridSellSignal(
                should_sell=False,
                sell_quantity=0,
                reserve_quantity=0,
                is_reserve_sell=False,
                target_price=0,
                reason="无持仓"
            )
        
        grid = self.config.grid
        profit_target = self.calculate_profit_target(avg_entry_price)
        
        # 计算策略止盈价格
        strategy_tp_price = avg_entry_price * (1 + profit_target / 100)
        
        # 计算保留仓位止盈价格（涨 $10）
        reserve_tp_price = avg_entry_price + grid.reserve_profit_target
        
        # 可卖出的张数（总持仓 - 保留张数）
        sellable_qty = position_qty - grid.reserve_qty
        
        # 检查是否达到策略止盈
        if current_price >= strategy_tp_price and sellable_qty > 0:
            return GridSellSignal(
                should_sell=True,
                sell_quantity=int(sellable_qty),
                reserve_quantity=grid.reserve_qty,
                is_reserve_sell=False,
                target_price=strategy_tp_price,
                reason=f"达到策略止盈 ${strategy_tp_price:.2f}，卖出 {int(sellable_qty)} 张，保留 {grid.reserve_qty} 张"
            )
        
        # 检查保留仓位是否达到止盈（涨 $10）
        if reserved_qty > 0 and current_price >= reserve_tp_price:
            return GridSellSignal(
                should_sell=True,
                sell_quantity=int(reserved_qty),
                reserve_quantity=0,
                is_reserve_sell=True,
                target_price=reserve_tp_price,
                reason=f"保留仓位达到止盈 ${reserve_tp_price:.2f} (涨 ${grid.reserve_profit_target})，卖出 {int(reserved_qty)} 张"
            )
        
        return GridSellSignal(
            should_sell=False,
            sell_quantity=0,
            reserve_quantity=grid.reserve_qty,
            is_reserve_sell=False,
            target_price=strategy_tp_price,
            reason=f"未达止盈，当前 ${current_price:.2f}，目标 ${strategy_tp_price:.2f}"
        )
    
    def calculate_profit_target(self, price: float) -> float:
        """计算目标利润百分比"""
        zone = self.get_price_zone(price)
        
        if zone == PriceZone.UNSAFE:
            return 0.0
        
        if zone == PriceZone.HIGH:
            min_price = self.config.high_price_range_min
            max_price = self.config.high_price_range_max
            min_profit = self.config.high_price_profit_min
            max_profit = self.config.high_price_profit_max
            
            clamped_price = max(min_price, min(price, max_price))
            ratio = (clamped_price - min_price) / (max_price - min_price)
            profit = max_profit - ratio * (max_profit - min_profit)
        else:
            min_price = self.config.low_price_range_min
            max_price = self.config.low_price_range_max
            min_profit = self.config.low_price_profit_min
            max_profit = self.config.low_price_profit_max
            
            clamped_price = max(min_price, min(price, max_price))
            ratio = (max_price - clamped_price) / (max_price - min_price)
            profit = min_profit + ratio * (max_profit - min_profit)
        
        return round(profit, 2)
    
    def calculate_take_profit_price(self, entry_price: float, is_long: bool) -> float:
        """计算止盈价格"""
        profit_pct = self.calculate_profit_target(entry_price) / 100
        
        if profit_pct == 0:
            return 0.0
        
        if is_long:
            return round(entry_price * (1 + profit_pct), 2)
        else:
            return round(entry_price * (1 - profit_pct), 2)
    
    def calculate_total_contract_value(self, price: float, position_size: float) -> float:
        """计算合约总金额（价格 × 张数）"""
        return round(price * position_size, 2)
    
    def calculate_pnl(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        is_long: bool
    ) -> Tuple[float, float]:
        """计算盈亏"""
        if is_long:
            pnl = (exit_price - entry_price) * position_size
        else:
            pnl = (entry_price - exit_price) * position_size
        
        investment = entry_price * position_size
        pnl_pct = (pnl / investment) * 100 if investment > 0 else 0
        
        return round(pnl, 2), round(pnl_pct, 2)
    
    def can_trade(self, price: float) -> Tuple[bool, str]:
        """检查是否可以交易"""
        if price < self.config.safe_price_min:
            return False, f"价格 ${price:.2f} 低于安全下限 ${self.config.safe_price_min:.2f}"
        
        if price > self.config.safe_price_max:
            return False, f"价格 ${price:.2f} 高于安全上限 ${self.config.safe_price_max:.2f}"
        
        return True, "价格在安全范围内"
    
    def update_last_buy_price(self, price: float):
        """更新上次买入价格"""
        self.last_buy_price = price
    
    def get_strategy_summary(self, price: float, current_position_qty: float = 0) -> dict:
        """获取当前价格下的策略摘要"""
        zone = self.get_price_zone(price)
        can_trade, trade_reason = self.can_trade(price)
        
        max_amount = self.get_max_contract_amount(price)
        current_value = current_position_qty * price
        remaining_amount = max(0, max_amount - current_value)
        
        grid = self.config.grid
        
        if not can_trade:
            return {
                "current_price": price,
                "price_zone": zone.value,
                "can_trade": False,
                "trade_reason": trade_reason,
                "profit_target_pct": 0,
                "max_contract_amount": max_amount,
                "current_position_value": current_value,
                "remaining_amount": remaining_amount,
                "grid_config": {
                    "normal_drop": f"${grid.normal_drop_min}-${grid.normal_drop_max}",
                    "large_drop": f"${grid.large_drop}+",
                    "high_normal_qty": grid.high_price_normal_qty,
                    "high_large_qty": grid.high_price_large_qty,
                    "low_normal_qty": grid.low_price_normal_qty,
                    "low_large_qty": grid.low_price_large_qty,
                    "reserve_qty": grid.reserve_qty,
                    "reserve_profit": grid.reserve_profit_target
                }
            }
        
        profit_target = self.calculate_profit_target(price)
        tp_price = self.calculate_take_profit_price(price, is_long=True)
        
        return {
            "current_price": price,
            "price_zone": zone.value,
            "can_trade": True,
            "trade_reason": trade_reason,
            "profit_target_pct": profit_target,
            "take_profit_price": tp_price,
            "max_contract_amount": max_amount,
            "current_position_value": current_value,
            "remaining_amount": remaining_amount,
            "capital": self.config.capital,
            "last_buy_price": self.last_buy_price,
            "grid_config": {
                "normal_drop": f"${grid.normal_drop_min}-${grid.normal_drop_max}",
                "large_drop": f"${grid.large_drop}+",
                "high_normal_qty": grid.high_price_normal_qty,
                "high_large_qty": grid.high_price_large_qty,
                "low_normal_qty": grid.low_price_normal_qty,
                "low_large_qty": grid.low_price_large_qty,
                "reserve_qty": grid.reserve_qty,
                "reserve_profit": grid.reserve_profit_target
            }
        }


class TradeTracker:
    """交易追踪器"""
    
    def __init__(self):
        self.trades = []
        self.total_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0
        self.reserved_positions = []  # 保留仓位记录
        
    def record_trade(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        is_long: bool,
        pnl: float,
        pnl_pct: float,
        is_reserve: bool = False,
        timestamp: datetime = None
    ):
        """记录交易"""
        trade = {
            "timestamp": timestamp or datetime.now(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "position_size": position_size,
            "direction": "LONG" if is_long else "SHORT",
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "is_reserve": is_reserve
        }
        self.trades.append(trade)
        self.total_pnl += pnl
        
        if pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1
    
    def add_reserved_position(self, entry_price: float, quantity: float):
        """添加保留仓位"""
        self.reserved_positions.append({
            "entry_price": entry_price,
            "quantity": quantity,
            "timestamp": datetime.now()
        })
    
    def get_reserved_quantity(self) -> float:
        """获取保留仓位总张数"""
        return sum(p["quantity"] for p in self.reserved_positions)
    
    def clear_reserved_positions(self):
        """清空保留仓位"""
        self.reserved_positions = []
    
    def get_statistics(self) -> dict:
        """获取交易统计"""
        total_trades = len(self.trades)
        win_rate = (self.win_count / total_trades * 100) if total_trades > 0 else 0
        
        return {
            "total_trades": total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(self.total_pnl, 2),
            "reserved_positions": len(self.reserved_positions),
            "reserved_quantity": self.get_reserved_quantity()
        }
    
    def get_last_trade(self) -> Optional[dict]:
        """获取最近一笔交易"""
        return self.trades[-1] if self.trades else None


def print_strategy_table(price: float, config: TradingStrategy = None, position_qty: float = 0):
    """打印策略参数表格"""
    if config is None:
        config = TradingStrategy()
    
    engine = TradingStrategyEngine(config)
    summary = engine.get_strategy_summary(price, position_qty)
    
    print("\n" + "=" * 70)
    print(f"SOL 价格: ${price:.2f} | 持仓: {position_qty} 张")
    print("=" * 70)
    print(f"价格区间: {summary['price_zone'].upper()}")
    print(f"可交易: {'是' if summary['can_trade'] else '否'}")
    
    if summary['can_trade']:
        print(f"目标利润: {summary['profit_target_pct']:.2f}%")
        print(f"止盈价格: ${summary.get('take_profit_price', 0):.2f}")
        print(f"最大合约金额: ${summary['max_contract_amount']:.2f}")
        print(f"当前持仓价值: ${summary['current_position_value']:.2f}")
        print(f"剩余可用额度: ${summary['remaining_amount']:.2f}")
    
    print("-" * 70)
    print("网格配置:")
    grid = summary['grid_config']
    print(f"  正常跌幅: {grid['normal_drop']} | 大跌幅: {grid['large_drop']}")
    print(f"  高价区间买入: 正常 {grid['high_normal_qty']} 张, 大跌 {grid['high_large_qty']} 张")
    print(f"  低价区间买入: 正常 {grid['low_normal_qty']} 张, 大跌 {grid['low_large_qty']} 张")
    print(f"  保留张数: {grid['reserve_qty']} 张 (涨 ${grid['reserve_profit']} 后卖出)")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    print("\n=== 网格交易策略测试 ===")
    config = TradingStrategy(capital=1000.0)
    
    # 测试不同价格
    test_prices = [90, 100, 110, 120, 130, 140, 150]
    
    for price in test_prices:
        print_strategy_table(price, config, position_qty=0)
    
    # 测试买入信号
    print("\n=== 买入信号测试 ===")
    engine = TradingStrategyEngine(config)
    engine.last_buy_price = 130.0  # 设置上次买入价格
    
    test_cases = [
        (126.5, 0),   # 跌 3.5，正常跌幅
        (125.0, 0),   # 跌 5，大跌幅
        (130.0, 5),   # 已有持仓
        (125.0, 8),   # 接近限额
    ]
    
    for current_price, position_qty in test_cases:
        signal = engine.generate_buy_signal(current_price, position_qty)
        print(f"\n价格 ${current_price:.2f}, 持仓 {position_qty} 张:")
        print(f"  买入: {'是' if signal.should_buy else '否'}")
        print(f"  张数: {signal.quantity}")
        print(f"  跌幅: ${signal.drop_amount:.2f} ({signal.drop_type.value})")
        print(f"  原因: {signal.reason}")
