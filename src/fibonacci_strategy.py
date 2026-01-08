"""
斐波那契网格交易策略模块
根据价格动态计算目标持仓，在斐波那契关键点位触发买卖
"""
import logging
import random
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from enum import Enum


# 价格随机偏移小数部分 (.2, .3, .6, .7)
PRICE_OFFSETS = [0.2, 0.3, 0.6, 0.7]


def get_random_price_offset() -> float:
    """获取随机价格偏移 (.2, .3, .6, .7)"""
    return random.choice(PRICE_OFFSETS)


def adjust_buy_price(base_price: float) -> float:
    """
    调整买入价格：略低于基准价格
    例如: $130.00 -> $129.2 / $129.3 / $129.6 / $129.7
    """
    offset = get_random_price_offset()
    return round(base_price - 1 + offset, 1)


def adjust_sell_price(base_price: float) -> float:
    """
    调整卖出价格：略高于基准价格
    例如: $133.00 -> $133.2 / $133.3 / $133.6 / $133.7
    """
    offset = get_random_price_offset()
    return round(base_price + offset, 1)


def generate_fibonacci_ratios(num_levels: int) -> List[float]:
    """
    根据点位数量自动生成斛波那契比例
    
    使用斛波那契数列的比例关系生成点位，包含经典斛波那契比例
    
    Args:
        num_levels: 点位数量 (2-20)
    
    Returns:
        斛波那契比例列表 [0.0, ..., 1.0]
    
    Example:
        >>> generate_fibonacci_ratios(7)
        [0.0, 0.236, 0.382, 0.5, 0.618, 0.764, 1.0]
        >>> generate_fibonacci_ratios(15)
        [0.0, 0.09, 0.146, 0.2, 0.236, 0.3, 0.382, 0.45, 0.5, 0.55, 0.618, 0.7, 0.764, 0.854, 1.0]
    """
    if num_levels < 2:
        num_levels = 2
    if num_levels > 20:
        num_levels = 20
    
    # 经典斛波那契比例（按优先级排序）
    classic_ratios = [
        0.0,    # 必须包含
        1.0,    # 必须包含
        0.5,    # 50% 位置
        0.618,  # 黄金分割
        0.382,  # 1 - 0.618
        0.236,  # 0.618 * 0.382
        0.764,  # 1 - 0.236
        0.146,  # 0.382 * 0.382
        0.854,  # 1 - 0.146
        0.090,  # 0.236 * 0.382
        0.200,  # 补充点位
        0.300,  # 补充点位
        0.450,  # 补充点位
        0.550,  # 补充点位
        0.700,  # 补充点位
    ]
    
    # 选取前 num_levels 个比例
    selected = classic_ratios[:num_levels]
    
    # 如果需要更多点位，用线性插值补充
    if num_levels > len(classic_ratios):
        step = 1.0 / (num_levels - 1)
        selected = [i * step for i in range(num_levels)]
    
    # 排序并返回
    return sorted(selected)


def calculate_fibonacci_levels(
    price_min: float,
    price_max: float,
    max_position: int,
    num_levels: int = 15
) -> List[Tuple[float, float, int]]:
    """
    计算斛波那契网格点位
    
    Args:
        price_min: 最低价格
        price_max: 最高价格
        max_position: 最大持仓张数
        num_levels: 点位数量，默认 15 个
    
    Returns:
        List of (fib_level, price, target_position)
        - fib_level: 斛波那契比例 (0.0 - 1.0)
        - price: 对应价格
        - target_position: 目标持仓张数
    
    Example:
        >>> levels = calculate_fibonacci_levels(100, 160, 40, num_levels=7)
        >>> for level, price, pos in levels:
        ...     print(f"Fib {level:.3f} @ ${price:.2f} -> {pos} 张")
        Fib 0.000 @ $100.00 -> 40 张
        Fib 0.236 @ $114.16 -> 30 张
        Fib 0.382 @ $122.92 -> 24 张
        Fib 0.500 @ $130.00 -> 20 张
        Fib 0.618 @ $137.08 -> 15 张
        Fib 0.764 @ $145.84 -> 9 张
        Fib 1.000 @ $160.00 -> 0 张
    """
    # 根据数量生成斛波那契比例
    fib_ratios = generate_fibonacci_ratios(num_levels)
    
    price_range = price_max - price_min
    result = []
    
    for level in fib_ratios:
        # 计算价格: price_min + range * level
        price = price_min + price_range * level
        
        # 计算目标持仓: 价格越低持仓越多，价格越高持仓越少
        target_pos = int(max_position * (1 - level))
        
        result.append((level, price, target_pos))
    
    return result


def get_target_position_at_price(
    price: float,
    fib_levels: List[Tuple[float, float, int]]
) -> int:
    """
    根据当前价格获取目标持仓
    
    价格在某个斛波那契点位之上，就使用该点位的目标持仓
    
    Args:
        price: 当前价格
        fib_levels: 斛波那契点位列表 [(level, price, target_pos), ...]
    
    Returns:
        目标持仓张数
    """
    if not fib_levels:
        return 0
    
    # 遍历点位，找到当前价格所在的区间
    for i, (level, fib_price, target_pos) in enumerate(fib_levels):
        if price < fib_price:
            # 价格低于该点位，使用上一个点位的目标持仓
            if i == 0:
                return fib_levels[0][2]  # 返回最大持仓
            return fib_levels[i - 1][2]
    
    # 价格超过最高点位
    return fib_levels[-1][2]


@dataclass
class FibonacciConfig:
    """斛波那契策略配置"""
    price_min: float = 100.0      # 最低价格
    price_max: float = 160.0      # 最高价格
    max_position: int = 40        # 最大持仓张数
    symbol: str = "SOL-USDT-SWAP"
    leverage: int = 2             # 杠杆倍数
    num_levels: int = 15          # 斛波那契点位数量 (7 或 15)
    
    @property
    def price_range(self) -> float:
        """价格区间幅度"""
        return self.price_max - self.price_min
    
    def get_fib_prices(self) -> List[Tuple[float, float, int]]:
        """
        获取所有斛波那契价格点位及对应目标持仓
        
        Returns:
            List of (fib_level, price, target_position)
        """
        return calculate_fibonacci_levels(
            price_min=self.price_min,
            price_max=self.price_max,
            max_position=self.max_position,
            num_levels=self.num_levels
        )


class TradeAction(Enum):
    """交易动作"""
    HOLD = "hold"      # 持有不动
    BUY = "buy"        # 买入
    SELL = "sell"      # 卖出


@dataclass
class FibonacciSignal:
    """斐波那契交易信号"""
    action: TradeAction
    quantity: int           # 买入/卖出数量
    current_price: float
    target_position: int    # 目标持仓
    current_position: int   # 当前持仓
    triggered_level: float  # 触发的斐波那契级别
    triggered_price: float  # 触发的价格点位
    reason: str


class FibonacciStrategyEngine:
    """斐波那契策略引擎"""
    
    def __init__(self, config: FibonacciConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # 计算所有斐波那契价格点位
        self.fib_levels = config.get_fib_prices()
        
        # 记录上次触发的价格点位索引
        self.last_triggered_index: Optional[int] = None
        
        # 记录上次价格（用于判断方向）
        self.last_price: float = 0.0
        
        self._log_fib_levels()
    
    def _log_fib_levels(self):
        """打印斐波那契价格点位"""
        self.logger.info("=== 斐波那契网格点位 ===")
        for level, price, target_pos in self.fib_levels:
            self.logger.info(f"  {level:.3f} -> ${price:.2f} -> 目标持仓 {target_pos} 张")
    
    def is_price_in_range(self, price: float) -> bool:
        """检查价格是否在交易范围内"""
        return self.config.price_min <= price <= self.config.price_max
    
    def calculate_target_position(self, price: float) -> int:
        """
        根据当前价格计算目标持仓
        
        价格越低，持仓越多；价格越高，持仓越少
        按斛波那契点位计算，价格在某个点位之上就使用该点位的目标持仓
        """
        if price <= self.config.price_min:
            return self.config.max_position
        if price >= self.config.price_max:
            return 0
        
        return get_target_position_at_price(price, self.fib_levels)
    
    def find_nearest_fib_level(self, price: float) -> Tuple[int, float, float, int]:
        """
        找到最近的斐波那契价格点位
        
        Returns:
            (index, fib_level, fib_price, target_position)
        """
        for i, (level, fib_price, target_pos) in enumerate(self.fib_levels):
            if price <= fib_price:
                return i, level, fib_price, target_pos
        
        # 价格超过最高点
        last = self.fib_levels[-1]
        return len(self.fib_levels) - 1, last[0], last[1], last[2]
    
    def find_crossed_fib_level(
        self, 
        old_price: float, 
        new_price: float
    ) -> Optional[Tuple[int, float, float, int]]:
        """
        检查价格是否穿越了某个斐波那契点位
        
        Returns:
            如果穿越了，返回 (index, fib_level, fib_price, target_position)
            否则返回 None
        """
        for i, (level, fib_price, target_pos) in enumerate(self.fib_levels):
            # 下跌穿越：从上方跌破
            if old_price > fib_price >= new_price:
                return i, level, fib_price, target_pos
            # 上涨穿越：从下方突破
            if old_price < fib_price <= new_price:
                return i, level, fib_price, target_pos
        
        return None
    
    def generate_signal(
        self,
        current_price: float,
        current_position: int
    ) -> FibonacciSignal:
        """
        生成交易信号
        
        Args:
            current_price: 当前价格
            current_position: 当前持仓张数
            
        Returns:
            FibonacciSignal
        """
        # 检查价格范围
        if not self.is_price_in_range(current_price):
            return FibonacciSignal(
                action=TradeAction.HOLD,
                quantity=0,
                current_price=current_price,
                target_position=current_position,
                current_position=current_position,
                triggered_level=0,
                triggered_price=0,
                reason=f"价格 ${current_price:.2f} 超出范围 ${self.config.price_min}-${self.config.price_max}"
            )
        
        # 首次运行，记录价格
        if self.last_price == 0:
            self.last_price = current_price
            target_pos = self.calculate_target_position(current_price)
            
            # 首次运行，如果持仓不足，需要买入到目标持仓
            if current_position < target_pos:
                quantity = target_pos - current_position
                return FibonacciSignal(
                    action=TradeAction.BUY,
                    quantity=quantity,
                    current_price=current_price,
                    target_position=target_pos,
                    current_position=current_position,
                    triggered_level=0,
                    triggered_price=current_price,
                    reason=f"初始化：当前持仓 {current_position} 张，目标 {target_pos} 张"
                )
            elif current_position > target_pos:
                quantity = current_position - target_pos
                return FibonacciSignal(
                    action=TradeAction.SELL,
                    quantity=quantity,
                    current_price=current_price,
                    target_position=target_pos,
                    current_position=current_position,
                    triggered_level=0,
                    triggered_price=current_price,
                    reason=f"初始化：当前持仓 {current_position} 张，目标 {target_pos} 张"
                )
            else:
                return FibonacciSignal(
                    action=TradeAction.HOLD,
                    quantity=0,
                    current_price=current_price,
                    target_position=target_pos,
                    current_position=current_position,
                    triggered_level=0,
                    triggered_price=current_price,
                    reason="初始化：持仓已达目标"
                )
        
        # 检查是否穿越了斐波那契点位
        crossed = self.find_crossed_fib_level(self.last_price, current_price)
        
        if crossed is None:
            # 没有穿越任何点位，保持不动
            self.last_price = current_price
            return FibonacciSignal(
                action=TradeAction.HOLD,
                quantity=0,
                current_price=current_price,
                target_position=current_position,
                current_position=current_position,
                triggered_level=0,
                triggered_price=0,
                reason="未触发任何斐波那契点位"
            )
        
        # 穿越了斐波那契点位
        index, level, fib_price, target_pos = crossed
        self.last_price = current_price
        
        # 判断方向
        is_falling = current_price < self.last_price or current_price <= fib_price
        
        if is_falling:
            # 下跌，买入
            if current_position < target_pos:
                quantity = target_pos - current_position
                return FibonacciSignal(
                    action=TradeAction.BUY,
                    quantity=quantity,
                    current_price=current_price,
                    target_position=target_pos,
                    current_position=current_position,
                    triggered_level=level,
                    triggered_price=fib_price,
                    reason=f"跌破斐波那契 {level:.3f} (${fib_price:.2f})，买入 {quantity} 张"
                )
        else:
            # 上涨，卖出
            if current_position > target_pos:
                quantity = current_position - target_pos
                return FibonacciSignal(
                    action=TradeAction.SELL,
                    quantity=quantity,
                    current_price=current_price,
                    target_position=target_pos,
                    current_position=current_position,
                    triggered_level=level,
                    triggered_price=fib_price,
                    reason=f"突破斐波那契 {level:.3f} (${fib_price:.2f})，卖出 {quantity} 张"
                )
        
        # 持仓已达目标
        return FibonacciSignal(
            action=TradeAction.HOLD,
            quantity=0,
            current_price=current_price,
            target_position=target_pos,
            current_position=current_position,
            triggered_level=level,
            triggered_price=fib_price,
            reason=f"触发斐波那契 {level:.3f}，但持仓已达目标"
        )
    
    def get_status_summary(self, current_price: float, current_position: int) -> dict:
        """获取策略状态摘要"""
        target_pos = self.calculate_target_position(current_price)
        
        # 找到当前价格所在的斐波那契区间
        current_level_idx, current_level, current_fib_price, _ = self.find_nearest_fib_level(current_price)
        
        # 下一个买入点位（更低的价格）
        next_buy_price = None
        next_buy_target = None
        if current_level_idx > 0:
            _, _, next_buy_price, next_buy_target = self.fib_levels[current_level_idx - 1]
        
        # 下一个卖出点位（更高的价格）
        next_sell_price = None
        next_sell_target = None
        if current_level_idx < len(self.fib_levels) - 1:
            _, _, next_sell_price, next_sell_target = self.fib_levels[current_level_idx + 1]
        
        return {
            "current_price": current_price,
            "current_position": current_position,
            "target_position": target_pos,
            "position_diff": target_pos - current_position,
            "price_range": (self.config.price_min, self.config.price_max),
            "max_position": self.config.max_position,
            "current_fib_level": current_level,
            "current_fib_price": current_fib_price,
            "next_buy_price": next_buy_price,
            "next_buy_target": next_buy_target,
            "next_sell_price": next_sell_price,
            "next_sell_target": next_sell_target,
            "fib_levels": self.fib_levels
        }


# 测试代码
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    config = FibonacciConfig(
        price_min=100.0,
        price_max=160.0,
        max_position=40
    )
    
    engine = FibonacciStrategyEngine(config)
    
    print("\n=== 斐波那契网格点位 ===")
    for level, price, target in config.get_fib_prices():
        print(f"  {level:.3f} -> ${price:.2f} -> 目标持仓 {target} 张")
    
    print("\n=== 测试信号生成 ===")
    
    # 模拟价格变化
    test_cases = [
        (130.0, 0, "初始化，价格 $130，无持仓"),
        (130.0, 20, "价格 $130，持仓 20 张"),
        (125.0, 20, "价格跌到 $125"),
        (120.0, 25, "价格跌到 $120"),
        (115.0, 30, "价格跌到 $115"),
        (120.0, 32, "价格反弹到 $120"),
        (130.0, 28, "价格反弹到 $130"),
        (140.0, 20, "价格涨到 $140"),
    ]
    
    for price, position, desc in test_cases:
        signal = engine.generate_signal(price, position)
        print(f"\n{desc}")
        print(f"  价格: ${price:.2f}, 持仓: {position} 张")
        print(f"  信号: {signal.action.value}, 数量: {signal.quantity}")
        print(f"  目标持仓: {signal.target_position}, 原因: {signal.reason}")
