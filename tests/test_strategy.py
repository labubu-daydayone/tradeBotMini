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
    
    # 测试不安全区间 (< 90)
    assert engine.get_price_zone(80) == PriceZone.UNSAFE
    assert engine.get_price_zone(89.99) == PriceZone.UNSAFE
    
    # 测试低价区间 (90-120)
    assert engine.get_price_zone(90) == PriceZone.LOW
    assert engine.get_price_zone(100) == PriceZone.LOW
    assert engine.get_price_zone(119.99) == PriceZone.LOW
    
    # 测试高价区间 (120-150)
    assert engine.get_price_zone(120) == PriceZone.HIGH
    assert engine.get_price_zone(135) == PriceZone.HIGH
    assert engine.get_price_zone(150) == PriceZone.HIGH
    
    # 测试不安全区间 (> 150)
    assert engine.get_price_zone(150.01) == PriceZone.UNSAFE
    assert engine.get_price_zone(200) == PriceZone.UNSAFE
    
    print("✓ 价格区间测试通过")


def test_price_safety():
    """测试价格安全检查"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    # 不安全价格
    assert engine.is_price_safe(80) == False
    assert engine.is_price_safe(89) == False
    assert engine.is_price_safe(151) == False
    assert engine.is_price_safe(200) == False
    
    # 安全价格
    assert engine.is_price_safe(90) == True
    assert engine.is_price_safe(100) == True
    assert engine.is_price_safe(120) == True
    assert engine.is_price_safe(150) == True
    
    print("✓ 价格安全检查测试通过")


def test_can_trade():
    """测试交易许可检查"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    # 可交易
    can_trade, reason = engine.can_trade(100)
    assert can_trade == True
    
    can_trade, reason = engine.can_trade(130)
    assert can_trade == True
    
    # 不可交易 - 价格过低
    can_trade, reason = engine.can_trade(80)
    assert can_trade == False
    assert "低于安全下限" in reason
    
    # 不可交易 - 价格过高
    can_trade, reason = engine.can_trade(160)
    assert can_trade == False
    assert "高于安全上限" in reason
    
    print("✓ 交易许可检查测试通过")


def test_profit_target():
    """测试利润目标计算"""
    config = TradingStrategy()
    engine = TradingStrategyEngine(config)
    
    # 高价区间测试 (120-150)
    # 价格 120 -> 利润 2.7%
    profit_120 = engine.calculate_profit_target(120)
    assert 2.6 <= profit_120 <= 2.8, f"价格 120 利润应为 ~2.7%, 实际: {profit_120}"
    
    # 价格 150 -> 利润 2.3%
    profit_150 = engine.calculate_profit_target(150)
    assert 2.2 <= profit_150 <= 2.4, f"价格 150 利润应为 ~2.3%, 实际: {profit_150}"
    
    # 低价区间测试 (90-120)
    # 价格 119 -> 利润 ~3.0%
    profit_119 = engine.calculate_profit_target(119)
    assert 2.9 <= profit_119 <= 3.1, f"价格 119 利润应为 ~3.0%, 实际: {profit_119}"
    
    # 价格 90 -> 利润 4.5%
    profit_90 = engine.calculate_profit_target(90)
    assert 4.4 <= profit_90 <= 4.6, f"价格 90 利润应为 ~4.5%, 实际: {profit_90}"
    
    # 不安全价格返回 0
    profit_80 = engine.calculate_profit_target(80)
    assert profit_80 == 0, f"不安全价格利润应为 0, 实际: {profit_80}"
    
    profit_160 = engine.calculate_profit_target(160)
    assert profit_160 == 0, f"不安全价格利润应为 0, 实际: {profit_160}"
    
    # 验证线性关系：价格越低利润越高
    assert profit_90 > profit_119, "低价区间：价格越低利润应越高"
    assert profit_120 > profit_150, "高价区间：价格越低利润应越高"
    
    print("✓ 利润目标测试通过")


def test_contract_amount():
    """测试合约金额计算"""
    config = TradingStrategy(capital=1000.0)
    engine = TradingStrategyEngine(config)
    
    # 高价区间: 合约金额 = 本金 * 1.1
    amount_high, leverage_high = engine.calculate_contract_amount(130)
    assert amount_high == 1100.0, f"高价区间合约金额应为 1100, 实际: {amount_high}"
    assert leverage_high == 2, f"杠杆应为 2, 实际: {leverage_high}"
    
    # 低价区间: 合约金额 = 本金 * 1.8
    amount_low, leverage_low = engine.calculate_contract_amount(100)
    assert amount_low == 1800.0, f"低价区间合约金额应为 1800, 实际: {amount_low}"
    assert leverage_low == 2, f"杠杆应为 2, 实际: {leverage_low}"
    
    # 不安全价格: 合约金额 = 0
    amount_unsafe, _ = engine.calculate_contract_amount(80)
    assert amount_unsafe == 0, f"不安全价格合约金额应为 0, 实际: {amount_unsafe}"
    
    print("✓ 合约金额测试通过")


def test_total_contract_value():
    """测试合约总金额计算（价格 × 张数）"""
    config = TradingStrategy(capital=1000.0)
    engine = TradingStrategyEngine(config)
    
    # 测试: 价格 120, 张数 5 -> 合约总金额 600
    total = engine.calculate_total_contract_value(120, 5)
    assert total == 600.0, f"合约总金额应为 600, 实际: {total}"
    
    # 测试: 价格 100, 张数 10 -> 合约总金额 1000
    total = engine.calculate_total_contract_value(100, 10)
    assert total == 1000.0, f"合约总金额应为 1000, 实际: {total}"
    
    print("✓ 合约总金额测试通过")


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
    
    # 不安全价格返回 0
    tp_unsafe = engine.calculate_take_profit_price(80, is_long=True)
    assert tp_unsafe == 0, "不安全价格止盈应为 0"
    
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
    test_prices = [80, 90, 100, 110, 120, 130, 140, 150, 160]
    
    print("\n策略参数汇总表:")
    print("-" * 100)
    print(f"{'价格':>8} | {'区间':>8} | {'可交易':>6} | {'利润%':>6} | {'合约金额':>10} | {'张数':>6} | {'做多止盈':>10} | {'做空止盈':>10}")
    print("-" * 100)
    
    for price in test_prices:
        summary = engine.get_strategy_summary(price)
        can_trade = "是" if summary['can_trade'] else "否"
        
        if summary['can_trade']:
            print(
                f"${price:>6.0f} | "
                f"{summary['price_zone']:>8} | "
                f"{can_trade:>6} | "
                f"{summary['profit_target_pct']:>5.2f}% | "
                f"${summary['total_contract_value']:>8.0f} | "
                f"{summary['position_size']:>5.2f} | "
                f"${summary['take_profit_long']:>8.2f} | "
                f"${summary['take_profit_short']:>8.2f}"
            )
        else:
            print(
                f"${price:>6.0f} | "
                f"{summary['price_zone']:>8} | "
                f"{can_trade:>6} | "
                f"{'N/A':>6} | "
                f"{'N/A':>10} | "
                f"{'N/A':>6} | "
                f"{'N/A':>10} | "
                f"{'N/A':>10}"
            )
    
    print("-" * 100)
    print("✓ 策略摘要测试通过")


if __name__ == "__main__":
    print("=" * 60)
    print("运行交易策略测试")
    print("=" * 60)
    print("安全价格范围: $90 - $150")
    print("低价区间: $90 - $120 (1.8x, 利润 3.0%-4.5%)")
    print("高价区间: $120 - $150 (1.1x, 利润 2.3%-2.7%)")
    print("=" * 60)
    
    test_price_zone()
    test_price_safety()
    test_can_trade()
    test_profit_target()
    test_contract_amount()
    test_total_contract_value()
    test_take_profit_price()
    test_pnl_calculation()
    test_strategy_summary()
    
    print("\n" + "=" * 60)
    print("所有测试通过! ✓")
    print("=" * 60)
