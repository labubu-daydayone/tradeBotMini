"""
OKX SOL 全仓合约交易机器人配置文件
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class OKXConfig:
    """OKX API 配置"""
    api_key: str = ""
    secret_key: str = ""
    passphrase: str = ""
    # 是否使用测试网
    use_testnet: bool = True
    
    # API 端点
    MAINNET_URL: str = "https://www.okx.com"
    TESTNET_URL: str = "https://www.okx.com"  # OKX 测试网使用相同域名，通过 header 区分
    
    @property
    def base_url(self) -> str:
        return self.TESTNET_URL if self.use_testnet else self.MAINNET_URL
    
    @property
    def simulated_trading(self) -> str:
        """返回模拟交易标志，1 表示模拟盘，0 表示实盘"""
        return "1" if self.use_testnet else "0"


@dataclass
class TelegramConfig:
    """Telegram 配置"""
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True


@dataclass
class TradingStrategy:
    """交易策略配置"""
    # 交易对
    symbol: str = "SOL-USDT-SWAP"
    
    # 价格阈值
    price_threshold: float = 120.0
    
    # 合约金额配置
    # 价格 >= 120 时，合约总金额为本金的 110%
    high_price_leverage_ratio: float = 1.10
    # 价格 < 120 时，合约总金额固定为 1800 USDT
    low_price_contract_amount: float = 1800.0
    
    # 利润百分比配置（基于价格的线性关系）
    # 价格 >= 120: 利润目标 2.3% - 2.7%
    high_price_profit_min: float = 2.3
    high_price_profit_max: float = 2.7
    
    # 价格 < 120: 利润目标 3.0% - 4.5%
    low_price_profit_min: float = 3.0
    low_price_profit_max: float = 4.5
    
    # 价格区间定义（用于计算线性利润）
    # 高价区间: 120 - 200
    high_price_range_min: float = 120.0
    high_price_range_max: float = 200.0
    
    # 低价区间: 50 - 120
    low_price_range_min: float = 50.0
    low_price_range_max: float = 120.0
    
    # 本金（USDT）
    capital: float = 1000.0
    
    # 全仓模式
    margin_mode: str = "cross"  # cross: 全仓, isolated: 逐仓
    
    # 杠杆倍数（默认值，实际会根据策略动态调整）
    default_leverage: int = 10


@dataclass
class AppConfig:
    """应用总配置"""
    okx: OKXConfig
    telegram: TelegramConfig
    strategy: TradingStrategy
    
    # 日志配置
    log_level: str = "INFO"
    log_file: str = "trading_bot.log"
    
    # 交易间隔（秒）
    check_interval: int = 5
    
    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量加载配置"""
        okx_config = OKXConfig(
            api_key=os.getenv("OKX_API_KEY", ""),
            secret_key=os.getenv("OKX_SECRET_KEY", ""),
            passphrase=os.getenv("OKX_PASSPHRASE", ""),
            use_testnet=os.getenv("OKX_USE_TESTNET", "true").lower() == "true"
        )
        
        telegram_config = TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
        )
        
        strategy_config = TradingStrategy(
            capital=float(os.getenv("TRADING_CAPITAL", "1000.0"))
        )
        
        return cls(
            okx=okx_config,
            telegram=telegram_config,
            strategy=strategy_config,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            check_interval=int(os.getenv("CHECK_INTERVAL", "5"))
        )


# 默认配置实例
def get_config() -> AppConfig:
    """获取配置实例"""
    return AppConfig.from_env()
