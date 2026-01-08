"""
OKX SOL 全仓合约交易机器人
主程序入口 - 斐波那契网格策略
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
from fibonacci_strategy import (
    FibonacciStrategyEngine, FibonacciConfig, FibonacciSignal, TradeAction
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
                okx_qty = abs(position.pos)
                okx_avg = position.avg_px
                
                # 检查数据库是否有持仓记录
                db_batches = self.db.get_position_batches()
                db_total = sum(b['remaining_qty'] for b in db_batches)
                
                if db_total > 0:
                    self.logger.info(f"数据库已有持仓: {db_total}张 @ ${db_batches[0]['entry_price']:.2f}")
                else:
                    # 数据库没有记录，使用 OKX 持仓同步
                    self.logger.info(f"同步 OKX 持仓到数据库: {okx_qty}张 @ ${okx_avg:.2f}")
                    self.db.record_buy(
                        symbol=self.config.strategy.symbol,
                        entry_price=okx_avg,
                        quantity=okx_qty,
                        direction="LONG",
                        notes="初始同步 OKX 持仓"
                    )
                
                # 更新斐波那契策略的当前持仓
                self.fib_strategy.current_position = int(okx_qty)
                self.logger.info(f"斐波那契策略当前持仓: {self.fib_strategy.current_position} 张")
            else:
                self.logger.info("当前无持仓")
                self.fib_strategy.current_position = 0
                
        except Exception as e:
            self.logger.error(f"同步初始持仓异常: {e}")
    
    def _signal_handler(self, signum, frame):
        """信号处理"""
        self.logger.info("收到停止信号，正在关闭...")
        self.running = False
    
    def get_current_price(self) -> Optional[float]:
        """获取当前价格"""
        try:
            ticker = self.okx_client.get_ticker(self.config.strategy.symbol)
            if ticker.get("code") == "0" and ticker.get("data"):
                data = ticker["data"][0]
                return float(data.get("last", 0))
        except Exception as e:
            self.logger.error(f"获取价格异常: {e}")
        return None
    
    def get_current_position(self) -> Optional[PositionInfo]:
        """获取当前持仓"""
        try:
            result = self.okx_client.get_positions(
                inst_type="SWAP",
                inst_id=self.config.strategy.symbol
            )
            
            if result.get("code") == "0" and result.get("data"):
                for pos_data in result["data"]:
                    pos_str = pos_data.get("pos", "0")
                    pos = float(pos_str) if pos_str else 0
                    
                    if abs(pos) > 0:
                        avg_px_str = pos_data.get("avgPx", "0")
                        upl_str = pos_data.get("upl", "0")
                        
                        upl_ratio_str = pos_data.get("uplRatio", "0")
                        margin_str = pos_data.get("margin", "0")
                        lever_str = pos_data.get("lever", "1")
                        
                        return PositionInfo(
                            inst_id=pos_data.get("instId", ""),
                            pos_side=pos_data.get("posSide", "net"),
                            pos=pos,
                            avg_px=float(avg_px_str) if avg_px_str else 0,
                            upl=float(upl_str) if upl_str else 0,
                            upl_ratio=float(upl_ratio_str) if upl_ratio_str else 0,
                            lever=int(lever_str) if lever_str else 1,
                            margin=float(margin_str) if margin_str else 0
                        )
            return None
        except Exception as e:
            self.logger.error(f"获取持仓异常: {e}")
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
            
            # 获取斐波那契交易信号
            signal = self.fib_strategy.get_signal(price)
            
            if signal:
                self.logger.info(f"斐波那契{signal.action.value}信号: {signal.reason}")
                
                if signal.action == TradeAction.BUY:
                    self._execute_fibonacci_buy(signal, price)
                elif signal.action == TradeAction.SELL:
                    self._execute_fibonacci_sell(signal, price, position)
            
        except Exception as e:
            self.logger.error(f"交易检查异常: {e}")
    
    def _execute_fibonacci_buy(self, signal: FibonacciSignal, price: float):
        """执行斐波那契买入"""
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
                    f"斐波那契买入成功: {signal.quantity} 张 @ ${price:.2f}, "
                    f"合约金额 ${total_value:.2f}"
                )
                
                # 记录到数据库
                self.db.record_buy(
                    symbol=self.config.strategy.symbol,
                    entry_price=price,
                    quantity=signal.quantity,
                    direction="LONG",
                    notes=f"斐波那契买入: {signal.reason}"
                )
                
                # 发送 Telegram 通知
                self.notifier.send_fibonacci_trade_notification(
                    action="BUY",
                    price=price,
                    quantity=signal.quantity,
                    target_position=signal.target_position,
                    current_position=signal.target_position,
                    fib_level=signal.fib_level,
                    fib_price=signal.fib_price,
                    reason=signal.reason
                )
            else:
                self.logger.error(f"斐波那契买入失败: {result}")
                
        except Exception as e:
            self.logger.error(f"斐波那契买入异常: {e}")
    
    def _execute_fibonacci_sell(self, signal: FibonacciSignal, price: float, position: PositionInfo):
        """执行斐波那契卖出"""
        try:
            # 下单卖出
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode="cross",
                side="sell",
                order_type="market",
                sz=str(signal.quantity),
                reduce_only=True
            )
            
            if result.get("code") == "0":
                total_value = price * signal.quantity
                
                # 使用 FIFO 计算盈亏
                sell_result = self.db.record_sell_fifo(
                    symbol=self.config.strategy.symbol,
                    exit_price=price,
                    quantity=signal.quantity,
                    direction="LONG",
                    notes=f"斐波那契卖出: {signal.reason}"
                )
                
                pnl = sell_result.total_pnl if sell_result else 0
                
                self.logger.info(
                    f"斐波那契卖出成功: {signal.quantity} 张 @ ${price:.2f}, "
                    f"合约金额 ${total_value:.2f}, 盈亏 ${pnl:.2f}"
                )
                
                # 发送 Telegram 通知
                self.notifier.send_fibonacci_trade_notification(
                    action="SELL",
                    price=price,
                    quantity=signal.quantity,
                    target_position=signal.target_position,
                    current_position=signal.target_position,
                    fib_level=signal.fib_level,
                    fib_price=signal.fib_price,
                    reason=signal.reason,
                    pnl=pnl
                )
            else:
                self.logger.error(f"斐波那契卖出失败: {result}")
                
        except Exception as e:
            self.logger.error(f"斐波那契卖出异常: {e}")
    
    def show_status(self):
        """显示当前状态"""
        price = self.get_current_price()
        position = self.get_current_position()
        current_qty = int(abs(position.pos)) if position else 0
        
        # 更新斐波那契策略
        self.fib_strategy.current_position = current_qty
        
        print("\n" + "=" * 70)
        print("SOL 全仓合约交易机器人状态 (斐波那契策略)")
        print("=" * 70)
        print(f"模式: {'测试网(模拟盘)' if self.config.okx.use_testnet else '正式网(实盘)'}")
        print(f"交易对: {self.config.strategy.symbol}")
        print(f"默认杠杆: {self.config.strategy.default_leverage}x")
        
        print("-" * 70)
        fib = self.config.strategy.fibonacci
        print("斐波那契配置:")
        print(f"  价格范围: ${fib.price_min:.0f} - ${fib.price_max:.0f}")
        print(f"  最大持仓: {fib.max_position} 张")
        
        if price:
            target = self.fib_strategy.calculate_target_position(price)
            levels = self.fib_strategy.get_fib_levels()
            
            print("-" * 70)
            print(f"当前价格: ${price:.2f}")
            print(f"目标持仓: {target} 张")
            print(f"当前持仓: {current_qty} 张")
            
            if target > current_qty:
                print(f"操作建议: 买入 {target - current_qty} 张")
            elif target < current_qty:
                print(f"操作建议: 卖出 {current_qty - target} 张")
            else:
                print("操作建议: 保持当前持仓")
            
            print("-" * 70)
            print("斐波那契网格点位:")
            for level in levels:
                marker = " ← 当前" if abs(level['price'] - price) < 2 else ""
                print(f"  {level['level']:.3f} | ${level['price']:.2f} | 目标 {level['target_position']} 张{marker}")
        
        if position:
            print("-" * 70)
            # 判断方向
            if position.pos_side == "net":
                direction = "做多" if position.pos > 0 else "做空"
            else:
                direction = "做多" if position.pos_side == "long" else "做空"
            
            print(f"当前持仓 (OKX): {direction} {abs(position.pos):.0f} 张")
            print(f"OKX 均价: ${position.avg_px:.2f}")
            print(f"合约总金额: ${abs(position.pos) * position.avg_px:.2f}")
            print(f"未实现盈亏: ${position.upl:.2f}")
        
        # 数据库统计
        stats = self.db.get_statistics()
        if stats:
            print("-" * 70)
            print("交易统计 (数据库):")
            print(f"  总交易次数: {stats.get('total_trades', 0)}")
            print(f"  累计盈亏: ${stats.get('total_pnl', 0):.2f}")
        
        print("=" * 70)
    
    def run(self):
        """运行交易机器人"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.running = True
        self.logger.info("交易机器人启动")
        
        # 发送启动通知
        price = self.get_current_price()
        position = self.get_current_position()
        position_info = None
        if position:
            position_info = {
                'direction': '做多' if position.pos > 0 else '做空',
                'quantity': abs(position.pos),
                'avg_price': position.avg_px,
                'upl': position.upl
            }
        self.notifier.send_bot_status(
            status="running",
            current_price=price or 0,
            has_position=position is not None and abs(position.pos) > 0,
            position_info=position_info
        )
        
        check_interval = self.config.check_interval
        
        while self.running:
            try:
                self.run_once()
                time.sleep(check_interval)
            except Exception as e:
                self.logger.error(f"运行异常: {e}")
                time.sleep(check_interval)
        
        self.logger.info("交易机器人已停止")
    
    def manual_buy(self, quantity: int):
        """手动买入"""
        price = self.get_current_price()
        if not price:
            print("无法获取价格")
            return
        
        signal = FibonacciSignal(
            action=TradeAction.BUY,
            quantity=quantity,
            target_position=self.fib_strategy.current_position + quantity,
            fib_level=0,
            fib_price=price,
            reason="手动买入"
        )
        self._execute_fibonacci_buy(signal, price)
    
    def manual_sell(self, quantity: int):
        """手动卖出"""
        price = self.get_current_price()
        position = self.get_current_position()
        
        if not price:
            print("无法获取价格")
            return
        
        if not position or abs(position.pos) < quantity:
            print(f"持仓不足，当前持仓: {abs(position.pos) if position else 0} 张")
            return
        
        signal = FibonacciSignal(
            action=TradeAction.SELL,
            quantity=quantity,
            target_position=self.fib_strategy.current_position - quantity,
            fib_level=0,
            fib_price=price,
            reason="手动卖出"
        )
        self._execute_fibonacci_sell(signal, price, position)
    
    def add_position(self, quantity: float, price: float):
        """手动添加持仓记录到数据库"""
        self.db.record_buy(
            symbol=self.config.strategy.symbol,
            entry_price=price,
            quantity=quantity,
            direction="LONG",
            is_manual=True,
            notes="手动添加持仓"
        )
        print(f"已添加持仓记录: {quantity} 张 @ ${price:.2f}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="OKX SOL 全仓合约交易机器人")
    parser.add_argument("--mode", choices=["run", "status", "buy", "sell", "test"],
                       default="status", help="运行模式")
    parser.add_argument("--testnet", action="store_true", help="使用测试网")
    parser.add_argument("--quantity", type=int, help="买入/卖出张数")
    parser.add_argument("--capital", type=float, help="本金 (USDT)")
    parser.add_argument("--add-position", action="store_true", help="添加持仓记录")
    parser.add_argument("--price", type=float, help="持仓价格")
    
    args = parser.parse_args()
    
    # 命令行参数覆盖环境变量（只有明确指定时）
    if args.testnet:
        os.environ["OKX_USE_TESTNET"] = "true"
    if args.capital is not None:
        os.environ["TRADING_CAPITAL"] = str(args.capital)
    
    # 获取配置
    config = get_config()
    
    # 创建机器人
    bot = TradingBot(config)
    
    # 处理添加持仓
    if args.add_position:
        if args.quantity and args.price:
            bot.add_position(args.quantity, args.price)
        else:
            print("请指定 --quantity 和 --price")
        return
    
    # 根据模式执行
    if args.mode == "status":
        bot.show_status()
    elif args.mode == "run":
        bot.run()
    elif args.mode == "buy":
        if args.quantity:
            bot.manual_buy(args.quantity)
        else:
            print("请指定 --quantity")
    elif args.mode == "sell":
        if args.quantity:
            bot.manual_sell(args.quantity)
        else:
            print("请指定 --quantity")
    elif args.mode == "test":
        bot.show_status()
        print("\n测试模式: 执行一次交易检查")
        bot.run_once()


if __name__ == "__main__":
    main()
