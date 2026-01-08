"""
OKX SOL 全仓合约交易机器人
主程序入口 - 支持网格分批买入策略
"""
import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime
from typing import Optional

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import AppConfig, get_config
from okx_client import OKXClient, TickerInfo, PositionInfo
from strategy import (
    TradingStrategyEngine, TradeTracker, PriceZone, 
    DropType, GridBuySignal, GridSellSignal
)
from telegram_notifier import TelegramNotifier


class TradingBot:
    """交易机器人主类"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False
        
        # 初始化日志
        self._setup_logging()
        
        # 初始化组件
        self.okx_client = OKXClient(config.okx)
        self.strategy = TradingStrategyEngine(config.strategy)
        self.notifier = TelegramNotifier(config.telegram)
        self.tracker = TradeTracker()
        
        # 当前状态
        self.current_position: Optional[PositionInfo] = None
        self.last_price: float = 0.0
        self.last_zone: Optional[PriceZone] = None
        self.last_safe_status: Optional[bool] = None
        
        self.logger.info("交易机器人初始化完成")
        self.logger.info(f"模式: {'测试网(模拟盘)' if config.okx.use_testnet else '正式网(实盘)'}")
        self.logger.info(f"交易对: {config.strategy.symbol}")
        self.logger.info(f"本金: {config.strategy.capital} USDT")
        self.logger.info(f"默认杠杆: {config.strategy.default_leverage}x")
        self.logger.info(f"安全价格范围: ${config.strategy.safe_price_min:.0f} - ${config.strategy.safe_price_max:.0f}")
        
        # 打印网格配置
        grid = config.strategy.grid
        self.logger.info("=== 网格交易配置 ===")
        self.logger.info(f"正常跌幅: ${grid.normal_drop_min}-${grid.normal_drop_max}")
        self.logger.info(f"大跌幅: ${grid.large_drop}+")
        self.logger.info(f"高价区间买入: 正常 {grid.high_price_normal_qty} 张, 大跌 {grid.high_price_large_qty} 张")
        self.logger.info(f"低价区间买入: 正常 {grid.low_price_normal_qty} 张, 大跌 {grid.low_price_large_qty} 张")
        self.logger.info(f"保留张数: {grid.reserve_qty} 张 (涨 ${grid.reserve_profit_target} 后卖出)")
        
    def _setup_logging(self):
        """配置日志"""
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        log_level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(log_format))
        
        file_handler = logging.FileHandler(self.config.log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[console_handler, file_handler]
        )
        
        self.logger = logging.getLogger(__name__)
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        self.logger.info("收到停止信号，正在关闭...")
        self.running = False
    
    def get_current_price(self) -> Optional[float]:
        """获取当前 SOL 价格"""
        try:
            ticker_data = self.okx_client.get_ticker(self.config.strategy.symbol)
            ticker = TickerInfo.from_response(ticker_data)
            if ticker:
                return ticker.last_price
            else:
                self.logger.error(f"获取行情失败: {ticker_data}")
                return None
        except Exception as e:
            self.logger.error(f"获取价格异常: {e}")
            return None
    
    def get_current_position(self) -> Optional[PositionInfo]:
        """获取当前持仓"""
        try:
            pos_data = self.okx_client.get_positions(
                inst_type="SWAP",
                inst_id=self.config.strategy.symbol
            )
            positions = PositionInfo.from_response(pos_data)
            return positions[0] if positions else None
        except Exception as e:
            self.logger.error(f"获取持仓异常: {e}")
            return None
    
    def setup_leverage(self, leverage: int) -> bool:
        """设置杠杆倍数"""
        try:
            result = self.okx_client.set_leverage(
                inst_id=self.config.strategy.symbol,
                lever=leverage,
                mgn_mode=self.config.strategy.margin_mode
            )
            if result.get("code") == "0":
                self.logger.info(f"杠杆设置成功: {leverage}x")
                return True
            else:
                self.logger.error(f"杠杆设置失败: {result}")
                return False
        except Exception as e:
            self.logger.error(f"设置杠杆异常: {e}")
            return False
    
    def execute_grid_buy(self, signal: GridBuySignal, current_price: float) -> bool:
        """
        执行网格买入
        
        Args:
            signal: 买入信号
            current_price: 当前价格
        """
        if not signal.should_buy:
            return False
        
        quantity = signal.quantity
        total_value = current_price * quantity
        
        # 获取当前持仓
        position = self.get_current_position()
        current_qty = abs(position.pos) if position else 0
        current_value = current_qty * current_price
        
        # 获取最大限额
        max_amount = self.strategy.get_max_contract_amount(current_price)
        remaining = max(0, max_amount - current_value)
        
        # 设置杠杆
        leverage = self.config.strategy.default_leverage
        if not self.setup_leverage(leverage):
            self.logger.error("设置杠杆失败，取消买入")
            return False
        
        # 下单
        try:
            # 单向持仓模式不需要 pos_side 参数
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode=self.config.strategy.margin_mode,
                side="buy",
                order_type="market",
                sz=str(quantity)
            )
            
            if result.get("code") == "0":
                self.logger.info(
                    f"网格买入成功: {quantity} 张 @ ${current_price:.2f}, "
                    f"跌幅 ${signal.drop_amount:.2f} ({signal.drop_type.value})"
                )
                
                # 更新上次买入价格
                self.strategy.update_last_buy_price(current_price)
                
                # 发送 Telegram 通知
                new_qty = current_qty + quantity
                new_value = new_qty * current_price
                
                self.notifier.send_grid_buy_notification(
                    symbol=self.config.strategy.symbol,
                    direction="LONG",
                    entry_price=current_price,
                    quantity=quantity,
                    total_contract_value=total_value,
                    drop_amount=signal.drop_amount,
                    drop_type=signal.drop_type.value,
                    current_position_qty=new_qty,
                    current_position_value=new_value,
                    max_amount=max_amount,
                    remaining_amount=max(0, max_amount - new_value)
                )
                return True
            else:
                self.logger.error(f"网格买入失败: {result}")
                self.notifier.send_error_notification(f"网格买入失败: {result.get('msg', 'Unknown error')}")
                return False
                
        except Exception as e:
            self.logger.error(f"网格买入异常: {e}")
            self.notifier.send_error_notification(f"网格买入异常: {str(e)}")
            return False
    
    def execute_grid_sell(self, signal: GridSellSignal, position: PositionInfo) -> bool:
        """
        执行网格卖出
        
        Args:
            signal: 卖出信号
            position: 当前持仓
        """
        if not signal.should_sell:
            return False
        
        sell_qty = signal.sell_quantity
        reserve_qty = signal.reserve_quantity
        
        try:
            # 部分平仓（单向持仓模式不需要 pos_side 参数）
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode=self.config.strategy.margin_mode,
                side="sell",
                order_type="market",
                sz=str(sell_qty),
                reduce_only=True
            )
            
            if result.get("code") == "0":
                exit_price = self.get_current_price() or position.avg_px
                total_value = exit_price * sell_qty
                
                # 计算盈亏
                pnl, pnl_pct = self.strategy.calculate_pnl(
                    entry_price=position.avg_px,
                    exit_price=exit_price,
                    position_size=sell_qty,
                    is_long=True
                )
                
                # 记录交易
                self.tracker.record_trade(
                    entry_price=position.avg_px,
                    exit_price=exit_price,
                    position_size=sell_qty,
                    is_long=True,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    is_reserve=signal.is_reserve_sell
                )
                
                # 如果不是保留仓位卖出，记录保留仓位
                if not signal.is_reserve_sell and reserve_qty > 0:
                    self.tracker.add_reserved_position(position.avg_px, reserve_qty)
                
                self.logger.info(
                    f"网格卖出成功: {sell_qty} 张 @ ${exit_price:.2f}, "
                    f"盈亏 ${pnl:.2f} ({pnl_pct:+.2f}%), "
                    f"保留 {reserve_qty} 张"
                )
                
                # 发送 Telegram 通知
                stats = self.tracker.get_statistics()
                self.notifier.send_grid_sell_notification(
                    symbol=self.config.strategy.symbol,
                    direction="LONG",
                    entry_price=position.avg_px,
                    exit_price=exit_price,
                    sell_quantity=sell_qty,
                    reserve_quantity=reserve_qty,
                    total_contract_value=total_value,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    is_reserve_sell=signal.is_reserve_sell,
                    total_pnl=stats["total_pnl"]
                )
                
                return True
            else:
                self.logger.error(f"网格卖出失败: {result}")
                return False
                
        except Exception as e:
            self.logger.error(f"网格卖出异常: {e}")
            return False
    
    def check_position_limit_and_notify(
        self,
        current_price: float,
        current_qty: float,
        buy_qty: int
    ) -> bool:
        """检查本金限制并发送通知"""
        can_buy, reason = self.strategy.check_position_limit(
            current_price, current_qty, buy_qty
        )
        
        if not can_buy:
            zone = self.strategy.get_price_zone(current_price)
            max_amount = self.strategy.get_max_contract_amount(current_price)
            current_value = current_qty * current_price
            requested_amount = buy_qty * current_price
            
            self.logger.warning(f"本金限制: {reason}")
            self.notifier.send_position_limit_warning(
                current_price=current_price,
                current_position_value=current_value,
                requested_amount=requested_amount,
                max_amount=max_amount,
                zone=zone.value
            )
        
        return can_buy
    
    def check_and_update_strategy(self, price: float):
        """检查并更新策略参数"""
        current_zone = self.strategy.get_price_zone(price)
        is_safe = self.strategy.is_price_safe(price)
        
        # 检查安全状态变化
        if self.last_safe_status is not None and is_safe != self.last_safe_status:
            if is_safe:
                self.logger.info(f"价格 ${price:.2f} 回到安全范围")
                self.notifier.send_safety_restored(
                    price,
                    self.config.strategy.safe_price_min,
                    self.config.strategy.safe_price_max
                )
            else:
                is_below = price < self.config.strategy.safe_price_min
                self.logger.warning(f"价格 ${price:.2f} 超出安全范围")
                self.notifier.send_safety_warning(
                    price,
                    self.config.strategy.safe_price_min,
                    self.config.strategy.safe_price_max,
                    is_below
                )
        
        self.last_safe_status = is_safe
        self.last_zone = current_zone
    
    def run_once(self):
        """执行一次交易循环"""
        # 获取当前价格
        price = self.get_current_price()
        if not price:
            self.logger.warning("无法获取价格，跳过本次循环")
            return
        
        self.last_price = price
        
        # 检查策略参数更新
        self.check_and_update_strategy(price)
        
        # 检查价格安全性
        if not self.strategy.is_price_safe(price):
            self.logger.debug(f"价格 ${price:.2f} 超出安全范围，跳过交易")
            return
        
        # 获取当前持仓
        position = self.get_current_position()
        self.current_position = position
        current_qty = abs(position.pos) if position else 0
        
        # 生成买入信号
        buy_signal = self.strategy.generate_buy_signal(price, current_qty)
        
        if buy_signal.should_buy:
            self.logger.info(f"买入信号: {buy_signal.reason}")
            
            # 检查本金限制
            if self.check_position_limit_and_notify(price, current_qty, buy_signal.quantity):
                self.execute_grid_buy(buy_signal, price)
        else:
            self.logger.debug(f"无买入信号: {buy_signal.reason}")
        
        # 检查卖出信号（如果有持仓）
        if position and abs(position.pos) > 0:
            reserved_qty = self.tracker.get_reserved_quantity()
            sell_signal = self.strategy.generate_sell_signal(
                price,
                abs(position.pos),
                position.avg_px,
                reserved_qty
            )
            
            if sell_signal.should_sell:
                self.logger.info(f"卖出信号: {sell_signal.reason}")
                self.execute_grid_sell(sell_signal, position)
            else:
                self.logger.debug(f"无卖出信号: {sell_signal.reason}")
    
    def start(self):
        """启动机器人"""
        self.running = True
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("交易机器人启动")
        
        price = self.get_current_price()
        if price:
            self.strategy.highest_price = price
            self.strategy.last_buy_price = price
        
        self.notifier.send_bot_status("running", current_price=price)
        
        while self.running:
            try:
                self.run_once()
                time.sleep(self.config.check_interval)
            except Exception as e:
                self.logger.error(f"主循环异常: {e}")
                self.notifier.send_error_notification(str(e))
                time.sleep(self.config.check_interval)
        
        self.notifier.send_bot_status("stopped")
        self.logger.info("交易机器人已停止")
    
    def manual_buy(self, quantity: int = None):
        """手动买入"""
        price = self.get_current_price()
        if not price:
            print("无法获取价格")
            return
        
        if quantity is None:
            zone = self.strategy.get_price_zone(price)
            grid = self.config.strategy.grid
            if zone == PriceZone.HIGH:
                quantity = grid.high_price_normal_qty
            else:
                quantity = grid.low_price_normal_qty
        
        position = self.get_current_position()
        current_qty = abs(position.pos) if position else 0
        
        signal = GridBuySignal(
            should_buy=True,
            quantity=quantity,
            drop_type=DropType.NORMAL,
            drop_amount=0,
            reason=f"手动买入 {quantity} 张"
        )
        
        if self.check_position_limit_and_notify(price, current_qty, quantity):
            self.execute_grid_buy(signal, price)
    
    def manual_sell(self, quantity: int = None):
        """手动卖出"""
        position = self.get_current_position()
        if not position or abs(position.pos) == 0:
            print("当前无持仓")
            return
        
        if quantity is None:
            quantity = int(abs(position.pos))
        
        signal = GridSellSignal(
            should_sell=True,
            sell_quantity=quantity,
            reserve_quantity=0,
            is_reserve_sell=False,
            target_price=0,
            reason=f"手动卖出 {quantity} 张"
        )
        
        self.execute_grid_sell(signal, position)
    
    def show_status(self):
        """显示当前状态"""
        price = self.get_current_price()
        position = self.get_current_position()
        current_qty = abs(position.pos) if position else 0
        
        print("\n" + "=" * 70)
        print("SOL 全仓合约交易机器人状态 (网格策略)")
        print("=" * 70)
        print(f"模式: {'测试网(模拟盘)' if self.config.okx.use_testnet else '正式网(实盘)'}")
        print(f"交易对: {self.config.strategy.symbol}")
        print(f"本金: {self.config.strategy.capital} USDT")
        print(f"默认杠杆: {self.config.strategy.default_leverage}x")
        print(f"安全价格范围: ${self.config.strategy.safe_price_min:.0f} - ${self.config.strategy.safe_price_max:.0f}")
        
        print("-" * 70)
        print("网格配置:")
        grid = self.config.strategy.grid
        print(f"  正常跌幅: ${grid.normal_drop_min}-${grid.normal_drop_max}")
        print(f"  大跌幅: ${grid.large_drop}+")
        print(f"  高价区间 (≥$120): 正常 {grid.high_price_normal_qty} 张, 大跌 {grid.high_price_large_qty} 张")
        print(f"  低价区间 (<$120): 正常 {grid.low_price_normal_qty} 张, 大跌 {grid.low_price_large_qty} 张")
        print(f"  保留张数: {grid.reserve_qty} 张 (涨 ${grid.reserve_profit_target} 后卖出)")
        
        print("-" * 70)
        
        if price:
            summary = self.strategy.get_strategy_summary(price, current_qty)
            
            print(f"当前价格: ${price:.2f}")
            print(f"价格区间: {summary['price_zone'].upper()}")
            print(f"可交易: {'是 ✓' if summary['can_trade'] else '否 ✗'}")
            
            if summary['can_trade']:
                print(f"目标利润: {summary['profit_target_pct']:.2f}%")
                print(f"止盈价格: ${summary.get('take_profit_price', 0):.2f}")
                print(f"最大合约金额: ${summary['max_contract_amount']:.2f}")
                print(f"当前持仓价值: ${summary['current_position_value']:.2f}")
                print(f"剩余可用额度: ${summary['remaining_amount']:.2f}")
                print(f"上次买入价格: ${summary['last_buy_price']:.2f}")
        else:
            print("无法获取价格")
        
        print("-" * 70)
        
        if position and abs(position.pos) > 0:
            direction = "做多" if position.pos_side == "long" else "做空"
            total_value = position.avg_px * abs(position.pos)
            print(f"当前持仓: {direction} {abs(position.pos):.0f} 张")
            print(f"开仓均价: ${position.avg_px:.2f}")
            print(f"合约总金额: ${total_value:.2f}")
            print(f"未实现盈亏: ${position.upl:.2f} ({position.upl_ratio*100:.2f}%)")
        else:
            print("当前持仓: 无")
        
        print("-" * 70)
        stats = self.tracker.get_statistics()
        print(f"总交易次数: {stats['total_trades']}")
        print(f"胜率: {stats['win_rate']:.1f}%")
        print(f"累计盈亏: ${stats['total_pnl']:.2f}")
        print(f"保留仓位: {stats['reserved_quantity']:.0f} 张")
        print("=" * 70)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="OKX SOL 全仓合约交易机器人 (网格策略)")
    parser.add_argument(
        "--mode",
        choices=["run", "status", "buy", "sell", "test"],
        default="status",
        help="运行模式"
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="使用测试网"
    )
    parser.add_argument(
        "--quantity",
        type=int,
        default=None,
        help="买入/卖出张数"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1000.0,
        help="本金 (USDT)"
    )
    
    args = parser.parse_args()
    
    if args.testnet:
        os.environ["OKX_USE_TESTNET"] = "true"
    if args.capital:
        os.environ["TRADING_CAPITAL"] = str(args.capital)
    
    config = get_config()
    bot = TradingBot(config)
    
    if args.mode == "run":
        bot.start()
    elif args.mode == "status":
        bot.show_status()
    elif args.mode == "buy":
        bot.manual_buy(args.quantity)
    elif args.mode == "sell":
        bot.manual_sell(args.quantity)
    elif args.mode == "test":
        print("测试模式 - 检查 API 连接和策略参数")
        bot.show_status()


if __name__ == "__main__":
    main()
