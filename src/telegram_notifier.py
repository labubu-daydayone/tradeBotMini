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
        
        Args:
            symbol: 交易对
            direction: 方向 (LONG/SHORT)
            entry_price: 开仓价格
            position_size: 持仓张数
            total_contract_value: 合约总金额 (价格 × 张数)
            leverage: 杠杆倍数
            target_profit_pct: 目标利润百分比
            take_profit_price: 止盈价格
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
        
        Args:
            symbol: 交易对
            direction: 方向 (LONG/SHORT)
            entry_price: 开仓价格
            exit_price: 平仓价格
            position_size: 持仓张数
            total_contract_value: 合约总金额 (平仓价格 × 张数)
            pnl: 盈亏金额
            pnl_pct: 盈亏百分比
            total_pnl: 累计盈亏
        """
        direction_cn = "做多" if direction.upper() == "LONG" else "做空"
        
        # 根据盈亏选择 emoji
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
        
        Args:
            current_price: 当前价格
            price_zone: 价格区间
            profit_target: 目标利润
            total_contract_value: 合约总金额 (价格 × 张数)
            position_size: 持仓张数
            leverage: 杠杆倍数
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


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    # 创建测试配置（需要填入真实的 token 和 chat_id 才能测试）
    config = TelegramConfig(
        bot_token="YOUR_BOT_TOKEN",
        chat_id="YOUR_CHAT_ID",
        enabled=False  # 设为 False 避免实际发送
    )
    
    notifier = TelegramNotifier(config)
    
    # 测试消息格式
    print("测试开仓通知格式:")
    # 模拟: 价格 $120, 开 5 张, 合约总金额 = 120 * 5 = 600
    entry_price = 120.0
    position_size = 5.0
    total_contract_value = entry_price * position_size  # 600
    
    notifier.send_trade_open_notification(
        symbol="SOL-USDT-SWAP",
        direction="LONG",
        entry_price=entry_price,
        position_size=position_size,
        total_contract_value=total_contract_value,
        leverage=2,
        target_profit_pct=2.7,
        take_profit_price=123.24
    )
