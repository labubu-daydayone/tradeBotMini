# TradeBotMini - OKX SOL 全仓合约交易机器人

一个针对 OKX 交易所 SOL-USDT 永续合约的自动化交易机器人，支持**斐波那契网格策略**、**一级/二级限价单预挂**、SQLite 数据库存储和 Telegram 通知。

## 功能特点

- **斐波那契网格策略**: 根据斐波那契数列自动计算目标持仓，动态买卖
- **一级限价单**: 在相邻斐波那契点位预挂买卖限价单
- **二级限价单**: 在下一个斐波那契点位预挂限价单，额外偏移 ±1U，用于捕捉急涨急跌
- **随机价格偏移**: 使用 .2, .3, .6, .7 四个小数值避免被狙击
- **FIFO 记账**: 先进先出计算每笔真实盈亏
- **SQLite 数据库**: 持久化存储交易记录、持仓历史和统计数据
- **Telegram 通知**: 实时推送买入、卖出、盈亏通知
- **测试网支持**: 支持 OKX 模拟盘进行策略测试

## 斐波那契网格策略

斐波那契策略根据当前价格自动计算目标持仓，在关键斐波那契点位触发买卖。

### 配置参数

| 参数 | 说明 | 默认值 |
|-----|------|-------|
| `FIBONACCI_PRICE_MIN` | 最低价格 | 100.0 |
| `FIBONACCI_PRICE_MAX` | 最高价格 | 160.0 |
| `FIBONACCI_MAX_POSITION` | 最大持仓张数 | 40 |

### 斐波那契网格点位（$100 - $160，15个点位）

| 斐波那契 | 价格 | 目标持仓 |
|---------|------|----------|
| 0.000 | $100.00 | 40 张 |
| 0.090 | $105.40 | 36 张 |
| 0.146 | $108.76 | 34 张 |
| 0.200 | $112.00 | 32 张 |
| 0.236 | $114.16 | 30 张 |
| 0.300 | $118.00 | 28 张 |
| 0.382 | $122.92 | 24 张 |
| 0.450 | $127.00 | 22 张 |
| 0.500 | $130.00 | 20 张 |
| 0.550 | $133.00 | 18 张 |
| 0.618 | $137.08 | 15 张 |
| 0.700 | $142.00 | 12 张 |
| 0.764 | $145.84 | 9 张 |
| 0.854 | $151.24 | 5 张 |
| 1.000 | $160.00 | 0 张 |

## 一级/二级限价单系统

机器人同时挂两层限价单，用于捕捉正常波动和急涨急跌：

### 一级限价单 (L1)

在**相邻**斐波那契点位挂单：
- **买入 L1**: 下方第一个斐波那契点位 + 随机偏移
- **卖出 L1**: 上方第一个斐波那契点位 + 随机偏移

### 二级限价单 (L2)

在**下一个**斐波那契点位挂单，额外偏移 ±1U：
- **买入 L2**: 下方第二个斐波那契点位 + 随机偏移 - 1U（更低价格，捕捉急跌）
- **卖出 L2**: 上方第二个斐波那契点位 + 随机偏移 + 1U（更高价格，捕捉急涨）

### 示例

当前价格 $135，持仓 15 张：

**买入侧**:
| 级别 | 斐波那契点位 | 基准价格 | 挂单价格 | 数量 |
|-----|-------------|---------|---------|------|
| L1 | Fib 0.550 | $133.00 | $132.3 | 3 张 |
| L2 | Fib 0.500 | $130.00 | $128.3 (-1U) | 5 张 |

**卖出侧**:
| 级别 | 斐波那契点位 | 基准价格 | 挂单价格 | 数量 |
|-----|-------------|---------|---------|------|
| L1 | Fib 0.618 | $137.08 | $137.3 | 0 张 |
| L2 | Fib 0.700 | $142.00 | $143.3 (+1U) | 3 张 |

### 成交逻辑

- **L2 买入成交（急跌）**: L1 买入单保持不动，等价格回调时成交
- **L1 买入成交（正常）**: 取消 L2 买入单，在新点位重新挂单
- **L2 卖出成交（急涨）**: L1 卖出单保持不动，等价格回调时成交
- **L1 卖出成交（正常）**: 取消 L2 卖出单，在新点位重新挂单

### 价格计算公式

```
买入 L1 = 斐波那契基准价 - 1 + 随机偏移(.2/.3/.6/.7)
买入 L2 = 斐波那契基准价 - 1 + 随机偏移 - 1

卖出 L1 = 斐波那契基准价 + 随机偏移(.2/.3/.6/.7)
卖出 L2 = 斐波那契基准价 + 随机偏移 + 1
```

### 示例流程

```
启动，价格 $135 → 市价买入 15 张
预挂 L1 买入 @ $132.3 (3张)
预挂 L2 买入 @ $128.3 (5张)
预挂 L2 卖出 @ $143.3 (3张)

场景1: 正常下跌
价格跌到 $132.3 → L1 买入成交
  → 发送 Telegram 通知
  → 持仓变为 18 张
  → 取消 L2 买入单
  → 重新挂单: L1 @ $126.7, L2 @ $121.6

场景2: 急跌（wick）
价格急跌到 $128.3 → L2 买入成交
  → 发送 Telegram 通知
  → 持仓变为 20 张
  → L1 买入单保持不动 @ $132.3（等回调）
  → 只清空 L2 买入单

场景3: 急涨（wick）
价格急涨到 $143.3 → L2 卖出成交
  → 发送 Telegram 通知（含利润）
  → 持仓变为 12 张
  → L1 卖出单保持不动（如果有）
  → 只清空 L2 卖出单
```

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/labubu-daydayone/tradeBotMini.git
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

# 斐波那契策略配置
FIBONACCI_PRICE_MIN=100.0
FIBONACCI_PRICE_MAX=160.0
FIBONACCI_MAX_POSITION=40

# 日志配置
LOG_LEVEL=INFO
CHECK_INTERVAL=5
```

### 4. 运行机器人

```bash
# 查看当前状态
python src/main.py --mode status

# 启动机器人
python src/main.py --mode run

# 使用测试网
python src/main.py --mode run --testnet

# 测试斐波那契点位和价格偏移
python src/main.py --mode test

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

## 项目结构

```
tradeBotMini/
├── src/
│   ├── __init__.py
│   ├── config.py              # 配置管理（自动加载 .env）
│   ├── okx_client.py          # OKX API 客户端
│   ├── fibonacci_strategy.py  # 斐波那契策略引擎
│   ├── limit_order_manager.py # 限价单管理器（一级/二级）
│   ├── telegram_notifier.py   # Telegram 通知
│   ├── database.py            # SQLite 数据库模块
│   └── main.py                # 主程序入口
├── trading.db                 # SQLite 数据库（运行后生成）
├── .env                       # 环境变量配置
├── .env.example               # 环境变量模板
├── requirements.txt           # Python 依赖
└── README.md                  # 使用文档
```

## Telegram 通知格式

### 买入通知

```
🟢 斐波那契买入 🟢

💰 价格: $128.30
📦 数量: 5 张
💵 合约金额: $641.50
📦 当前持仓: 20 张
📝 原因: [L2] 限价单成交

⏰ 2026-01-08 12:30:45
```

### 卖出通知

```
🔴 斐波那契卖出 🔴

💰 价格: $143.30
📦 数量: 3 张
💵 合约金额: $429.90
📈 本次利润: $18.90
📦 剩余持仓: 12 张
📝 原因: [L2] 限价单成交

⏰ 2026-01-08 14:15:30
```

## 数据库功能

交易机器人使用 SQLite 数据库持久化存储数据：

### 存储内容

- **交易记录**: 所有买入和卖出记录，包含价格、数量、盈亏等
- **持仓批次**: FIFO 记账的持仓批次
- **每日统计**: 每日交易次数、胜率、盈亏汇总

### 数据库文件

数据库文件默认保存在项目根目录：`trading.db`

### 查看数据

```bash
# 使用 sqlite3 命令行工具
sqlite3 trading.db

# 查看交易记录
SELECT * FROM trades ORDER BY created_at DESC LIMIT 10;

# 查看持仓批次
SELECT * FROM position_lots WHERE quantity > 0;

# 查看每日统计
SELECT * FROM daily_stats ORDER BY date DESC;
```

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
3. **重要**: 先向您的 Bot 发送一条消息，否则 Bot 无法向您发送通知

## 注意事项

1. **风险提示**: 合约交易存在高风险，请谨慎操作
2. **资金安全**: 请勿投入超出承受能力的资金
3. **测试优先**: 建议先在测试网充分测试
4. **API 安全**: 妥善保管 API Key
5. **数据备份**: 定期备份 `trading.db` 数据库文件
6. **二级单风险**: 二级单用于捕捉急涨急跌，可能因价格快速反转而产生亏损

## 许可证

MIT License

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。使用本机器人进行交易的风险由用户自行承担。
