"""
SQLite 数据库模块
用于存储交易记录、持仓历史和统计数据
"""
import sqlite3
import os
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TradeRecord:
    """交易记录"""
    id: Optional[int]
    symbol: str
    direction: str  # LONG / SHORT
    side: str  # BUY / SELL
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    contract_value: float  # 合约总金额 (价格 × 张数)
    pnl: Optional[float]
    pnl_pct: Optional[float]
    is_reserve: bool  # 是否是保留仓位
    status: str  # OPEN / CLOSED
    drop_type: Optional[str]  # normal / large
    drop_amount: Optional[float]
    created_at: datetime
    closed_at: Optional[datetime]
    notes: Optional[str]


@dataclass
class PositionSnapshot:
    """持仓快照"""
    id: Optional[int]
    symbol: str
    quantity: float
    avg_price: float
    contract_value: float
    unrealized_pnl: float
    snapshot_time: datetime


class TradingDatabase:
    """交易数据库"""
    
    def __init__(self, db_path: str = None):
        """
        初始化数据库
        
        Args:
            db_path: 数据库文件路径，默认为项目根目录下的 trading.db
        """
        if db_path is None:
            # 默认路径：项目根目录
            project_root = Path(__file__).parent.parent
            db_path = str(project_root / "trading.db")
        
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_database(self):
        """初始化数据库表"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 交易记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                contract_value REAL NOT NULL,
                pnl REAL,
                pnl_pct REAL,
                is_reserve INTEGER DEFAULT 0,
                status TEXT DEFAULT 'OPEN',
                drop_type TEXT,
                drop_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                notes TEXT
            )
        """)
        
        # 持仓快照表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS position_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                contract_value REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 每日统计表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_trades INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                total_volume REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 保留仓位表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reserved_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                target_price REAL,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        self.logger.info(f"数据库初始化完成: {self.db_path}")
    
    # ==================== 交易记录操作 ====================
    
    def record_buy(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        direction: str = "LONG",
        drop_type: str = None,
        drop_amount: float = None,
        notes: str = None
    ) -> int:
        """
        记录买入交易
        
        Returns:
            交易记录 ID
        """
        contract_value = entry_price * quantity
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trades (
                symbol, direction, side, entry_price, quantity, 
                contract_value, drop_type, drop_amount, status, notes
            ) VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (symbol, direction, entry_price, quantity, contract_value, 
              drop_type, drop_amount, notes))
        
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        self.logger.info(f"记录买入: ID={trade_id}, {quantity}张 @ ${entry_price:.2f}")
        return trade_id
    
    def record_sell(
        self,
        symbol: str,
        exit_price: float,
        quantity: float,
        entry_price: float,
        pnl: float,
        pnl_pct: float,
        direction: str = "LONG",
        is_reserve: bool = False,
        notes: str = None
    ) -> int:
        """
        记录卖出交易
        
        Returns:
            交易记录 ID
        """
        contract_value = exit_price * quantity
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trades (
                symbol, direction, side, entry_price, exit_price, quantity,
                contract_value, pnl, pnl_pct, is_reserve, status, closed_at, notes
            ) VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, 'CLOSED', CURRENT_TIMESTAMP, ?)
        """, (symbol, direction, entry_price, exit_price, quantity,
              contract_value, pnl, pnl_pct, 1 if is_reserve else 0, notes))
        
        trade_id = cursor.lastrowid
        
        # 更新每日统计
        self._update_daily_stats(conn, pnl, contract_value)
        
        conn.commit()
        conn.close()
        
        self.logger.info(f"记录卖出: ID={trade_id}, {quantity}张 @ ${exit_price:.2f}, 盈亏 ${pnl:.2f}")
        return trade_id
    
    def _update_daily_stats(self, conn: sqlite3.Connection, pnl: float, volume: float):
        """更新每日统计"""
        today = datetime.now().strftime("%Y-%m-%d")
        cursor = conn.cursor()
        
        # 检查今日记录是否存在
        cursor.execute("SELECT id FROM daily_stats WHERE date = ?", (today,))
        row = cursor.fetchone()
        
        if row:
            # 更新现有记录
            cursor.execute("""
                UPDATE daily_stats SET
                    total_trades = total_trades + 1,
                    win_count = win_count + ?,
                    loss_count = loss_count + ?,
                    total_pnl = total_pnl + ?,
                    total_volume = total_volume + ?
                WHERE date = ?
            """, (1 if pnl > 0 else 0, 1 if pnl <= 0 else 0, pnl, volume, today))
        else:
            # 创建新记录
            cursor.execute("""
                INSERT INTO daily_stats (date, total_trades, win_count, loss_count, total_pnl, total_volume)
                VALUES (?, 1, ?, ?, ?, ?)
            """, (today, 1 if pnl > 0 else 0, 1 if pnl <= 0 else 0, pnl, volume))
    
    def get_open_trades(self, symbol: str = None) -> List[Dict]:
        """获取未平仓交易"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute("""
                SELECT * FROM trades WHERE status = 'OPEN' AND symbol = ?
                ORDER BY created_at DESC
            """, (symbol,))
        else:
            cursor.execute("""
                SELECT * FROM trades WHERE status = 'OPEN'
                ORDER BY created_at DESC
            """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_trade_history(
        self,
        symbol: str = None,
        limit: int = 100,
        start_date: str = None,
        end_date: str = None
    ) -> List[Dict]:
        """获取交易历史"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    # ==================== 保留仓位操作 ====================
    
    def add_reserved_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        target_price: float = None
    ) -> int:
        """添加保留仓位"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reserved_positions (symbol, entry_price, quantity, target_price)
            VALUES (?, ?, ?, ?)
        """, (symbol, entry_price, quantity, target_price))
        
        reserve_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        self.logger.info(f"添加保留仓位: ID={reserve_id}, {quantity}张 @ ${entry_price:.2f}")
        return reserve_id
    
    def get_reserved_positions(self, symbol: str = None) -> List[Dict]:
        """获取保留仓位"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute("""
                SELECT * FROM reserved_positions 
                WHERE status = 'ACTIVE' AND symbol = ?
                ORDER BY created_at DESC
            """, (symbol,))
        else:
            cursor.execute("""
                SELECT * FROM reserved_positions WHERE status = 'ACTIVE'
                ORDER BY created_at DESC
            """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def close_reserved_position(self, reserve_id: int):
        """关闭保留仓位"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE reserved_positions 
            SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (reserve_id,))
        
        conn.commit()
        conn.close()
    
    def get_total_reserved_quantity(self, symbol: str = None) -> float:
        """获取保留仓位总张数"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute("""
                SELECT COALESCE(SUM(quantity), 0) as total
                FROM reserved_positions 
                WHERE status = 'ACTIVE' AND symbol = ?
            """, (symbol,))
        else:
            cursor.execute("""
                SELECT COALESCE(SUM(quantity), 0) as total
                FROM reserved_positions WHERE status = 'ACTIVE'
            """)
        
        row = cursor.fetchone()
        conn.close()
        
        return row["total"] if row else 0
    
    # ==================== 持仓快照操作 ====================
    
    def save_position_snapshot(
        self,
        symbol: str,
        quantity: float,
        avg_price: float,
        unrealized_pnl: float = 0
    ):
        """保存持仓快照"""
        contract_value = avg_price * quantity
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO position_snapshots (symbol, quantity, avg_price, contract_value, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?)
        """, (symbol, quantity, avg_price, contract_value, unrealized_pnl))
        
        conn.commit()
        conn.close()
    
    def get_position_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """获取持仓历史"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute("""
                SELECT * FROM position_snapshots 
                WHERE symbol = ?
                ORDER BY snapshot_time DESC LIMIT ?
            """, (symbol, limit))
        else:
            cursor.execute("""
                SELECT * FROM position_snapshots 
                ORDER BY snapshot_time DESC LIMIT ?
            """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    # ==================== 统计查询 ====================
    
    def get_statistics(self, symbol: str = None) -> Dict:
        """获取交易统计"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 总体统计
        if symbol:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(SUM(contract_value), 0) as total_volume
                FROM trades 
                WHERE side = 'SELL' AND symbol = ?
            """, (symbol,))
        else:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(SUM(contract_value), 0) as total_volume
                FROM trades WHERE side = 'SELL'
            """)
        
        row = cursor.fetchone()
        conn.close()
        
        total_trades = row["total_trades"] or 0
        win_count = row["win_count"] or 0
        
        return {
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": row["loss_count"] or 0,
            "win_rate": (win_count / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl": row["total_pnl"] or 0,
            "total_volume": row["total_volume"] or 0,
            "reserved_quantity": self.get_total_reserved_quantity(symbol)
        }
    
    def get_daily_stats(self, date: str = None) -> Dict:
        """获取每日统计"""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (date,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            total_trades = row["total_trades"] or 0
            win_count = row["win_count"] or 0
            return {
                "date": date,
                "total_trades": total_trades,
                "win_count": win_count,
                "loss_count": row["loss_count"] or 0,
                "win_rate": (win_count / total_trades * 100) if total_trades > 0 else 0,
                "total_pnl": row["total_pnl"] or 0,
                "total_volume": row["total_volume"] or 0
            }
        else:
            return {
                "date": date,
                "total_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "total_volume": 0
            }
    
    def get_recent_stats(self, days: int = 7) -> List[Dict]:
        """获取最近几天的统计"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM daily_stats 
            ORDER BY date DESC LIMIT ?
        """, (days,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    db = TradingDatabase("test_trading.db")
    
    # 测试买入记录
    buy_id = db.record_buy(
        symbol="SOL-USDT-SWAP",
        entry_price=130.0,
        quantity=2,
        drop_type="normal",
        drop_amount=3.5,
        notes="测试买入"
    )
    print(f"买入记录 ID: {buy_id}")
    
    # 测试卖出记录
    sell_id = db.record_sell(
        symbol="SOL-USDT-SWAP",
        exit_price=133.5,
        quantity=1,
        entry_price=130.0,
        pnl=3.5,
        pnl_pct=2.69,
        is_reserve=False,
        notes="测试卖出"
    )
    print(f"卖出记录 ID: {sell_id}")
    
    # 测试保留仓位
    reserve_id = db.add_reserved_position(
        symbol="SOL-USDT-SWAP",
        entry_price=130.0,
        quantity=1,
        target_price=140.0
    )
    print(f"保留仓位 ID: {reserve_id}")
    
    # 获取统计
    stats = db.get_statistics("SOL-USDT-SWAP")
    print(f"\n统计数据:")
    print(f"  总交易次数: {stats['total_trades']}")
    print(f"  胜率: {stats['win_rate']:.1f}%")
    print(f"  累计盈亏: ${stats['total_pnl']:.2f}")
    print(f"  保留仓位: {stats['reserved_quantity']} 张")
    
    # 获取交易历史
    history = db.get_trade_history(limit=10)
    print(f"\n最近交易记录: {len(history)} 条")
    for trade in history:
        print(f"  {trade['side']} {trade['quantity']}张 @ ${trade['entry_price']:.2f}")
    
    # 清理测试数据库
    os.remove("test_trading.db")
    print("\n测试完成，已清理测试数据库")
