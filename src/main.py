"""
OKX SOL 全仓合约交易机器人
主程序入口
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
from strategy import TradingStrategyEngine, TradeTracker, PriceZone
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
        self.last_safe_status: Optional[bool] = None  # 上次价格是否安全
        
        self.logger.info("交易机器人初始化完成")
        self.logger.info(f"模式: {'测试网(模拟盘)' if config.okx.use_testnet else '正式网(实盘)'}")
        self.logger.info(f"交易对: {config.strategy.symbol}")
        self.logger.info(f"本金: {config.strategy.capital} USDT")
        self.logger.info(f"默认杠杆: {config.strategy.default_leverage}x")
        self.logger.info(f"安全价格范围: ${config.strategy.safe_price_min:.0f} - ${config.strategy.safe_price_max:.0f}")
        self.logger.info(f"测试模式: {'是' if config.strategy.test_mode else '否'}")
        
    def _setup_logging(self):
        """配置日志"""
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        log_level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        
        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(log_format))
        
        # 文件处理器
        file_handler = logging.FileHandler(self.config.log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        
        # 配置根日志
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
    
    def open_position(self, is_long: bool = True) -> bool:
        """
        开仓
        
        Args:
            is_long: True 做多, False 做空
        """
        price = self.get_current_price()
        if not price:
            return False
        
        # 检查价格是否安全
        can_trade, reason = self.strategy.can_trade(price)
        if not can_trade:
            self.logger.warning(f"无法开仓: {reason}")
            self.notifier.send_error_notification(f"无法开仓: {reason}")
            return False
        
        # 计算策略参数
        contract_amount, leverage = self.strategy.calculate_contract_amount(price)
        position_size = self.strategy.calculate_position_size(price, contract_amount)
        profit_target = self.strategy.calculate_profit_target(price)
        tp_price = self.strategy.calculate_take_profit_price(price, is_long)
        
        # 计算合约总金额 (价格 × 张数)
        total_contract_value = self.strategy.calculate_total_contract_value(price, position_size)
        
        # 设置杠杆
        if not self.setup_leverage(leverage):
            self.logger.error("设置杠杆失败，取消开仓")
            return False
        
        # 下单
        side = "buy" if is_long else "sell"
        pos_side = "long" if is_long else "short"
        
        try:
            result = self.okx_client.place_order(
                inst_id=self.config.strategy.symbol,
                td_mode=self.config.strategy.margin_mode,
                side=side,
                order_type="market",
                sz=str(position_size),
                pos_side=pos_side,
                tp_trigger_px=str(tp_price),
                tp_ord_px="-1"  # 市价止盈
            )
            
            if result.get("code") == "0":
                self.logger.info(
                    f"开仓成功: {'做多' if is_long else '做空'} {position_size} 张 @ ${price:.2f}, "
                    f"合约总金额: ${total_contract_value:.2f}"
                )
                
                # 发送 Telegram 通知
                self.notifier.send_trade_open_notification(
                    symbol=self.config.strategy.symbol,
                    direction="LONG" if is_long else "SHORT",
                    entry_price=price,
                    position_size=position_size,
                    total_contract_value=total_contract_value,
                    leverage=leverage,
                    target_profit_pct=profit_target,
                    take_profit_price=tp_price
                )
                return True
            else:
                self.logger.error(f"开仓失败: {result}")
                self.notifier.send_error_notification(f"开仓失败: {result.get('msg', 'Unknown error')}")
                return False
                
        except Exception as e:
            self.logger.error(f"开仓异常: {e}")
            self.notifier.send_error_notification(f"开仓异常: {str(e)}")
            return False
    
    def close_position(self, position: PositionInfo) -> bool:
        """平仓"""
        try:
            result = self.okx_client.close_position(
                inst_id=self.config.strategy.symbol,
                mgn_mode=self.config.strategy.margin_mode,
                pos_side=position.pos_side
            )
            
            if result.get("code") == "0":
                # 获取平仓价格
                exit_price = self.get_current_price() or position.avg_px
                is_long = position.pos_side == "long"
                
                # 计算合约总金额 (平仓价格 × 张数)
                total_contract_value = self.strategy.calculate_total_contract_value(
                    exit_price, abs(position.pos)
                )
                
                # 计算盈亏
                pnl, pnl_pct = self.strategy.calculate_pnl(
                    entry_price=position.avg_px,
                    exit_price=exit_price,
                    position_size=abs(position.pos),
                    is_long=is_long
                )
                
                # 记录交易
                self.tracker.record_trade(
                    entry_price=position.avg_px,
                    exit_price=exit_price,
                    position_size=abs(position.pos),
                    is_long=is_long,
                    pnl=pnl,
                    pnl_pct=pnl_pct
                )
                
                self.logger.info(
                    f"平仓成功: {'做多' if is_long else '做空'} @ ${exit_price:.2f}, "
                    f"合约总金额: ${total_contract_value:.2f}, "
                    f"盈亏: ${pnl:.2f} ({pnl_pct:+.2f}%)"
                )
                
                # 发送 Telegram 通知
                stats = self.tracker.get_statistics()
                self.notifier.send_trade_close_notification(
                    symbol=self.config.strategy.symbol,
                    direction="LONG" if is_long else "SHORT",
                    entry_price=position.avg_px,
                    exit_price=exit_price,
                    position_size=abs(position.pos),
                    total_contract_value=total_contract_value,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    total_pnl=stats["total_pnl"]
                )
                return True
            else:
                self.logger.error(f"平仓失败: {result}")
                return False
                
        except Exception as e:
            self.logger.error(f"平仓异常: {e}")
            return False
    
    def check_and_update_strategy(self, price: float):
        """检查并更新策略参数（当价格区间变化时）"""
        current_zone = self.strategy.get_price_zone(price)
        is_safe = self.strategy.is_price_safe(price)
        
        # 检查安全状态变化
        if self.last_safe_status is not None and is_safe != self.last_safe_status:
            if is_safe:
                # 从不安全变为安全
                self.logger.info(f"价格 ${price:.2f} 回到安全范围，恢复交易")
                self.notifier.send_message(
                    f"🟢 <b>安全提醒</b>\n\n"
                    f"价格 ${price:.2f} 回到安全范围 "
                    f"(${self.config.strategy.safe_price_min:.0f} - ${self.config.strategy.safe_price_max:.0f})\n"
                    f"交易功能已恢复"
                )
            else:
                # 从安全变为不安全
                if price < self.config.strategy.safe_price_min:
                    reason = f"低于安全下限 ${self.config.strategy.safe_price_min:.0f}"
                else:
                    reason = f"高于安全上限 ${self.config.strategy.safe_price_max:.0f}"
                
                self.logger.warning(f"价格 ${price:.2f} 超出安全范围，停止交易")
                self.notifier.send_message(
                    f"🔴 <b>安全警告</b>\n\n"
                    f"价格 ${price:.2f} {reason}\n"
                    f"安全范围: ${self.config.strategy.safe_price_min:.0f} - ${self.config.strategy.safe_price_max:.0f}\n\n"
                    f"⚠️ 交易功能已暂停，等待价格回归安全范围"
                )
        
        self.last_safe_status = is_safe
        
        # 检查价格区间变化（仅在安全范围内）
        if is_safe and self.last_zone and current_zone != self.last_zone and current_zone != PriceZone.UNSAFE:
            # 价格区间发生变化
            self.logger.info(f"价格区间变化: {self.last_zone.value} -> {current_zone.value}")
            
            summary = self.strategy.get_strategy_summary(price)
            self.notifier.send_strategy_update(
                current_price=price,
                price_zone=current_zone.value,
                profit_target=summary["profit_target_pct"],
                total_contract_value=summary["total_contract_value"],
                position_size=summary["position_size"],
                leverage=summary["leverage"]
            )
        
        self.last_zone = current_zone
    
    def run_once(self):
        """执行一次交易循环"""
        # 获取当前价格
        price = self.get_current_price()
        if not price:
            self.logger.warning("无法获取价格，跳过本次循环")
            return
        
        self.last_price = price
        
        # 检查价格安全性
        is_safe = self.strategy.is_price_safe(price)
        zone = self.strategy.get_price_zone(price)
        
        if is_safe:
            self.logger.debug(f"当前 SOL 价格: ${price:.2f} (安全, {zone.value}区间)")
        else:
            self.logger.debug(f"当前 SOL 价格: ${price:.2f} (不安全, 停止交易)")
        
        # 检查策略参数更新
        self.check_and_update_strategy(price)
        
        # 获取当前持仓
        position = self.get_current_position()
        self.current_position = position
        
        if position:
            # 有持仓，检查是否需要平仓
            total_value = position.avg_px * abs(position.pos)
            self.logger.debug(
                f"当前持仓: {position.pos_side} {position.pos} 张 @ ${position.avg_px:.2f}, "
                f"合约总金额: ${total_value:.2f}, "
                f"未实现盈亏: ${position.upl:.2f} ({position.upl_ratio*100:.2f}%)"
            )
            
            # 这里可以添加额外的平仓逻辑，比如止损
            # 目前依赖 OKX 的止盈止损订单
            
        else:
            # 无持仓，可以考虑开仓
            self.logger.debug("当前无持仓")
            
            # 这里可以添加开仓信号逻辑
            # 目前需要手动触发或通过其他信号
    
    def start(self):
        """启动机器人"""
        self.running = True
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("交易机器人启动")
        
        # 发送启动通知
        price = self.get_current_price()
        self.notifier.send_bot_status("running", current_price=price)
        
        # 主循环
        while self.running:
            try:
                self.run_once()
                time.sleep(self.config.check_interval)
            except Exception as e:
                self.logger.error(f"主循环异常: {e}")
                self.notifier.send_error_notification(str(e))
                time.sleep(self.config.check_interval)
        
        # 发送停止通知
        self.notifier.send_bot_status("stopped")
        self.logger.info("交易机器人已停止")
    
    def manual_open_long(self):
        """手动开多"""
        self.logger.info("手动触发开多")
        return self.open_position(is_long=True)
    
    def manual_open_short(self):
        """手动开空"""
        self.logger.info("手动触发开空")
        return self.open_position(is_long=False)
    
    def manual_close_all(self):
        """手动平仓"""
        position = self.get_current_position()
        if position:
            return self.close_position(position)
        else:
            self.logger.info("当前无持仓")
            return True
    
    def show_status(self):
        """显示当前状态"""
        price = self.get_current_price()
        position = self.get_current_position()
        
        print("\n" + "=" * 60)
        print("SOL 全仓合约交易机器人状态")
        print("=" * 60)
        print(f"模式: {'测试网(模拟盘)' if self.config.okx.use_testnet else '正式网(实盘)'}")
        print(f"测试模式: {'是 (使用固定金额)' if self.config.strategy.test_mode else '否 (按比例计算)'}")
        print(f"交易对: {self.config.strategy.symbol}")
        print(f"本金: {self.config.strategy.capital} USDT")
        print(f"默认杠杆: {self.config.strategy.default_leverage}x")
        print(f"安全价格范围: ${self.config.strategy.safe_price_min:.0f} - ${self.config.strategy.safe_price_max:.0f}")
        print("-" * 60)
        
        if price:
            summary = self.strategy.get_strategy_summary(price)
            
            print(f"当前价格: ${price:.2f}")
            print(f"价格区间: {summary['price_zone'].upper()}")
            print(f"可交易: {'是 ✓' if summary['can_trade'] else '否 ✗'}")
            
            if not summary['can_trade']:
                print(f"原因: {summary['trade_reason']}")
            else:
                zone_cn = "高价区间 (120-150)" if summary['price_zone'] == "high" else "低价区间 (90-120)"
                ratio = self.config.strategy.high_price_leverage_ratio if summary['price_zone'] == "high" else self.config.strategy.low_price_leverage_ratio
                
                print(f"区间说明: {zone_cn}")
                print(f"合约倍数: {ratio}x (本金的 {ratio*100:.0f}%)")
                print(f"目标利润: {summary['profit_target_pct']:.2f}%")
                print(f"开仓张数: {summary['position_size']:.2f}")
                print(f"合约总金额: ${summary['total_contract_value']:.2f} (${price:.2f} × {summary['position_size']:.2f})")
                print(f"做多止盈: ${summary['take_profit_long']:.2f}")
                print(f"做空止盈: ${summary['take_profit_short']:.2f}")
        else:
            print("无法获取价格")
        
        print("-" * 60)
        
        if position:
            direction = "做多" if position.pos_side == "long" else "做空"
            total_value = position.avg_px * abs(position.pos)
            print(f"当前持仓: {direction} {abs(position.pos):.2f} 张")
            print(f"开仓均价: ${position.avg_px:.2f}")
            print(f"合约总金额: ${total_value:.2f}")
            print(f"未实现盈亏: ${position.upl:.2f} ({position.upl_ratio*100:.2f}%)")
        else:
            print("当前持仓: 无")
        
        print("-" * 60)
        stats = self.tracker.get_statistics()
        print(f"总交易次数: {stats['total_trades']}")
        print(f"胜率: {stats['win_rate']:.1f}%")
        print(f"累计盈亏: ${stats['total_pnl']:.2f}")
        print("=" * 60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="OKX SOL 全仓合约交易机器人")
    parser.add_argument(
        "--mode",
        choices=["run", "status", "open-long", "open-short", "close", "test"],
        default="status",
        help="运行模式"
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="使用测试网"
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="使用测试模式（固定金额）"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1000.0,
        help="本金 (USDT)"
    )
    
    args = parser.parse_args()
    
    # 设置环境变量（如果通过命令行指定）
    if args.testnet:
        os.environ["OKX_USE_TESTNET"] = "true"
    if args.test_mode:
        os.environ["TEST_MODE"] = "true"
    if args.capital:
        os.environ["TRADING_CAPITAL"] = str(args.capital)
    
    # 加载配置
    config = get_config()
    
    # 创建机器人
    bot = TradingBot(config)
    
    # 根据模式执行
    if args.mode == "run":
        bot.start()
    elif args.mode == "status":
        bot.show_status()
    elif args.mode == "open-long":
        bot.manual_open_long()
    elif args.mode == "open-short":
        bot.manual_open_short()
    elif args.mode == "close":
        bot.manual_close_all()
    elif args.mode == "test":
        # 测试模式：只获取价格和显示策略参数
        print("测试模式 - 检查 API 连接和策略参数")
        bot.show_status()


if __name__ == "__main__":
    main()
