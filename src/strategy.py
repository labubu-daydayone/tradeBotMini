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
    HIGH = "high"  # >= 120
    LOW = "low"    # < 120


@dataclass
class TradeSignal:
    """交易信号"""
    action: str  # "open_long", "open_short", "close_long", "close_short", "hold"
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
        
    def get_price_zone(self, price: float) -> PriceZone:
        """判断价格所在区间"""
        if price >= self.config.price_threshold:
            return PriceZone.HIGH
        return PriceZone.LOW
    
    def calculate_profit_target(self, price: float) -> float:
        """
        计算目标利润百分比
        
        价格-利润线性关系：
        - 高价区间 (120-200): 价格越高，利润越低 (2.7% -> 2.3%)
        - 低价区间 (50-120): 价格越低，利润越高 (3.0% -> 4.5%)
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            目标利润百分比
        """
        zone = self.get_price_zone(price)
        
        if zone == PriceZone.HIGH:
            # 高价区间: 120-200
            # 价格 120 -> 利润 2.7%
            # 价格 200 -> 利润 2.3%
            # 线性递减
            min_price = self.config.high_price_range_min
            max_price = self.config.high_price_range_max
            min_profit = self.config.high_price_profit_min  # 2.3%
            max_profit = self.config.high_price_profit_max  # 2.7%
            
            # 限制价格范围
            clamped_price = max(min_price, min(price, max_price))
            
            # 线性插值: 价格越高，利润越低
            ratio = (clamped_price - min_price) / (max_price - min_price)
            profit = max_profit - ratio * (max_profit - min_profit)
            
        else:
            # 低价区间: 50-120
            # 价格 120 -> 利润 3.0%
            # 价格 50 -> 利润 4.5%
            # 线性递增（价格越低利润越高）
            min_price = self.config.low_price_range_min
            max_price = self.config.low_price_range_max
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
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            (合约总金额, 杠杆倍数)
        """
        zone = self.get_price_zone(price)
        capital = self.config.capital
        
        if zone == PriceZone.HIGH:
            # 价格 >= 120: 合约总金额 = 本金 * 110%
            contract_amount = capital * self.config.high_price_leverage_ratio
            # 计算杠杆: 合约金额 / 本金
            leverage = int(contract_amount / capital * 10)  # 基础杠杆
            leverage = max(1, min(leverage, 20))  # 限制在 1-20 倍
        else:
            # 价格 < 120: 合约总金额固定 1800 USDT
            contract_amount = self.config.low_price_contract_amount
            # 计算杠杆
            leverage = int(contract_amount / capital * 10)
            leverage = max(1, min(leverage, 50))  # 低价区间允许更高杠杆
        
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
        # 合约张数 = 合约金额 / 价格
        # 注意: OKX 合约面值为 1 SOL
        position_size = contract_amount / price
        return round(position_size, 2)
    
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
    
    def get_strategy_summary(self, price: float) -> dict:
        """
        获取当前价格下的策略摘要
        
        Args:
            price: 当前 SOL 价格
            
        Returns:
            策略参数字典
        """
        zone = self.get_price_zone(price)
        profit_target = self.calculate_profit_target(price)
        contract_amount, leverage = self.calculate_contract_amount(price)
        position_size = self.calculate_position_size(price, contract_amount)
        
        # 假设做多
        tp_price_long = self.calculate_take_profit_price(price, is_long=True)
        # 假设做空
        tp_price_short = self.calculate_take_profit_price(price, is_long=False)
        
        return {
            "current_price": price,
            "price_zone": zone.value,
            "profit_target_pct": profit_target,
            "contract_amount_usdt": contract_amount,
            "leverage": leverage,
            "position_size": position_size,
            "take_profit_long": tp_price_long,
            "take_profit_short": tp_price_short,
            "capital": self.config.capital
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
    print(f"价格区间: {summary['price_zone'].upper()}")
    print(f"目标利润: {summary['profit_target_pct']:.2f}%")
    print(f"合约金额: {summary['contract_amount_usdt']:.2f} USDT")
    print(f"杠杆倍数: {summary['leverage']}x")
    print(f"开仓张数: {summary['position_size']:.2f}")
    print(f"做多止盈: ${summary['take_profit_long']:.2f}")
    print(f"做空止盈: ${summary['take_profit_short']:.2f}")
    print("=" * 60)


if __name__ == "__main__":
    # 测试不同价格下的策略参数
    logging.basicConfig(level=logging.DEBUG)
    
    config = TradingStrategy(capital=1000.0)
    
    test_prices = [50, 80, 100, 120, 150, 180, 200]
    
    print("\n策略参数测试:")
    for price in test_prices:
        print_strategy_table(price, config)
