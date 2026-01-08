"""
OKX SOL 全仓合约交易机器人
主程序入口 - 斐波那契网格策略 + 限价单预挂
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
        
        # 限价单管理
        self.pending_buy_order: Optional[Dict] = None   # 当前挂的买入单
        self.pending_sell_order: Optional[Dict] = None  # 当前挂的卖出单
        
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
                db_lots = self.db.get_position_lots(self.config.strategy.symbol)
                db_qty = sum(lot.get('quantity', 0) for lot in db_lots)
                
                if db_qty == 0:
                    # 数据库没有记录，用 OKX 均价创建初始批次
                    self.db.record_buy(
                        symbol=self.config.strategy.symbol,
                        entry_price=okx_avg,
                        quantity=int(okx_qty),
                        direction="LONG",
                        notes="初始同步: OKX 持仓"
                    )
                    self.logger.info(f"同步 OKX 持仓到数据库: {int(okx_qty)}张 @ ${okx_avg:.2f}")
                else:
                    self.logger.info(f"数据库已有持仓: {db_qty}张")
            else:
                self.logger.info("当前无持仓")
                
        except Exception as e:
            self.logger.error(f"同步初始持仓异常: {e}")
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        self.logger.info("收到停止信号，正在关闭...")
        self.running = False
    
    def get_current_price(self) -> Optional[float]:
        """获取当前价格"""
        try:
            result = self.okx_client.get_ticker(self.config.strategy.symbol)
            if result.get("code") == "0" and result.get("data"):
                return float(result["data"][0]["last"])
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
    
    def get_pending_orders(self) -> List[Dict]:
        """获取当前挂单"""
        try:
            result = self.okx_client.get_orders_pending(
                inst_type="SWAP",
                inst_id=self.config.strategy.symbol
            )
            if result.get("code") == "0":
                return result.get("data", [])
        except Exception as e:
            self.logger.error(f"获取挂单异常: {e}")
        return []
    
    def cancel_all_orders(self):
        """取消所有挂单"""
        try:
            orders = self.get_pending_orders()
            for order in orders:
                ord_id = order.get("ordId")
                if ord_id:
                    self.okx_client.cancel_order(
                        inst_id=self.config.strategy.symbol,
                        ord_id=ord_id
                    )
                    self.logger.info(f"取消订单: {ord_id}")
            self.pending_buy_order = None
            self.pending_sell_order = None
        except Exception as e:
            self.logger.error(f"取消订单异常: {e}")
    
    def place_limit_buy_order(self, price: float, quantity: int) -> Optional[str]:
        """挂买入限价单"""
        try:
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
                order_type="limit",
                sz=str(quantity),
                px=str(price)
            )
            
            if result.get("code") == "0" and result.get("data"):
                ord_id = result["data"][0].get("ordId")
                self.pending_buy_order = {
                    "ordId": ord_id,
                    "price": price,
                    "quantity": quantity
                }
                self.logger.info(f"挂买入限价单: {quantity} 张 @ ${price:.1f}, 订单ID: {ord_id}")
                return ord_id
            else:
                self.logger.error(f"挂买入限价单失败: {result}")
        except Exception as e:
            self.logger.error(f"挂买入限价单异常: {e}")
        return None
    
    def place_limit_sell_order(self, price: float, quantity: int) -> Optional[str]:
        """挂卖出限价单"""
        try:
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode="cross",
                side="sell",
                order_type="limit",
                sz=str(quantity),
                px=str(price),
                reduce_only=True
            )
            
            if result.get("code") == "0" and result.get("data"):
                ord_id = result["data"][0].get("ordId")
                self.pending_sell_order = {
                    "ordId": ord_id,
                    "price": price,
                    "quantity": quantity
                }
                self.logger.info(f"挂卖出限价单: {quantity} 张 @ ${price:.1f}, 订单ID: {ord_id}")
                return ord_id
            else:
                self.logger.error(f"挂卖出限价单失败: {result}")
        except Exception as e:
            self.logger.error(f"挂卖出限价单异常: {e}")
        return None
    
    def check_order_filled(self, ord_id: str) -> Optional[Dict]:
        """检查订单是否成交"""
        try:
            result = self.okx_client.get_order(
                inst_id=self.config.strategy.symbol,
                ord_id=ord_id
            )
            if result.get("code") == "0" and result.get("data"):
                order = result["data"][0]
                state = order.get("state")
                if state == "filled":
                    return {
                        "ordId": ord_id,
                        "side": order.get("side"),
                        "fillPx": float(order.get("fillPx", 0)),
                        "fillSz": float(order.get("fillSz", 0)),
                        "state": state
                    }
        except Exception as e:
            self.logger.error(f"检查订单状态异常: {e}")
        return None
    
    def setup_limit_orders(self, current_price: float, current_position: int):
        """设置限价单预挂"""
        try:
            # 获取上下两个斐波那契点位
            fib_prices = self.fib_strategy.config.get_fib_prices()
            
            buy_level = None   # 下一个买入点位
            sell_level = None  # 下一个卖出点位
            
            for i, (level, price, target_pos) in enumerate(fib_prices):
                if price < current_price and target_pos > current_position:
                    # 找到低于当前价格且需要买入的点位
                    buy_level = (level, price, target_pos)
                if price > current_price and target_pos < current_position:
                    # 找到高于当前价格且需要卖出的点位
                    if sell_level is None:
                        sell_level = (level, price, target_pos)
            
            # 取消现有挂单
            self.cancel_all_orders()
            
            # 挂买入限价单
            if buy_level:
                level, base_price, target_pos = buy_level
                buy_qty = target_pos - current_position
                if buy_qty > 0:
                    # 应用随机价格偏移
                    adjusted_price = adjust_buy_price(base_price)
                    self.place_limit_buy_order(adjusted_price, buy_qty)
                    self.logger.info(f"预挂买入单: 斐波那契 {level:.3f} 点位, 基准 ${base_price:.2f} -> 实际 ${adjusted_price:.1f}")
            
            # 挂卖出限价单（只有有持仓时才挂）
            if sell_level and current_position > 0:
                level, base_price, target_pos = sell_level
                sell_qty = current_position - target_pos
                if sell_qty > 0:
                    # 应用随机价格偏移
                    adjusted_price = adjust_sell_price(base_price)
                    self.place_limit_sell_order(adjusted_price, sell_qty)
                    self.logger.info(f"预挂卖出单: 斐波那契 {level:.3f} 点位, 基准 ${base_price:.2f} -> 实际 ${adjusted_price:.1f}")
                    
        except Exception as e:
            self.logger.error(f"设置限价单异常: {e}")
    
    def check_and_handle_filled_orders(self, current_price: float, current_position: int):
        """检查并处理已成交的订单"""
        try:
            # 检查买入单
            if self.pending_buy_order:
                ord_id = self.pending_buy_order["ordId"]
                filled = self.check_order_filled(ord_id)
                if filled:
                    fill_price = filled["fillPx"]
                    fill_qty = int(filled["fillSz"])
                    
                    self.logger.info(f"买入限价单成交: {fill_qty} 张 @ ${fill_price:.2f}")
                    
                    # 记录到数据库
                    self.db.record_buy(
                        symbol=self.config.strategy.symbol,
                        entry_price=fill_price,
                        quantity=fill_qty,
                        direction="LONG",
                        notes=f"限价单成交"
                    )
                    
                    # 发送 Telegram 通知
                    self.notifier.send_fibonacci_trade_notification(
                        action="BUY",
                        price=fill_price,
                        quantity=fill_qty,
                        target_position=current_position + fill_qty,
                        current_position=current_position + fill_qty,
                        reason="限价单成交"
                    )
                    
                    self.pending_buy_order = None
                    
                    # 重新设置限价单
                    new_position = current_position + fill_qty
                    self.setup_limit_orders(current_price, new_position)
            
            # 检查卖出单
            if self.pending_sell_order:
                ord_id = self.pending_sell_order["ordId"]
                filled = self.check_order_filled(ord_id)
                if filled:
                    fill_price = filled["fillPx"]
                    fill_qty = int(filled["fillSz"])
                    
                    self.logger.info(f"卖出限价单成交: {fill_qty} 张 @ ${fill_price:.2f}")
                    
                    # 使用 FIFO 计算盈亏
                    sell_result = self.db.record_sell_fifo(
                        symbol=self.config.strategy.symbol,
                        exit_price=fill_price,
                        quantity=fill_qty,
                        direction="LONG"
                    )
                    
                    # 发送 Telegram 通知
                    self.notifier.send_fibonacci_trade_notification(
                        action="SELL",
                        price=fill_price,
                        quantity=fill_qty,
                        target_position=current_position - fill_qty,
                        current_position=current_position - fill_qty,
                        profit=sell_result.total_profit if sell_result else 0,
                        reason="限价单成交"
                    )
                    
                    self.pending_sell_order = None
                    
                    # 重新设置限价单
                    new_position = current_position - fill_qty
                    self.setup_limit_orders(current_price, new_position)
                    
        except Exception as e:
            self.logger.error(f"检查成交订单异常: {e}")
    
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
            self.check_and_handle_filled_orders(price, current_qty)
            
            # 如果没有挂单，设置新的限价单
            if not self.pending_buy_order and not self.pending_sell_order:
                # 首次启动或订单都已成交，检查是否需要初始化买入
                signal = self.fib_strategy.generate_signal(price, current_qty)
                
                if signal and signal.action == TradeAction.BUY and "初始化" in signal.reason:
                    # 初始化买入使用市价单
                    self._execute_market_buy(signal, price)
                else:
                    # 设置限价单预挂
                    self.setup_limit_orders(price, current_qty)
            
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
                
                # 设置限价单预挂
                self.setup_limit_orders(price, signal.target_position)
            else:
                self.logger.error(f"初始化买入失败: {result}")
                
        except Exception as e:
            self.logger.error(f"初始化买入异常: {e}")
    
    def run(self):
        """运行交易机器人"""
        self.running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("交易机器人启动")
        
        # 发送启动通知
        position = self.get_current_position()
        price = self.get_current_price()
        
        if position and abs(position.pos) > 0:
            pos_direction = "LONG" if position.pos > 0 else "SHORT"
            position_info = {
                'direction': pos_direction,
                'entry_price': position.avg_px,
                'size': abs(position.pos),
                'unrealized_pnl': position.upl
            }
            self.notifier.send_bot_status(
                status="运行中",
                current_price=price,
                has_position=True,
                position_info=position_info
            )
        else:
            self.notifier.send_bot_status(
                status="运行中",
                current_price=price,
                has_position=False
            )
        
        # 主循环
        interval = self.config.check_interval
        while self.running:
            try:
                self.run_once()
                time.sleep(interval)
            except Exception as e:
                self.logger.error(f"主循环异常: {e}")
                time.sleep(interval)
        
        # 停止时取消所有挂单
        self.logger.info("正在取消所有挂单...")
        self.cancel_all_orders()
        self.logger.info("交易机器人已停止")
    
    def show_status(self):
        """显示当前状态"""
        print("\n" + "=" * 70)
        print("SOL 全仓合约交易机器人状态 (斐波那契策略)")
        print("=" * 70)
        
        print(f"模式: {'测试网(模拟盘)' if self.config.okx.use_testnet else '正式网(实盘)'}")
        print(f"交易对: {self.config.strategy.symbol}")
        print(f"默认杠杆: {self.config.strategy.default_leverage}x")
        
        # 斐波那契配置
        fib = self.config.strategy.fibonacci
        print("-" * 70)
        print("斐波那契策略配置:")
        print(f"  价格范围: ${fib.price_min:.0f} - ${fib.price_max:.0f}")
        print(f"  最大持仓: {fib.max_position} 张")
        
        # 当前价格
        price = self.get_current_price()
        if price:
            print("-" * 70)
            print(f"当前价格: ${price:.2f}")
            
            # 计算目标持仓
            target = self.fib_strategy.calculate_target_position(price)
            print(f"目标持仓: {target} 张")
        
        # 当前持仓
        position = self.get_current_position()
        if position and abs(position.pos) > 0:
            # 单向持仓模式：根据 pos 正负判断方向
            if position.pos_side == "net":
                direction = "做多" if position.pos > 0 else "做空"
            else:
                direction = "做多" if position.pos_side == "long" else "做空"
            
            print("-" * 70)
            print(f"当前持仓 (OKX): {direction} {abs(position.pos):.0f} 张")
            print(f"OKX 均价: ${position.avg_px:.2f}")
            print(f"合约总金额: ${position.avg_px * abs(position.pos):.2f}")
            print(f"未实现盈亏: ${position.upl:.2f}")
        else:
            print("-" * 70)
            print("当前持仓: 无")
        
        # 挂单状态
        orders = self.get_pending_orders()
        if orders:
            print("-" * 70)
            print("当前挂单:")
            for order in orders:
                side = "买入" if order.get("side") == "buy" else "卖出"
                px = order.get("px", "0")
                sz = order.get("sz", "0")
                print(f"  {side}: {sz} 张 @ ${float(px):.1f}")
        
        # 数据库持仓批次
        db_lots = self.db.get_position_lots(self.config.strategy.symbol)
        if db_lots:
            print("-" * 70)
            print("持仓批次 (FIFO):")
            total_qty = 0
            total_value = 0
            for lot in db_lots:
                qty = lot.get('quantity', 0)
                px = lot.get('entry_price', 0)
                total_qty += qty
                total_value += qty * px
                print(f"  {qty}张 @ ${px:.2f}")
            if total_qty > 0:
                avg = total_value / total_qty
                print(f"  合计: {total_qty}张, 均价 ${avg:.2f}")
        
        print("=" * 70 + "\n")
    
    def manual_buy(self, quantity: int):
        """手动买入"""
        price = self.get_current_price()
        if not price:
            print("无法获取价格")
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
            print(f"买入成功: {quantity} 张 @ ${price:.2f}")
            self.db.record_buy(
                symbol=self.config.strategy.symbol,
                entry_price=price,
                quantity=quantity,
                direction="LONG",
                notes="手动买入"
            )
            self.notifier.send_fibonacci_trade_notification(
                action="BUY",
                price=price,
                quantity=quantity,
                target_position=quantity,
                current_position=quantity,
                reason="手动买入"
            )
        else:
            print(f"买入失败: {result}")
    
    def manual_sell(self, quantity: int):
        """手动卖出"""
        price = self.get_current_price()
        if not price:
            print("无法获取价格")
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
            print(f"卖出成功: {quantity} 张 @ ${price:.2f}")
            
            sell_result = self.db.record_sell_fifo(
                symbol=self.config.strategy.symbol,
                exit_price=price,
                quantity=quantity,
                direction="LONG"
            )
            
            profit = sell_result.total_profit if sell_result else 0
            self.notifier.send_fibonacci_trade_notification(
                action="SELL",
                price=price,
                quantity=quantity,
                target_position=0,
                current_position=0,
                profit=profit,
                reason="手动卖出"
            )
            
            if sell_result:
                print(f"盈亏: ${profit:.2f}")
        else:
            print(f"卖出失败: {result}")
    
    def add_position(self, quantity: int, price: float):
        """手动添加持仓记录（用于同步手动交易）"""
        self.db.record_buy(
            symbol=self.config.strategy.symbol,
            entry_price=price,
            quantity=quantity,
            direction="LONG",
            notes="手动添加"
        )
        print(f"已添加持仓记录: {quantity} 张 @ ${price:.2f}")


def main():
    parser = argparse.ArgumentParser(description="OKX SOL 交易机器人")
    parser.add_argument("--mode", choices=["run", "status", "buy", "sell", "test"],
                        default="status", help="运行模式")
    parser.add_argument("--quantity", type=int, help="买入/卖出数量")
    parser.add_argument("--price", type=float, help="价格（用于添加持仓）")
    parser.add_argument("--add-position", action="store_true", help="添加持仓记录")
    parser.add_argument("--testnet", action="store_true", help="使用测试网")
    
    args = parser.parse_args()
    
    # 加载配置
    config = get_config()
    
    # 命令行参数覆盖
    if args.testnet:
        config.okx.use_testnet = True
    
    # 创建机器人
    bot = TradingBot(config)
    
    # 执行对应模式
    if args.add_position:
        if not args.quantity or not args.price:
            print("添加持仓需要指定 --quantity 和 --price")
            return
        bot.add_position(args.quantity, args.price)
    elif args.mode == "run":
        bot.run()
    elif args.mode == "status":
        bot.show_status()
    elif args.mode == "buy":
        if not args.quantity:
            print("买入需要指定 --quantity")
            return
        bot.manual_buy(args.quantity)
    elif args.mode == "sell":
        if not args.quantity:
            print("卖出需要指定 --quantity")
            return
        bot.manual_sell(args.quantity)
    elif args.mode == "test":
        # 测试模式：显示斐波那契点位和价格偏移
        print("\n斐波那契点位及价格偏移示例:")
        print("-" * 50)
        fib_prices = bot.fib_strategy.config.get_fib_prices()
        for level, price, target in fib_prices:
            buy_px = adjust_buy_price(price)
            sell_px = adjust_sell_price(price)
            print(f"  {level:.3f} | 基准 ${price:.2f} | 买入 ${buy_px:.1f} | 卖出 ${sell_px:.1f} | 目标 {target}张")


if __name__ == "__main__":
    main()
