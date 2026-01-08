# TradeBotMini - OKX SOL 全仓合约交易机器人

一个针对 OKX 交易所 SOL-USDT 永续合约的自动化交易机器人，支持**网格分批买入策略**、动态止盈、本金限制保护和 Telegram 通知。

## 功能特点

- **网格分批买入**: 根据价格跌幅自动分批买入，固定张数
- **动态止盈策略**: 价格越低利润目标越高，价格越高利润目标越低
- **分批止盈**: 卖出大部分仓位，保留部分等更高价格卖出
- **本金限制保护**: 自动检查持仓是否超过本金限制，超限时发送警告
- **安全价格防护**: 价格超出安全范围自动停止交易
- **Telegram 通知**: 实时推送买入、卖出、盈亏和警告通知
- **测试网支持**: 支持 OKX 模拟盘进行策略测试

## 网格交易策略

### 买入策略

根据价格跌幅和当前价格区间，自动决定买入张数：

| 价格区间 | 正常跌幅 ($3.2-$3.6) | 大跌幅 (≥$5) |
|---------|---------------------|-------------|
| 高价区间 (≥$120) | 买入 1 张 | 买入 2 张 |
| 低价区间 ($90-$120) | 买入 2 张 | 买入 3 张 |

### 本金限制

| 价格区间 | 最大合约金额 | 说明 |
|---------|-------------|------|
| 高价区间 (≥$120) | 本金 × 1.1 | 如本金 1000，最大 1100 |
| 低价区间 ($90-$120) | 本金 × 1.8 | 如本金 1000，最大 1800 |

当持仓价值 + 新买入金额 > 最大限额时，会发送 Telegram 警告并取消买入。

### 卖出策略（分批止盈）

1. **策略止盈**: 达到目标利润时，卖出大部分仓位，保留 1 张
2. **保留仓位止盈**: 保留的 1 张等价格涨 $10 后再卖出

### 利润目标

| 价格区间 | SOL 价格 | 利润目标 |
|---------|---------|---------|
| 低价区间 | $90 | 4.5% |
| 低价区间 | $120 | 3.0% |
| 高价区间 | $120 | 2.7% |
| 高价区间 | $150 | 2.3% |

### 安全防护

- 价格 < $90 或 > $150 时自动停止交易
- 发送 Telegram 安全警告
- 价格回归安全范围后自动恢复

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

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# OKX API 配置
OKX_API_KEY=your_api_key_here
OKX_SECRET_KEY=your_secret_key_here
OKX_PASSPHRASE=your_passphrase_here
OKX_USE_TESTNET=true

# Telegram 配置
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# 交易配置
TRADING_CAPITAL=1000.0

# 网格配置
GRID_NORMAL_DROP_MIN=3.2
GRID_NORMAL_DROP_MAX=3.6
GRID_LARGE_DROP=5.0
GRID_HIGH_NORMAL_QTY=1
GRID_HIGH_LARGE_QTY=2
GRID_LOW_NORMAL_QTY=2
GRID_LOW_LARGE_QTY=3
GRID_RESERVE_QTY=1
GRID_RESERVE_PROFIT=10.0
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

# 手动买入
python src/main.py --mode buy --quantity 2

# 手动卖出
python src/main.py --mode sell --quantity 1
```

## 命令行参数

| 参数 | 说明 | 可选值 |
|-----|------|-------|
| `--mode` | 运行模式 | `run`, `status`, `buy`, `sell`, `test` |
| `--testnet` | 使用测试网 | 无需值 |
| `--quantity` | 买入/卖出张数 | 整数 |
| `--capital` | 本金 (USDT) | 数字，默认 1000 |

## 项目结构

```
tradeBotMini/
├── src/
│   ├── __init__.py
│   ├── config.py          # 配置管理（含网格配置）
│   ├── okx_client.py      # OKX API 客户端
│   ├── strategy.py        # 网格交易策略引擎
│   ├── telegram_notifier.py  # Telegram 通知
│   └── main.py            # 主程序入口
├── tests/
│   └── test_strategy.py   # 策略测试
├── .env.example           # 环境变量模板
├── requirements.txt       # Python 依赖
└── README.md              # 使用文档
```

## Telegram 通知示例

### 网格买入通知
```
🟢 网格买入 🟢

📊 交易对: SOL-USDT-SWAP
📈 方向: 做多
💰 买入价格: $126.50
📦 买入张数: 2 张
💵 本次金额: $253.00

━━━━━ 触发条件 ━━━━━
📉 跌幅: $3.50 (正常跌幅)

━━━━━ 持仓状态 ━━━━━
📦 当前持仓: 5 张
💵 持仓价值: $632.50
🎯 最大额度: $1100.00
💰 剩余额度: $467.50
```

### 本金限制警告
```
⚠️ 本金限制警告 ⚠️

📊 当前价格: $130.00
📍 价格区间: 高价区间 (1.1x)

━━━━━ 额度状态 ━━━━━
💵 当前持仓: $910.00
📦 请求买入: $260.00
🚫 总计: $1170.00
🎯 最大限额: $1100.00

❌ 超出限额，买入已取消
```

### 策略止盈通知
```
💰 策略止盈 💰

📊 交易对: SOL-USDT-SWAP
📈 方向: 做多
💰 开仓价格: $126.50
💵 平仓价格: $129.92
📦 卖出张数: 4 张
📦 保留张数: 1 张
💎 卖出金额: $519.68

━━━━━ 交易结果 ━━━━━
📈 盈亏: +$13.68 (+2.70%)

━━━━━ 累计统计 ━━━━━
📈 累计盈亏: $156.80
```

## 配置说明

### 网格配置参数

| 参数 | 说明 | 默认值 |
|-----|------|-------|
| `GRID_NORMAL_DROP_MIN` | 正常跌幅最小值 | 3.2 |
| `GRID_NORMAL_DROP_MAX` | 正常跌幅最大值 | 3.6 |
| `GRID_LARGE_DROP` | 大跌幅阈值 | 5.0 |
| `GRID_HIGH_NORMAL_QTY` | 高价区间正常跌幅买入张数 | 1 |
| `GRID_HIGH_LARGE_QTY` | 高价区间大跌幅买入张数 | 2 |
| `GRID_LOW_NORMAL_QTY` | 低价区间正常跌幅买入张数 | 2 |
| `GRID_LOW_LARGE_QTY` | 低价区间大跌幅买入张数 | 3 |
| `GRID_RESERVE_QTY` | 保留张数 | 1 |
| `GRID_RESERVE_PROFIT` | 保留仓位止盈目标 (美元) | 10.0 |

## 获取 API 配置

### OKX API

1. 登录 [OKX 官网](https://www.okx.com)
2. 进入 **账户** → **API**
3. 创建新的 API Key，勾选 **交易** 权限
4. 记录 API Key、Secret Key 和 Passphrase

### OKX 测试网（模拟盘）

设置 `OKX_USE_TESTNET=true` 即可使用模拟盘，无需真实资金。

### Telegram Bot

1. 在 Telegram 中搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot` 创建机器人
3. 获取 Bot Token

### Telegram Chat ID

1. 搜索 [@userinfobot](https://t.me/userinfobot)
2. 发送任意消息获取 Chat ID

## 注意事项

1. **风险提示**: 合约交易存在高风险，请谨慎操作
2. **资金安全**: 请勿投入超出承受能力的资金
3. **测试优先**: 建议先在测试网充分测试
4. **API 安全**: 妥善保管 API Key
5. **本金限制**: 超出限额时会自动取消买入并发送警告

## 许可证

MIT License

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。使用本机器人进行交易的风险由用户自行承担。
