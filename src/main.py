"""
OKX SOL 全仓合约交易机器人
主程序入口 - 斐波那契网格策略 + 一级/二级限价单预挂
"""
import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime
from typing import Optional, Dict, List

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import AppConfig, get_config
from okx_client import OKXClient, TickerInfo, PositionInfo
from fibonacci_strategy import (
    FibonacciStrategyEngine, FibonacciConfig, FibonacciSignal, TradeAction,
    adjust_buy_price, adjust_sell_price
)
from limit_order_manager import (
    LimitOrderManager, LimitOrder,
    adjust_buy_price as adjust_buy_price_v2,
    adjust_sell_price as adjust_sell_price_v2
)
from telegram_notifier import TelegramNotifier
from database import TradingDatabase, SellResult


class TradingBot:
    """交易机器人主类"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False
        
        # 初始化日志
        self._setup_logging()
        
        # 初始化组件
        self.okx_client = OKXClient(config.okx)
        self.notifier = TelegramNotifier(config.telegram)
        self.db = TradingDatabase()  # SQLite 数据库
        
        # 斐波那契策略引擎
        fib_config = FibonacciConfig(
            price_min=config.strategy.fibonacci.price_min,
            price_max=config.strategy.fibonacci.price_max,
            max_position=config.strategy.fibonacci.max_position,
            symbol=config.strategy.symbol,
            leverage=config.strategy.default_leverage
        )
        self.fib_strategy = FibonacciStrategyEngine(fib_config)
        
        # 限价单管理器（支持一级和二级订单）
        self.order_manager = LimitOrderManager(
            okx_client=self.okx_client,
            strategy_engine=self.fib_strategy,
            telegram=self.notifier,
            database=self.db,
            symbol=config.strategy.symbol
        )
        
        # 当前状态
        self.current_position: Optional[PositionInfo] = None
        self.last_price: float = 0.0
        
        self.logger.info("交易机器人初始化完成")
        self.logger.info(f"模式: {'测试网(模拟盘)' if config.okx.use_testnet else '正式网(实盘)'}")
        self.logger.info(f"交易对: {config.strategy.symbol}")
        self.logger.info(f"默认杠杆: {config.strategy.default_leverage}x")
        
        # 打印斐波那契策略配置
        fib = config.strategy.fibonacci
        self.logger.info("=== 斐波那契网格策略 ===")
        self.logger.info(f"价格范围: ${fib.price_min:.0f} - ${fib.price_max:.0f}")
        self.logger.info(f"最大持仓: {fib.max_position} 张")
        self.logger.info("=== 限价单配置 ===")
        self.logger.info("L1: 相邻斐波那契点位 + 随机偏移")
        self.logger.info("L2: 下一个斐波那契点位 + 随机偏移 ± 1U")
        
        # 同步初始持仓
        self._sync_initial_position()
        
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
    
    def _sync_initial_position(self):
        """同步初始持仓（启动时调用）"""
        try:
            # 获取 OKX 当前持仓
            position = self.get_current_position()
            
            if position and abs(position.pos) > 0:
                okx_qty = int(abs(position.pos))
                avg_price = position.avg_px
                
                self.logger.info(f"OKX 当前持仓: {okx_qty} 张, 均价 ${avg_price:.2f}")
                
                # 检查数据库持仓
                db_qty, db_avg = self.db.get_total_position(self.config.strategy.symbol)
                
                if db_qty != okx_qty:
                    self.logger.warning(f"数据库持仓 ({db_qty}) 与 OKX ({okx_qty}) 不一致")
                    # 可以选择同步数据库
            else:
                self.logger.info("当前无持仓")
                
        except Exception as e:
            self.logger.error(f"同步初始持仓失败: {e}")
    
    def get_current_price(self) -> Optional[float]:
        """获取当前价格"""
        try:
            ticker = self.okx_client.get_ticker(self.config.strategy.symbol)
            if ticker:
                return ticker.last
        except Exception as e:
            self.logger.error(f"获取价格失败: {e}")
        return None
    
    def get_current_position(self) -> Optional[PositionInfo]:
        """获取当前持仓"""
        try:
            positions = self.okx_client.get_positions(
                inst_type="SWAP",
                inst_id=self.config.strategy.symbol
            )
            if positions:
                self.current_position = positions[0]
                return positions[0]
        except Exception as e:
            self.logger.error(f"获取持仓失败: {e}")
        return None
    
    def run_once(self):
        """执行一次交易检查"""
        try:
            # 获取当前价格
            price = self.get_current_price()
            if not price:
                self.logger.warning("无法获取价格")
                return
            
            self.last_price = price
            
            # 获取当前持仓
            position = self.get_current_position()
            current_qty = int(abs(position.pos)) if position else 0
            
            # 更新斐波那契策略的当前持仓
            self.fib_strategy.current_position = current_qty
            
            # 检查已成交的限价单
            filled_orders = self.order_manager.check_filled_orders(current_qty)
            
            if filled_orders:
                # 有订单成交，更新持仓数量
                for order in filled_orders:
                    if order.side == "buy":
                        current_qty += order.quantity
                    else:
                        current_qty -= order.quantity
                    self.logger.info(f"订单成交 L{order.level}: {order.side} {order.quantity} 张 @ ${order.price:.1f}")
            
            # 检查是否需要初始化买入
            if current_qty == 0:
                signal = self.fib_strategy.generate_signal(price, current_qty)
                if signal and signal.action == TradeAction.BUY and "初始化" in signal.reason:
                    self._execute_market_buy(signal, price)
                    return
            
            # 更新限价单（一级和二级）
            self.order_manager.update_orders(price, current_qty)
            
        except Exception as e:
            self.logger.error(f"交易检查异常: {e}")
    
    def _execute_market_buy(self, signal: FibonacciSignal, price: float):
        """执行市价买入（用于初始化）"""
        try:
            # 设置杠杆
            self.okx_client.set_leverage(
                inst_id=self.config.strategy.symbol,
                lever=self.config.strategy.default_leverage,
                mgn_mode="cross"
            )
            
            # 下单买入
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode="cross",
                side="buy",
                order_type="market",
                sz=str(signal.quantity)
            )
            
            if result.get("code") == "0":
                total_value = price * signal.quantity
                self.logger.info(
                    f"初始化买入成功: {signal.quantity} 张 @ ${price:.2f}, "
                    f"合约金额 ${total_value:.2f}"
                )
                
                # 记录到数据库
                self.db.record_buy(
                    symbol=self.config.strategy.symbol,
                    entry_price=price,
                    quantity=signal.quantity,
                    direction="LONG",
                    notes=f"初始化买入: {signal.reason}"
                )
                
                # 发送 Telegram 通知
                self.notifier.send_fibonacci_trade_notification(
                    action="BUY",
                    price=price,
                    quantity=signal.quantity,
                    target_position=signal.target_position,
                    current_position=signal.target_position,
                    reason=signal.reason
                )
                
            else:
                self.logger.error(f"初始化买入失败: {result}")
                
        except Exception as e:
            self.logger.error(f"初始化买入异常: {e}")
    
    def manual_buy(self, quantity: int):
        """手动买入"""
        try:
            price = self.get_current_price()
            if not price:
                print("无法获取当前价格")
                return
            
            # 设置杠杆
            self.okx_client.set_leverage(
                inst_id=self.config.strategy.symbol,
                lever=self.config.strategy.default_leverage,
                mgn_mode="cross"
            )
            
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode="cross",
                side="buy",
                order_type="market",
                sz=str(quantity)
            )
            
            if result.get("code") == "0":
                total_value = price * quantity
                print(f"买入成功: {quantity} 张 @ ${price:.2f}, 合约金额 ${total_value:.2f}")
                
                # 记录到数据库
                self.db.record_buy(
                    symbol=self.config.strategy.symbol,
                    entry_price=price,
                    quantity=quantity,
                    direction="LONG",
                    notes="手动买入"
                )
            else:
                print(f"买入失败: {result}")
                
        except Exception as e:
            print(f"买入异常: {e}")
    
    def manual_sell(self, quantity: int):
        """手动卖出"""
        try:
            price = self.get_current_price()
            if not price:
                print("无法获取当前价格")
                return
            
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode="cross",
                side="sell",
                order_type="market",
                sz=str(quantity),
                reduce_only=True
            )
            
            if result.get("code") == "0":
                total_value = price * quantity
                print(f"卖出成功: {quantity} 张 @ ${price:.2f}, 合约金额 ${total_value:.2f}")
                
                # 使用 FIFO 计算盈亏
                sell_result = self.db.record_sell_fifo(
                    symbol=self.config.strategy.symbol,
                    exit_price=price,
                    quantity=quantity,
                    direction="LONG"
                )
                
                if sell_result:
                    print(f"本次利润: ${sell_result.total_profit:.2f}")
            else:
                print(f"卖出失败: {result}")
                
        except Exception as e:
            print(f"卖出异常: {e}")
    
    def show_status(self):
        """显示当前状态"""
        print("\n" + "=" * 70)
        print("SOL 全仓合约交易机器人状态 (斐波那契策略 + 二级限价单)")
        print("=" * 70)
        
        # 基本信息
        print(f"模式: {'测试网(模拟盘)' if self.config.okx.use_testnet else '正式网(实盘)'}")
        print(f"交易对: {self.config.strategy.symbol}")
        print(f"默认杠杆: {self.config.strategy.default_leverage}x")
        
        # 斐波那契配置
        fib = self.config.strategy.fibonacci
        print("-" * 70)
        print("斐波那契策略配置:")
        print(f"  价格范围: ${fib.price_min:.0f} - ${fib.price_max:.0f}")
        print(f"  最大持仓: {fib.max_position} 张")
        
        # 限价单配置
        print("-" * 70)
        print("限价单配置:")
        print("  L1: 相邻斐波那契点位 + 随机偏移 (.2/.3/.6/.7)")
        print("  L2: 下一个斐波那契点位 + 随机偏移 ± 1U")
        
        # 当前价格和持仓
        price = self.get_current_price()
        position = self.get_current_position()
        
        print("-" * 70)
        if price:
            print(f"当前价格: ${price:.2f}")
            target_pos = self.fib_strategy.get_target_position(price)
            print(f"目标持仓: {target_pos} 张")
        
        if position and abs(position.pos) > 0:
            qty = int(abs(position.pos))
            print(f"当前持仓: {qty} 张")
            print(f"持仓均价: ${position.avg_px:.2f}")
            print(f"未实现盈亏: ${position.upl:.2f}")
        else:
            print("当前持仓: 无")
        
        # 限价单状态
        print("-" * 70)
        print("当前限价单:")
        status = self.order_manager.get_status()
        
        if status["buy_order_l1"]:
            o = status["buy_order_l1"]
            print(f"  买入 L1: ${o['price']:.1f} x {o['quantity']} 张 (Fib {o['fib_level']:.3f})")
        else:
            print("  买入 L1: 无")
        
        if status["buy_order_l2"]:
            o = status["buy_order_l2"]
            print(f"  买入 L2: ${o['price']:.1f} x {o['quantity']} 张 (Fib {o['fib_level']:.3f})")
        else:
            print("  买入 L2: 无")
        
        if status["sell_order_l1"]:
            o = status["sell_order_l1"]
            print(f"  卖出 L1: ${o['price']:.1f} x {o['quantity']} 张 (Fib {o['fib_level']:.3f})")
        else:
            print("  卖出 L1: 无")
        
        if status["sell_order_l2"]:
            o = status["sell_order_l2"]
            print(f"  卖出 L2: ${o['price']:.1f} x {o['quantity']} 张 (Fib {o['fib_level']:.3f})")
        else:
            print("  卖出 L2: 无")
        
        # 数据库统计
        print("-" * 70)
        print("交易统计 (数据库):")
        db_qty, db_avg = self.db.get_total_position(self.config.strategy.symbol)
        print(f"  数据库持仓: {db_qty} 张")
        if db_avg:
            print(f"  平均成本: ${db_avg:.2f}")
        
        print("=" * 70)
    
    def show_fib_levels(self):
        """显示斐波那契点位和价格偏移示例"""
        print("\n斐波那契点位及价格偏移示例:")
        print("-" * 70)
        
        for level, fib_price, target_pos in self.fib_strategy.fib_levels:
            buy_l1 = adjust_buy_price_v2(fib_price, is_level2=False)
            buy_l2 = adjust_buy_price_v2(fib_price, is_level2=True)
            sell_l1 = adjust_sell_price_v2(fib_price, is_level2=False)
            sell_l2 = adjust_sell_price_v2(fib_price, is_level2=True)
            
            print(f"  {level:.3f} | 基准 ${fib_price:.2f} | 买L1 ${buy_l1:.1f} | 买L2 ${buy_l2:.1f} | 卖L1 ${sell_l1:.1f} | 卖L2 ${sell_l2:.1f} | 目标 {target_pos}张")
    
    def start(self):
        """启动机器人"""
        self.running = True
        
        # 设置信号处理
        def signal_handler(signum, frame):
            self.logger.info("收到停止信号，正在关闭...")
            self.running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 同步交易所订单状态
        self.order_manager.sync_with_exchange()
        
        # 发送启动通知
        self.notifier.send_message(
            "🤖 交易机器人启动\n\n"
            f"模式: {'测试网' if self.config.okx.use_testnet else '正式网'}\n"
            f"交易对: {self.config.strategy.symbol}\n"
            f"策略: 斐波那契网格 + 二级限价单\n"
            f"价格范围: ${self.config.strategy.fibonacci.price_min:.0f} - ${self.config.strategy.fibonacci.price_max:.0f}\n"
            f"最大持仓: {self.config.strategy.fibonacci.max_position} 张"
        )
        
        self.logger.info("交易机器人启动")
        
        # 首次运行
        self.run_once()
        
        # 主循环
        interval = self.config.check_interval
        while self.running:
            try:
                self.run_once()
                time.sleep(interval)
            except Exception as e:
                self.logger.error(f"主循环异常: {e}")
                time.sleep(interval)
        
        # 关闭前取消所有挂单
        self.order_manager._cancel_all_orders()
        self.logger.info("交易机器人已停止")
    
    def stop(self):
        """停止机器人"""
        self.running = False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="OKX SOL 全仓合约交易机器人")
    parser.add_argument(
        "--mode",
        choices=["run", "status", "buy", "sell", "test"],
        default="status",
        help="运行模式: run=启动机器人, status=查看状态, buy=手动买入, sell=手动卖出, test=测试斐波那契点位"
    )
    parser.add_argument("--testnet", action="store_true", help="使用测试网")
    parser.add_argument("--quantity", type=int, default=1, help="买入/卖出数量")
    
    args = parser.parse_args()
    
    # 加载配置
    config = get_config()
    
    # 如果命令行指定了 testnet，覆盖配置
    if args.testnet:
        config.okx.use_testnet = True
    
    # 创建机器人
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
        bot.show_fib_levels()


if __name__ == "__main__":
    main()
