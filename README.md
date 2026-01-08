# TradeBotMini - OKX SOL 全仓合约交易机器人

一个针对 OKX 交易所 SOL-USDT 永续合约的自动化交易机器人，支持**网格分批买入策略**、动态止盈、本金限制保护、SQLite 数据库存储和 Telegram 通知。

## 功能特点

- **网格分批买入**: 根据价格跌幅自动分批买入，固定张数
- **动态止盈策略**: 价格越低利润目标越高，价格越高利润目标越低
- **分批止盈**: 卖出大部分仓位，保留部分等更高价格卖出
- **本金限制保护**: 自动检查持仓是否超过本金限制，超限时发送警告
- **安全价格防护**: 价格超出安全范围自动停止交易
- **SQLite 数据库**: 持久化存储交易记录、持仓历史和统计数据
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

## 数据库功能

交易机器人使用 SQLite 数据库持久化存储数据：

### 存储内容

- **交易记录**: 所有买入和卖出记录，包含价格、数量、盈亏等
- **保留仓位**: 等待更高价格卖出的保留仓位
- **持仓快照**: 定期保存持仓状态
- **每日统计**: 每日交易次数、胜率、盈亏汇总

### 数据库文件

数据库文件默认保存在项目根目录：`trading.db`

### 查看数据

```bash
# 使用 sqlite3 命令行工具
sqlite3 trading.db

# 查看交易记录
SELECT * FROM trades ORDER BY created_at DESC LIMIT 10;

# 查看每日统计
SELECT * FROM daily_stats ORDER BY date DESC;

# 查看保留仓位
SELECT * FROM reserved_positions WHERE status = 'ACTIVE';
```

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
│   ├── config.py          # 配置管理（自动加载 .env）
│   ├── okx_client.py      # OKX API 客户端
│   ├── strategy.py        # 网格交易策略引擎
│   ├── telegram_notifier.py  # Telegram 通知
│   ├── database.py        # SQLite 数据库模块
│   └── main.py            # 主程序入口
├── tests/
│   └── test_strategy.py   # 策略测试
├── trading.db             # SQLite 数据库（运行后生成）
├── .env                   # 环境变量配置
├── .env.example           # 环境变量模板
├── requirements.txt       # Python 依赖
└── README.md              # 使用文档
```

## 状态显示示例

```
======================================================================
SOL 全仓合约交易机器人状态 (网格策略)
======================================================================
模式: 测试网(模拟盘)
交易对: SOL-USDT-SWAP
本金: 1000.0 USDT
默认杠杆: 2x
安全价格范围: $90 - $150
----------------------------------------------------------------------
网格配置:
  正常跌幅: $3.2-$3.6
  大跌幅: $5.0+
  高价区间 (≥$120): 正常 1 张, 大跌 2 张
  低价区间 (<$120): 正常 2 张, 大跌 3 张
  保留张数: 1 张 (涨 $10.0 后卖出)
----------------------------------------------------------------------
当前价格: $136.86
价格区间: HIGH
可交易: 是 ✓
目标利润: 2.48%
止盈价格: $140.25
最大合约金额: $1100.00
当前持仓价值: $0.00
剩余可用额度: $1100.00
----------------------------------------------------------------------
交易统计 (数据库):
  总交易次数: 15
  胜率: 86.7%
  累计盈亏: $45.60
  总交易量: $2850.00
  保留仓位: 3 张
----------------------------------------------------------------------
今日统计 (2026-01-08):
  交易次数: 5
  胜率: 80.0%
  今日盈亏: $12.30
----------------------------------------------------------------------
最近交易记录:
  2026-01-08 18:52 | 卖出 2张 @ $136.50, 盈亏 $3.50
  2026-01-08 18:45 | 买入 2张 @ $133.00
  2026-01-08 17:30 | 卖出 1张 @ $135.20, 盈亏 $2.10
======================================================================
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
3. **重要**: 先向您的 Bot 发送一条消息，否则 Bot 无法向您发送通知

## 注意事项

1. **风险提示**: 合约交易存在高风险，请谨慎操作
2. **资金安全**: 请勿投入超出承受能力的资金
3. **测试优先**: 建议先在测试网充分测试
4. **API 安全**: 妥善保管 API Key
5. **本金限制**: 超出限额时会自动取消买入并发送警告
6. **数据备份**: 定期备份 `trading.db` 数据库文件

## 许可证

MIT License

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。使用本机器人进行交易的风险由用户自行承担。
