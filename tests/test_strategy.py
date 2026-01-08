"""
交易策略测试
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from strategy import TradingStrategyEngine, PriceZone
from config import TradingStrategy


def test_price_zone():
    """测试价格区间判断"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    # 测试高价区间
    assert engine.get_price_zone(120) == PriceZone.HIGH
    assert engine.get_price_zone(150) == PriceZone.HIGH
    assert engine.get_price_zone(200) == PriceZone.HIGH
    
    # 测试低价区间
    assert engine.get_price_zone(119.99) == PriceZone.LOW
    assert engine.get_price_zone(100) == PriceZone.LOW
    assert engine.get_price_zone(50) == PriceZone.LOW
    
    print("✓ 价格区间测试通过")


def test_profit_target():
    """测试利润目标计算"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    # 高价区间测试
    # 价格 120 -> 利润 2.7%
    profit_120 = engine.calculate_profit_target(120)
    assert 2.6 <= profit_120 <= 2.8, f"价格 120 利润应为 ~2.7%, 实际: {profit_120}"
    
    # 价格 200 -> 利润 2.3%
    profit_200 = engine.calculate_profit_target(200)
    assert 2.2 <= profit_200 <= 2.4, f"价格 200 利润应为 ~2.3%, 实际: {profit_200}"
    
    # 低价区间测试
    # 价格 119 -> 利润 ~3.0%
    profit_119 = engine.calculate_profit_target(119)
    assert 2.9 <= profit_119 <= 3.1, f"价格 119 利润应为 ~3.0%, 实际: {profit_119}"
    
    # 价格 50 -> 利润 4.5%
    profit_50 = engine.calculate_profit_target(50)
    assert 4.4 <= profit_50 <= 4.6, f"价格 50 利润应为 ~4.5%, 实际: {profit_50}"
    
    # 验证线性关系：价格越低利润越高
    assert profit_50 > profit_119, "低价区间：价格越低利润应越高"
    assert profit_120 > profit_200, "高价区间：价格越低利润应越高"
    
    print("✓ 利润目标测试通过")


def test_contract_amount():
    """测试合约金额计算"""
    config = TradingStrategy(capital=1000.0)
    engine = TradingStrategyEngine(config)
    
    # 高价区间: 合约金额 = 本金 * 110%
    amount_high, leverage_high = engine.calculate_contract_amount(150)
    assert amount_high == 1100.0, f"高价区间合约金额应为 1100, 实际: {amount_high}"
    
    # 低价区间: 合约金额 = 1800
    amount_low, leverage_low = engine.calculate_contract_amount(100)
    assert amount_low == 1800.0, f"低价区间合约金额应为 1800, 实际: {amount_low}"
    
    print("✓ 合约金额测试通过")


def test_take_profit_price():
    """测试止盈价格计算"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    entry_price = 125.0
    
    # 做多止盈
    tp_long = engine.calculate_take_profit_price(entry_price, is_long=True)
    assert tp_long > entry_price, "做多止盈价应高于开仓价"
    
    # 做空止盈
    tp_short = engine.calculate_take_profit_price(entry_price, is_long=False)
    assert tp_short < entry_price, "做空止盈价应低于开仓价"
    
    print("✓ 止盈价格测试通过")


def test_pnl_calculation():
    """测试盈亏计算"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    # 做多盈利
    pnl, pnl_pct = engine.calculate_pnl(
        entry_price=100,
        exit_price=105,
        position_size=10,
        is_long=True
    )
    assert pnl == 50.0, f"做多盈利应为 50, 实际: {pnl}"
    assert pnl_pct == 5.0, f"做多盈利率应为 5%, 实际: {pnl_pct}"
    
    # 做多亏损
    pnl, pnl_pct = engine.calculate_pnl(
        entry_price=100,
        exit_price=95,
        position_size=10,
        is_long=True
    )
    assert pnl == -50.0, f"做多亏损应为 -50, 实际: {pnl}"
    
    # 做空盈利
    pnl, pnl_pct = engine.calculate_pnl(
        entry_price=100,
        exit_price=95,
        position_size=10,
        is_long=False
    )
    assert pnl == 50.0, f"做空盈利应为 50, 实际: {pnl}"
    
    print("✓ 盈亏计算测试通过")


def test_strategy_summary():
    """测试策略摘要"""
    config = TradingStrategy(capital=1000.0)
    engine = TradingStrategyEngine(config)
    
    # 测试不同价格的策略摘要
    test_prices = [50, 80, 100, 120, 150, 180, 200]
    
    print("\n策略参数汇总表:")
    print("-" * 80)
    print(f"{'价格':>8} | {'区间':>6} | {'利润%':>6} | {'合约金额':>10} | {'杠杆':>4} | {'做多止盈':>10} | {'做空止盈':>10}")
    print("-" * 80)
    
    for price in test_prices:
        summary = engine.get_strategy_summary(price)
        print(
            f"${price:>6.0f} | "
            f"{summary['price_zone']:>6} | "
            f"{summary['profit_target_pct']:>5.2f}% | "
            f"${summary['contract_amount_usdt']:>8.0f} | "
            f"{summary['leverage']:>3}x | "
            f"${summary['take_profit_long']:>8.2f} | "
            f"${summary['take_profit_short']:>8.2f}"
        )
    
    print("-" * 80)
    print("✓ 策略摘要测试通过")


if __name__ == "__main__":
    print("=" * 60)
    print("运行交易策略测试")
    print("=" * 60)
    
    test_price_zone()
    test_profit_target()
    test_contract_amount()
    test_take_profit_price()
    test_pnl_calculation()
    test_strategy_summary()
    
    print("\n" + "=" * 60)
    print("所有测试通过! ✓")
    print("=" * 60)
