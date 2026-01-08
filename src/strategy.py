"""
SOL 全仓合约交易策略模块
实现价格-利润线性关系和动态杠杆策略
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum
from datetime import datetime

from config import TradingStrategy


class PriceZone(Enum):
    """价格区间"""
    HIGH = "high"      # 120 - 150
    LOW = "low"        # 90 - 120
    UNSAFE = "unsafe"  # < 90 或 > 150 (不交易)


@dataclass
class TradeSignal:
    """交易信号"""
    action: str  # "open_long", "open_short", "close_long", "close_short", "hold", "stop"
    price: float
    target_profit_pct: float
    contract_amount: float
    leverage: int
    take_profit_price: float
    reason: str
    timestamp: datetime


class TradingStrategyEngine:
    """交易策略引擎"""
    
    def __init__(self, config: TradingStrategy):
        self.config = config
        self.logger = logging.getLogger(__name__)
        
    def is_price_safe(self, price: float) -> bool:
        """
        检查价格是否在安全交易范围内
        
        安全范围: $90 - $150
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            True 如果价格在安全范围内，False 否则
        """
        return self.config.safe_price_min <= price <= self.config.safe_price_max
    
    def get_price_zone(self, price: float) -> PriceZone:
        """
        判断价格所在区间
        
        - UNSAFE: < $90 或 > $150 (停止交易)
        - LOW: $90 - $120 (低价区间)
        - HIGH: $120 - $150 (高价区间)
        """
        if not self.is_price_safe(price):
            return PriceZone.UNSAFE
        
        if price >= self.config.price_threshold:
            return PriceZone.HIGH
        return PriceZone.LOW
    
    def calculate_profit_target(self, price: float) -> float:
        """
        计算目标利润百分比
        
        价格-利润线性关系：
        - 高价区间 (120-150): 价格越高，利润越低 (2.7% -> 2.3%)
        - 低价区间 (90-120): 价格越低，利润越高 (3.0% -> 4.5%)
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            目标利润百分比，如果价格不安全返回 0
        """
        zone = self.get_price_zone(price)
        
        if zone == PriceZone.UNSAFE:
            self.logger.warning(f"价格 ${price:.2f} 超出安全范围，不进行交易")
            return 0.0
        
        if zone == PriceZone.HIGH:
            # 高价区间: 120-150
            # 价格 120 -> 利润 2.7%
            # 价格 150 -> 利润 2.3%
            # 线性递减
            min_price = self.config.high_price_range_min  # 120
            max_price = self.config.high_price_range_max  # 150
            min_profit = self.config.high_price_profit_min  # 2.3%
            max_profit = self.config.high_price_profit_max  # 2.7%
            
            # 限制价格范围
            clamped_price = max(min_price, min(price, max_price))
            
            # 线性插值: 价格越高，利润越低
            ratio = (clamped_price - min_price) / (max_price - min_price)
            profit = max_profit - ratio * (max_profit - min_profit)
            
        else:
            # 低价区间: 90-120
            # 价格 120 -> 利润 3.0%
            # 价格 90 -> 利润 4.5%
            # 线性递增（价格越低利润越高）
            min_price = self.config.low_price_range_min  # 90
            max_price = self.config.low_price_range_max  # 120
            min_profit = self.config.low_price_profit_min  # 3.0%
            max_profit = self.config.low_price_profit_max  # 4.5%
            
            # 限制价格范围
            clamped_price = max(min_price, min(price, max_price))
            
            # 线性插值: 价格越低，利润越高
            ratio = (max_price - clamped_price) / (max_price - min_price)
            profit = min_profit + ratio * (max_profit - min_profit)
        
        self.logger.debug(f"价格 {price:.2f} 在 {zone.value} 区间，目标利润: {profit:.2f}%")
        return round(profit, 2)
    
    def calculate_contract_amount(self, price: float) -> Tuple[float, int]:
        """
        计算合约金额和杠杆倍数
        
        合约金额计算规则：
        - 价格 >= 120: 合约总金额 = 本金 * 1.1 倍
        - 价格 < 120: 合约总金额 = 本金 * 1.8 倍
        - 价格超出安全范围: 返回 0
        
        测试模式下使用固定金额
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            (合约总金额, 杠杆倍数)
        """
        zone = self.get_price_zone(price)
        capital = self.config.capital
        leverage = self.config.default_leverage  # 固定2倍杠杆
        
        if zone == PriceZone.UNSAFE:
            self.logger.warning(f"价格 ${price:.2f} 超出安全范围，不进行交易")
            return 0.0, leverage
        
        if self.config.test_mode:
            # 测试模式：使用固定金额
            if zone == PriceZone.HIGH:
                contract_amount = self.config.test_high_price_amount
            else:
                contract_amount = self.config.test_low_price_amount
        else:
            # 正常模式：按本金比例计算
            if zone == PriceZone.HIGH:
                # 价格 >= 120: 合约总金额 = 本金 * 1.1
                contract_amount = capital * self.config.high_price_leverage_ratio
            else:
                # 价格 < 120: 合约总金额 = 本金 * 1.8
                contract_amount = capital * self.config.low_price_leverage_ratio
        
        self.logger.debug(
            f"价格 {price:.2f} 在 {zone.value} 区间，"
            f"合约金额: {contract_amount:.2f} USDT，杠杆: {leverage}x"
        )
        return contract_amount, leverage
    
    def calculate_take_profit_price(self, entry_price: float, is_long: bool) -> float:
        """
        计算止盈价格
        
        Args:
            entry_price: 开仓价格
            is_long: 是否做多
            
        Returns:
            止盈价格
        """
        profit_pct = self.calculate_profit_target(entry_price) / 100
        
        if profit_pct == 0:
            return 0.0
        
        if is_long:
            # 做多: 止盈价 = 开仓价 * (1 + 利润率)
            take_profit = entry_price * (1 + profit_pct)
        else:
            # 做空: 止盈价 = 开仓价 * (1 - 利润率)
            take_profit = entry_price * (1 - profit_pct)
        
        return round(take_profit, 2)
    
    def calculate_position_size(self, price: float, contract_amount: float) -> float:
        """
        计算开仓数量（张数）
        
        OKX SOL-USDT-SWAP 合约面值: 1 SOL
        
        Args:
            price: 当前价格
            contract_amount: 合约总金额 (USDT)
            
        Returns:
            开仓张数
        """
        if contract_amount == 0 or price == 0:
            return 0.0
        # 合约张数 = 合约金额 / 价格
        # 注意: OKX 合约面值为 1 SOL
        position_size = contract_amount / price
        return round(position_size, 2)
    
    def calculate_total_contract_value(self, price: float, position_size: float) -> float:
        """
        计算合约总金额（价格 × 张数）
        
        Args:
            price: 当前价格
            position_size: 持仓张数
            
        Returns:
            合约总金额 (USDT)
        """
        return round(price * position_size, 2)
    
    def calculate_pnl(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        is_long: bool
    ) -> Tuple[float, float]:
        """
        计算盈亏
        
        Args:
            entry_price: 开仓价格
            exit_price: 平仓价格
            position_size: 持仓数量
            is_long: 是否做多
            
        Returns:
            (盈亏金额 USDT, 盈亏百分比)
        """
        if is_long:
            pnl = (exit_price - entry_price) * position_size
        else:
            pnl = (entry_price - exit_price) * position_size
        
        # 盈亏百分比 = 盈亏 / (开仓价 * 持仓数量) * 100
        investment = entry_price * position_size
        pnl_pct = (pnl / investment) * 100 if investment > 0 else 0
        
        return round(pnl, 2), round(pnl_pct, 2)
    
    def can_trade(self, price: float) -> Tuple[bool, str]:
        """
        检查是否可以交易
        
        Args:
            price: 当前价格
            
        Returns:
            (是否可交易, 原因)
        """
        if price < self.config.safe_price_min:
            return False, f"价格 ${price:.2f} 低于安全下限 ${self.config.safe_price_min:.2f}，停止交易"
        
        if price > self.config.safe_price_max:
            return False, f"价格 ${price:.2f} 高于安全上限 ${self.config.safe_price_max:.2f}，停止交易"
        
        return True, "价格在安全范围内"
    
    def get_strategy_summary(self, price: float) -> dict:
        """
        获取当前价格下的策略摘要
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            策略参数字典
        """
        zone = self.get_price_zone(price)
        can_trade, trade_reason = self.can_trade(price)
        
        if not can_trade:
            return {
                "current_price": price,
                "price_zone": zone.value,
                "can_trade": False,
                "trade_reason": trade_reason,
                "profit_target_pct": 0,
                "contract_amount_usdt": 0,
                "total_contract_value": 0,
                "leverage": self.config.default_leverage,
                "position_size": 0,
                "take_profit_long": 0,
                "take_profit_short": 0,
                "capital": self.config.capital,
                "test_mode": self.config.test_mode,
                "safe_price_min": self.config.safe_price_min,
                "safe_price_max": self.config.safe_price_max
            }
        
        profit_target = self.calculate_profit_target(price)
        contract_amount, leverage = self.calculate_contract_amount(price)
        position_size = self.calculate_position_size(price, contract_amount)
        
        # 计算合约总金额（价格 × 张数）
        total_contract_value = self.calculate_total_contract_value(price, position_size)
        
        # 假设做多
        tp_price_long = self.calculate_take_profit_price(price, is_long=True)
        # 假设做空
        tp_price_short = self.calculate_take_profit_price(price, is_long=False)
        
        return {
            "current_price": price,
            "price_zone": zone.value,
            "can_trade": True,
            "trade_reason": trade_reason,
            "profit_target_pct": profit_target,
            "contract_amount_usdt": contract_amount,  # 目标合约金额
            "total_contract_value": total_contract_value,  # 实际合约总金额 (价格 × 张数)
            "leverage": leverage,
            "position_size": position_size,
            "take_profit_long": tp_price_long,
            "take_profit_short": tp_price_short,
            "capital": self.config.capital,
            "test_mode": self.config.test_mode,
            "safe_price_min": self.config.safe_price_min,
            "safe_price_max": self.config.safe_price_max
        }


class TradeTracker:
    """交易追踪器"""
    
    def __init__(self):
        self.trades = []
        self.total_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0
        
    def record_trade(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        is_long: bool,
        pnl: float,
        pnl_pct: float,
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
            "pnl_pct": pnl_pct
        }
        self.trades.append(trade)
        self.total_pnl += pnl
        
        if pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1
    
    def get_statistics(self) -> dict:
        """获取交易统计"""
        total_trades = len(self.trades)
        win_rate = (self.win_count / total_trades * 100) if total_trades > 0 else 0
        
        return {
            "total_trades": total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(self.total_pnl, 2)
        }
    
    def get_last_trade(self) -> Optional[dict]:
        """获取最近一笔交易"""
        return self.trades[-1] if self.trades else None


def print_strategy_table(price: float, config: TradingStrategy = None):
    """打印策略参数表格"""
    if config is None:
        config = TradingStrategy()
    
    engine = TradingStrategyEngine(config)
    summary = engine.get_strategy_summary(price)
    
    print("\n" + "=" * 60)
    print(f"SOL 价格: ${price:.2f}")
    print("=" * 60)
    print(f"安全价格范围: ${summary['safe_price_min']:.0f} - ${summary['safe_price_max']:.0f}")
    print(f"价格区间: {summary['price_zone'].upper()}")
    print(f"可交易: {'是' if summary['can_trade'] else '否'}")
    
    if not summary['can_trade']:
        print(f"原因: {summary['trade_reason']}")
    else:
        print(f"目标利润: {summary['profit_target_pct']:.2f}%")
        print(f"目标合约金额: {summary['contract_amount_usdt']:.2f} USDT")
        print(f"合约总金额 (价格×张数): {summary['total_contract_value']:.2f} USDT")
        print(f"杠杆倍数: {summary['leverage']}x")
        print(f"开仓张数: {summary['position_size']:.2f}")
        print(f"做多止盈: ${summary['take_profit_long']:.2f}")
        print(f"做空止盈: ${summary['take_profit_short']:.2f}")
    
    print(f"测试模式: {'是' if summary['test_mode'] else '否'}")
    print("=" * 60)


if __name__ == "__main__":
    # 测试不同价格下的策略参数
    logging.basicConfig(level=logging.DEBUG)
    
    # 测试正常模式
    print("\n=== 策略参数测试 (本金 1000 USDT) ===")
    print("安全价格范围: $90 - $150")
    print("低价区间: $90 - $120 (1.8x, 利润 3.0%-4.5%)")
    print("高价区间: $120 - $150 (1.1x, 利润 2.3%-2.7%)")
    
    config = TradingStrategy(capital=1000.0, test_mode=False)
    
    # 测试各种价格
    test_prices = [80, 90, 100, 110, 120, 130, 140, 150, 160]
    
    for price in test_prices:
        print_strategy_table(price, config)
