# TradeBotMini - OKX SOL 全仓合约交易机器人

一个针对 OKX 交易所 SOL-USDT 永续合约的自动化交易机器人，支持动态杠杆策略、价格-利润线性关系、安全价格防护和 Telegram 通知。

## 功能特点

- **动态杠杆策略**: 根据 SOL 价格自动调整合约金额（1.1x 或 1.8x 本金）
- **价格-利润线性关系**: 价格越低利润目标越高，价格越高利润目标越低
- **安全价格防护**: 价格超出安全范围（$90-$150）自动停止交易
- **全仓合约**: 使用全仓模式，固定 2 倍杠杆
- **Telegram 通知**: 实时推送开仓、平仓、盈亏和安全警告通知
- **测试网支持**: 支持 OKX 模拟盘进行策略测试

## 交易策略

### 价格区间划分

| 价格区间 | SOL 价格范围 | 合约金额 | 利润目标 | 是否交易 |
|---------|-------------|---------|---------|---------|
| 低价区间 | $90 - $120 | 本金 × 1.8 | 3.0% - 4.5% | ✅ 可交易 |
| 高价区间 | $120 - $150 | 本金 × 1.1 | 2.3% - 2.7% | ✅ 可交易 |
| 安全区间 | < $90 或 > $150 | - | - | ❌ 停止交易 |

### 利润计算规则

**低价区间 ($90 - $120)**:
- 价格 $120 → 利润目标 3.0%
- 价格 $90 → 利润目标 4.5%
- 价格越低，利润目标线性递增

**高价区间 ($120 - $150)**:
- 价格 $120 → 利润目标 2.7%
- 价格 $150 → 利润目标 2.3%
- 价格越高，利润目标线性递减

### 安全防护

当 SOL 价格超出安全范围时，机器人会：
1. 停止新开仓操作
2. 发送 Telegram 安全警告
3. 等待价格回归安全范围后自动恢复

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/tradeBotMini.git
cd tradeBotMini
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

复制环境变量模板并填入您的配置:

```bash
cp .env.example .env
```

编辑 `.env` 文件:

```bash
# OKX API 配置
OKX_API_KEY=your_api_key_here
OKX_SECRET_KEY=your_secret_key_here
OKX_PASSPHRASE=your_passphrase_here
OKX_USE_TESTNET=true

# Telegram 配置
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TELEGRAM_ENABLED=true

# 交易配置
TRADING_CAPITAL=1000.0

# 安全价格范围
SAFE_PRICE_MIN=90.0
SAFE_PRICE_MAX=150.0
```

### 4. 运行机器人

```bash
# 加载环境变量
export $(cat .env | xargs)

# 查看当前状态
python src/main.py --mode status

# 启动机器人
python src/main.py --mode run

# 使用测试网
python src/main.py --mode run --testnet

# 指定本金
python src/main.py --mode run --capital 2000
```

## 命令行参数

| 参数 | 说明 | 可选值 |
|-----|------|-------|
| `--mode` | 运行模式 | `run`, `status`, `open-long`, `open-short`, `close`, `test` |
| `--testnet` | 使用测试网 | 无需值 |
| `--test-mode` | 使用固定金额 | 无需值 |
| `--capital` | 本金 (USDT) | 数字，默认 1000 |

### 运行模式说明

- `run`: 启动自动交易机器人
- `status`: 显示当前状态和策略参数
- `open-long`: 手动开多仓
- `open-short`: 手动开空仓
- `close`: 手动平仓
- `test`: 测试模式，检查 API 连接

## 获取 API 配置

### OKX API

1. 登录 [OKX 官网](https://www.okx.com)
2. 进入 **账户** → **API**
3. 创建新的 API Key
4. 权限设置: 勾选 **交易** 权限
5. 记录 API Key、Secret Key 和 Passphrase

> ⚠️ **安全提示**: 
> - 请勿将 API Key 分享给他人
> - 建议设置 IP 白名单
> - 测试时请先使用模拟盘

### OKX 测试网（模拟盘）

1. 登录 OKX 网页版
2. 切换到 **模拟交易** 模式
3. 在模拟交易模式下创建 API Key
4. 设置 `OKX_USE_TESTNET=true`

### Telegram Bot

1. 在 Telegram 中搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称
4. 获取 Bot Token

### Telegram Chat ID

1. 在 Telegram 中搜索 [@userinfobot](https://t.me/userinfobot)
2. 发送任意消息
3. 获取您的 Chat ID

## 项目结构

```
tradeBotMini/
├── src/
│   ├── __init__.py
│   ├── config.py          # 配置管理
│   ├── okx_client.py      # OKX API 客户端（支持正式网/测试网）
│   ├── strategy.py        # 交易策略引擎
│   ├── telegram_notifier.py  # Telegram 通知
│   └── main.py            # 主程序入口
├── tests/
│   └── test_strategy.py   # 策略测试
├── .env.example           # 环境变量模板
├── requirements.txt       # Python 依赖
└── README.md              # 使用文档
```

## 策略参数配置

可以在 `src/config.py` 中修改策略参数:

```python
@dataclass
class TradingStrategy:
    # 价格阈值（高低价区间分界线）
    price_threshold: float = 120.0
    
    # 安全价格范围
    safe_price_min: float = 90.0   # 低于此价格停止交易
    safe_price_max: float = 150.0  # 高于此价格停止交易
    
    # 合约金额比例
    high_price_leverage_ratio: float = 1.1  # 高价区间 110%
    low_price_leverage_ratio: float = 1.8   # 低价区间 180%
    
    # 利润目标范围
    high_price_profit_min: float = 2.3  # 高价区间最低利润 (价格150时)
    high_price_profit_max: float = 2.7  # 高价区间最高利润 (价格120时)
    low_price_profit_min: float = 3.0   # 低价区间最低利润 (价格120时)
    low_price_profit_max: float = 4.5   # 低价区间最高利润 (价格90时)
    
    # 默认杠杆
    default_leverage: int = 2
```

## Telegram 通知示例

### 开仓通知
```
🟢 开仓通知 🟢

📊 交易对: SOL-USDT-SWAP
📈 方向: 做多
💰 开仓价格: $125.50
📦 持仓张数: 8.76
💵 合约总金额: $1099.38
   ($125.50 × 8.76 张)
⚡ 杠杆倍数: 2x
🎯 目标利润: 2.65%
🏁 止盈价格: $128.82

⏰ 2025-01-08 12:30:45
```

### 安全警告
```
🔴 安全警告

价格 $85.00 低于安全下限 $90
安全范围: $90 - $150

⚠️ 交易功能已暂停，等待价格回归安全范围
```

### 平仓通知
```
💰 平仓通知 💰

📊 交易对: SOL-USDT-SWAP
📈 方向: 做多
💰 开仓价格: $125.50
💵 平仓价格: $128.82
📦 持仓张数: 8.76
💎 合约总金额: $1128.46
   ($128.82 × 8.76 张)

━━━━━ 交易结果 ━━━━━
💰 盈利: $29.08 (+2.65%)

━━━━━ 累计统计 ━━━━━
📈 累计盈亏: $156.80

⏰ 2025-01-08 14:15:30
```

## 注意事项

1. **风险提示**: 合约交易存在高风险，请谨慎操作
2. **资金安全**: 请勿投入超出承受能力的资金
3. **测试优先**: 建议先在测试网充分测试后再使用实盘
4. **API 安全**: 妥善保管 API Key，定期更换
5. **网络稳定**: 确保运行环境网络稳定
6. **安全防护**: 价格超出 $90-$150 范围时会自动停止交易

## 许可证

MIT License

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。使用本机器人进行交易的风险由用户自行承担。作者不对任何因使用本软件造成的损失负责。
