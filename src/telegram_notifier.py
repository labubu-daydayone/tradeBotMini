"""
Telegram 通知模块
用于发送交易通知和状态更新
"""
import logging
import requests
from typing import Optional
from datetime import datetime
from dataclasses import dataclass

from config import TelegramConfig


class TelegramNotifier:
    """Telegram 通知器"""
    
    def __init__(self, config: TelegramConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{config.bot_token}"
        
    def _send_request(self, method: str, data: dict) -> dict:
        """发送 Telegram API 请求"""
        url = f"{self.base_url}/{method}"
        try:
            response = requests.post(url, json=data, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Telegram API 请求失败: {e}")
            return {"ok": False, "error": str(e)}
    
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        发送消息
        
        Args:
            text: 消息内容
            parse_mode: 解析模式 (HTML, Markdown, MarkdownV2)
            
        Returns:
            是否发送成功
        """
        if not self.config.enabled:
            self.logger.debug("Telegram 通知已禁用")
            return True
        
        if not self.config.bot_token or not self.config.chat_id:
            self.logger.warning("Telegram 配置不完整，跳过通知")
            return False
        
        data = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        
        result = self._send_request("sendMessage", data)
        
        if result.get("ok"):
            self.logger.info("Telegram 消息发送成功")
            return True
        else:
            self.logger.error(f"Telegram 消息发送失败: {result}")
            return False
    
    def send_grid_buy_notification(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        quantity: int,
        total_contract_value: float,
        drop_amount: float,
        drop_type: str,
        current_position_qty: float,
        current_position_value: float,
        max_amount: float,
        remaining_amount: float
    ) -> bool:
        """
        发送网格买入通知
        
        Args:
            symbol: 交易对
            direction: 方向
            entry_price: 买入价格
            quantity: 买入张数
            total_contract_value: 本次买入金额
            drop_amount: 跌幅金额
            drop_type: 跌幅类型 (normal/large)
            current_position_qty: 当前持仓张数
            current_position_value: 当前持仓价值
            max_amount: 最大限额
            remaining_amount: 剩余额度
        """
        drop_type_cn = "大跌" if drop_type == "large" else "正常跌幅"
        direction_cn = "做多" if direction.upper() == "LONG" else "做空"
        
        message = f"""
🟢 <b>网格买入</b> 🟢

📊 <b>交易对:</b> {symbol}
📈 <b>方向:</b> {direction_cn}
💰 <b>买入价格:</b> ${entry_price:.2f}
📦 <b>买入张数:</b> {quantity} 张
💵 <b>本次金额:</b> ${total_contract_value:.2f}

<b>━━━━━ 触发条件 ━━━━━</b>
📉 <b>跌幅:</b> ${drop_amount:.2f} ({drop_type_cn})

<b>━━━━━ 持仓状态 ━━━━━</b>
📦 <b>当前持仓:</b> {current_position_qty:.0f} 张
💵 <b>持仓价值:</b> ${current_position_value:.2f}
🎯 <b>最大额度:</b> ${max_amount:.2f}
💰 <b>剩余额度:</b> ${remaining_amount:.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_grid_sell_notification(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        sell_quantity: int,
        reserve_quantity: int,
        total_contract_value: float,
        pnl: float,
        pnl_pct: float,
        is_reserve_sell: bool,
        total_pnl: float
    ) -> bool:
        """
        发送网格卖出通知
        
        Args:
            symbol: 交易对
            direction: 方向
            entry_price: 开仓价格
            exit_price: 平仓价格
            sell_quantity: 卖出张数
            reserve_quantity: 保留张数
            total_contract_value: 卖出金额
            pnl: 盈亏金额
            pnl_pct: 盈亏百分比
            is_reserve_sell: 是否是保留仓位卖出
            total_pnl: 累计盈亏
        """
        direction_cn = "做多" if direction.upper() == "LONG" else "做空"
        sell_type = "保留仓位止盈" if is_reserve_sell else "策略止盈"
        
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        pnl_sign = "+" if pnl >= 0 else ""
        
        message = f"""
💰 <b>{sell_type}</b> 💰

📊 <b>交易对:</b> {symbol}
📈 <b>方向:</b> {direction_cn}
💰 <b>开仓价格:</b> ${entry_price:.2f}
💵 <b>平仓价格:</b> ${exit_price:.2f}
📦 <b>卖出张数:</b> {sell_quantity} 张
📦 <b>保留张数:</b> {reserve_quantity} 张
💎 <b>卖出金额:</b> ${total_contract_value:.2f}

<b>━━━━━ 交易结果 ━━━━━</b>
{pnl_emoji} <b>盈亏:</b> ${pnl_sign}{pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)

<b>━━━━━ 累计统计 ━━━━━</b>
📈 <b>累计盈亏:</b> ${total_pnl:.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_position_limit_warning(
        self,
        current_price: float,
        current_position_value: float,
        requested_amount: float,
        max_amount: float,
        zone: str
    ) -> bool:
        """
        发送本金限制警告
        
        Args:
            current_price: 当前价格
            current_position_value: 当前持仓价值
            requested_amount: 请求买入金额
            max_amount: 最大限额
            zone: 价格区间
        """
        zone_cn = "高价区间" if zone == "high" else "低价区间"
        ratio = "1.1x" if zone == "high" else "1.8x"
        
        message = f"""
⚠️ <b>本金限制警告</b> ⚠️

📊 <b>当前价格:</b> ${current_price:.2f}
📍 <b>价格区间:</b> {zone_cn} ({ratio})

<b>━━━━━ 额度状态 ━━━━━</b>
💵 <b>当前持仓:</b> ${current_position_value:.2f}
📦 <b>请求买入:</b> ${requested_amount:.2f}
🚫 <b>总计:</b> ${current_position_value + requested_amount:.2f}
🎯 <b>最大限额:</b> ${max_amount:.2f}

❌ 超出限额，买入已取消

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_trade_open_notification(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        position_size: float,
        total_contract_value: float,
        leverage: int,
        target_profit_pct: float,
        take_profit_price: float
    ) -> bool:
        """
        发送开仓通知
        """
        emoji = "🟢" if direction.upper() == "LONG" else "🔴"
        direction_cn = "做多" if direction.upper() == "LONG" else "做空"
        
        message = f"""
{emoji} <b>开仓通知</b> {emoji}

📊 <b>交易对:</b> {symbol}
📈 <b>方向:</b> {direction_cn}
💰 <b>开仓价格:</b> ${entry_price:.2f}
📦 <b>持仓张数:</b> {position_size:.2f}
💵 <b>合约总金额:</b> ${total_contract_value:.2f}
   <i>(${entry_price:.2f} × {position_size:.2f} 张)</i>
⚡ <b>杠杆倍数:</b> {leverage}x
🎯 <b>目标利润:</b> {target_profit_pct:.2f}%
🏁 <b>止盈价格:</b> ${take_profit_price:.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_trade_close_notification(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        position_size: float,
        total_contract_value: float,
        pnl: float,
        pnl_pct: float,
        total_pnl: float = None
    ) -> bool:
        """
        发送平仓通知
        """
        direction_cn = "做多" if direction.upper() == "LONG" else "做空"
        
        if pnl > 0:
            result_emoji = "💰"
            result_text = "盈利"
        else:
            result_emoji = "📉"
            result_text = "亏损"
        
        message = f"""
{result_emoji} <b>平仓通知</b> {result_emoji}

📊 <b>交易对:</b> {symbol}
📈 <b>方向:</b> {direction_cn}
💰 <b>开仓价格:</b> ${entry_price:.2f}
💵 <b>平仓价格:</b> ${exit_price:.2f}
📦 <b>持仓张数:</b> {position_size:.2f}
💎 <b>合约总金额:</b> ${total_contract_value:.2f}
   <i>(${exit_price:.2f} × {position_size:.2f} 张)</i>

<b>━━━━━ 交易结果 ━━━━━</b>
{result_emoji} <b>{result_text}:</b> ${pnl:.2f} ({pnl_pct:+.2f}%)
"""
        
        if total_pnl is not None:
            total_emoji = "📈" if total_pnl >= 0 else "📉"
            message += f"""
<b>━━━━━ 累计统计 ━━━━━</b>
{total_emoji} <b>累计盈亏:</b> ${total_pnl:.2f}
"""
        
        message += f"""
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_strategy_update(
        self,
        current_price: float,
        price_zone: str,
        profit_target: float,
        total_contract_value: float,
        position_size: float,
        leverage: int
    ) -> bool:
        """
        发送策略参数更新通知
        """
        zone_emoji = "🔥" if price_zone.upper() == "HIGH" else "❄️"
        zone_cn = "高价区间" if price_zone.upper() == "HIGH" else "低价区间"
        
        message = f"""
{zone_emoji} <b>策略参数更新</b> {zone_emoji}

💲 <b>当前价格:</b> ${current_price:.2f}
📊 <b>价格区间:</b> {zone_cn}
🎯 <b>目标利润:</b> {profit_target:.2f}%
📦 <b>开仓张数:</b> {position_size:.2f}
💵 <b>合约总金额:</b> ${total_contract_value:.2f}
   <i>(${current_price:.2f} × {position_size:.2f} 张)</i>
⚡ <b>杠杆倍数:</b> {leverage}x

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_error_notification(self, error_message: str) -> bool:
        """
        发送错误通知
        """
        message = f"""
⚠️ <b>系统错误</b> ⚠️

❌ <b>错误信息:</b>
<code>{error_message}</code>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_bot_status(
        self,
        status: str,
        current_price: float = None,
        has_position: bool = False,
        position_info: dict = None
    ) -> bool:
        """
        发送机器人状态通知
        """
        status_emoji = "🟢" if status == "running" else "🔴"
        status_cn = "运行中" if status == "running" else "已停止"
        
        message = f"""
{status_emoji} <b>机器人状态</b> {status_emoji}

📡 <b>状态:</b> {status_cn}
"""
        
        if current_price:
            message += f"💲 <b>SOL 价格:</b> ${current_price:.2f}\n"
        
        if has_position and position_info:
            direction = "做多" if position_info.get("direction") == "LONG" else "做空"
            entry_price = position_info.get('entry_price', 0)
            size = position_info.get('size', 0)
            total_value = entry_price * size
            message += f"""
<b>━━━━━ 当前持仓 ━━━━━</b>
📈 <b>方向:</b> {direction}
💰 <b>开仓价:</b> ${entry_price:.2f}
📦 <b>张数:</b> {size:.2f}
💵 <b>合约总金额:</b> ${total_value:.2f}
💎 <b>未实现盈亏:</b> ${position_info.get('unrealized_pnl', 0):.2f}
"""
        else:
            message += "📭 <b>持仓:</b> 无\n"
        
        message += f"""
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_safety_warning(
        self,
        current_price: float,
        safe_min: float,
        safe_max: float,
        is_below: bool
    ) -> bool:
        """
        发送安全警告
        """
        if is_below:
            reason = f"低于安全下限 ${safe_min:.0f}"
        else:
            reason = f"高于安全上限 ${safe_max:.0f}"
        
        message = f"""
🔴 <b>安全警告</b> 🔴

📊 <b>当前价格:</b> ${current_price:.2f}
⚠️ {reason}
📍 <b>安全范围:</b> ${safe_min:.0f} - ${safe_max:.0f}

❌ 交易功能已暂停
⏳ 等待价格回归安全范围

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_safety_restored(
        self,
        current_price: float,
        safe_min: float,
        safe_max: float
    ) -> bool:
        """
        发送安全恢复通知
        """
        message = f"""
🟢 <b>安全恢复</b> 🟢

📊 <b>当前价格:</b> ${current_price:.2f}
📍 <b>安全范围:</b> ${safe_min:.0f} - ${safe_max:.0f}

✅ 价格回归安全范围
✅ 交易功能已恢复

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_daily_summary(
        self,
        total_trades: int,
        win_count: int,
        loss_count: int,
        total_pnl: float,
        win_rate: float
    ) -> bool:
        """
        发送每日交易汇总
        """
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        
        message = f"""
📊 <b>每日交易汇总</b> 📊

📝 <b>总交易次数:</b> {total_trades}
✅ <b>盈利次数:</b> {win_count}
❌ <b>亏损次数:</b> {loss_count}
🎯 <b>胜率:</b> {win_rate:.1f}%

<b>━━━━━ 盈亏统计 ━━━━━</b>
{pnl_emoji} <b>今日盈亏:</b> ${total_pnl:.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())


    def send_fibonacci_trade_notification(
        self,
        action: str,
        price: float,
        quantity: int,
        target_position: int,
        current_position: int,
        fib_level: float,
        fib_price: float,
        reason: str,
        pnl: float = None
    ) -> bool:
        """
        发送斥波那契交易通知
        """
        if action.upper() == "BUY":
            emoji = "🟢"
            action_cn = "买入"
        else:
            emoji = "🔴"
            action_cn = "卖出"
        
        total_value = price * quantity
        
        message = f"""
{emoji} <b>斥波那契{action_cn}</b> {emoji}

📊 <b>交易对:</b> SOL-USDT-SWAP
💰 <b>价格:</b> ${price:.2f}
📦 <b>数量:</b> {quantity} 张
💵 <b>合约金额:</b> ${total_value:.2f}

<b>━━━━━ 斥波那契点位 ━━━━━</b>
📈 <b>触发级别:</b> {fib_level:.3f}
📍 <b>触发价格:</b> ${fib_price:.2f}

<b>━━━━━ 持仓状态 ━━━━━</b>
🎯 <b>目标持仓:</b> {target_position} 张
📦 <b>当前持仓:</b> {current_position} 张
"""
        
        if pnl is not None:
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            message += f"""
<b>━━━━━ 盈亏 ━━━━━</b>
{pnl_emoji} <b>本次盈亏:</b> ${pnl:.2f}
"""
        
        message += f"""
📝 <b>原因:</b> {reason}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())
    
    def send_fibonacci_status(
        self,
        current_price: float,
        current_position: int,
        target_position: int,
        fib_levels: list,
        next_buy_price: float = None,
        next_sell_price: float = None
    ) -> bool:
        """
        发送斥波那契策略状态
        """
        position_diff = target_position - current_position
        if position_diff > 0:
            diff_text = f"需买入 {position_diff} 张"
        elif position_diff < 0:
            diff_text = f"需卖出 {-position_diff} 张"
        else:
            diff_text = "已达目标"
        
        message = f"""
📈 <b>斥波那契策略状态</b> 📈

💲 <b>SOL 价格:</b> ${current_price:.2f}
📦 <b>当前持仓:</b> {current_position} 张
🎯 <b>目标持仓:</b> {target_position} 张
📊 <b>差异:</b> {diff_text}

<b>━━━━━ 下一触发点 ━━━━━</b>
"""
        
        if next_buy_price:
            message += f"🟢 <b>下一买入点:</b> ${next_buy_price:.2f}\n"
        if next_sell_price:
            message += f"🔴 <b>下一卖出点:</b> ${next_sell_price:.2f}\n"
        
        message += f"""
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.send_message(message.strip())


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    config = TelegramConfig(
        bot_token="YOUR_BOT_TOKEN",
        chat_id="YOUR_CHAT_ID",
        enabled=False
    )
    
    notifier = TelegramNotifier(config)
    
    print("Telegram 通知模块测试完成")
