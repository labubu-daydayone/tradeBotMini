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
    
    # 价格阈值（高低价区间分界线）
    price_threshold: float = 120.0
    
    # 安全价格范围（超出此范围停止交易）
    safe_price_min: float = 90.0   # 低于此价格停止交易
    safe_price_max: float = 150.0  # 高于此价格停止交易
    
    # 合约金额配置（按本金比例计算）
    # 价格 >= 120 时，合约总金额 = 本金 * 1.1 倍
    high_price_leverage_ratio: float = 1.1
    # 价格 < 120 时，合约总金额 = 本金 * 1.8 倍
    low_price_leverage_ratio: float = 1.8
    
    # 利润百分比配置（基于价格的线性关系）
    # 价格 120-150 (高价区间): 利润目标 2.3% - 2.7%
    high_price_profit_min: float = 2.3  # 价格 150 时
    high_price_profit_max: float = 2.7  # 价格 120 时
    
    # 价格 90-120 (低价区间): 利润目标 3.0% - 4.5%
    low_price_profit_min: float = 3.0   # 价格 120 时
    low_price_profit_max: float = 4.5   # 价格 90 时
    
    # 价格区间定义（用于计算线性利润）
    # 高价区间: 120 - 150
    high_price_range_min: float = 120.0
    high_price_range_max: float = 150.0
    
    # 低价区间: 90 - 120
    low_price_range_min: float = 90.0
    low_price_range_max: float = 120.0
    
    # 本金（USDT）
    capital: float = 1000.0
    
    # 全仓模式
    margin_mode: str = "cross"  # cross: 全仓, isolated: 逐仓
    
    # 默认杠杆倍数（固定2倍杠杆）
    default_leverage: int = 2
    
    # 测试模式配置（写死的测试金额）
    test_mode: bool = False
    test_high_price_amount: float = 1100.0  # 测试模式高价区间固定金额
    test_low_price_amount: float = 1800.0   # 测试模式低价区间固定金额


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
            capital=float(os.getenv("TRADING_CAPITAL", "1000.0")),
            test_mode=os.getenv("TEST_MODE", "false").lower() == "true",
            safe_price_min=float(os.getenv("SAFE_PRICE_MIN", "90.0")),
            safe_price_max=float(os.getenv("SAFE_PRICE_MAX", "150.0"))
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
