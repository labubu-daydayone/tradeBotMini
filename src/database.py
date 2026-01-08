"""
SQLite 数据库模块
用于存储交易记录、持仓历史和统计数据
支持 FIFO (先进先出) 记账方式
"""
import sqlite3
import os
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PositionLot:
    """持仓批次（用于 FIFO 记账）"""
    id: Optional[int]
    symbol: str
    entry_price: float
    quantity: float  # 剩余数量
    original_quantity: float  # 原始买入数量
    is_manual: bool  # 是否手动买入（初始持仓）
    created_at: datetime
    notes: Optional[str] = None


@dataclass
class SellResult:
    """卖出结果（FIFO 计算）"""
    total_quantity: float
    total_pnl: float
    avg_entry_price: float
    exit_price: float
    matched_lots: List[Dict] = field(default_factory=list)  # 匹配的批次明细


class TradingDatabase:
    """交易数据库 - 支持 FIFO 记账"""
    
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
        
        # 持仓批次表（FIFO 核心）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS position_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                original_quantity REAL NOT NULL,
                is_manual INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """)
        
        # 交易记录表（完整历史）
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
                lot_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                notes TEXT
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
                lot_id INTEGER,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        self.logger.info(f"数据库初始化完成: {self.db_path}")
    
    # ==================== FIFO 持仓批次操作 ====================
    
    def add_position_lot(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        is_manual: bool = False,
        notes: str = None
    ) -> int:
        """
        添加持仓批次（买入时调用）
        
        Args:
            symbol: 交易对
            entry_price: 买入价格
            quantity: 买入数量
            is_manual: 是否手动买入（初始持仓）
            notes: 备注
            
        Returns:
            批次 ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO position_lots (
                symbol, entry_price, quantity, original_quantity, is_manual, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (symbol, entry_price, quantity, quantity, 1 if is_manual else 0, notes))
        
        lot_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        self.logger.info(f"添加持仓批次: ID={lot_id}, {quantity}张 @ ${entry_price:.2f} {'(手动)' if is_manual else ''}")
        return lot_id
    
    def get_position_lots(self, symbol: str) -> List[Dict]:
        """
        获取所有未平仓的持仓批次（按时间排序，用于 FIFO）
        
        Args:
            symbol: 交易对
            
        Returns:
            持仓批次列表（按创建时间升序）
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM position_lots 
            WHERE symbol = ? AND quantity > 0
            ORDER BY created_at ASC
        """, (symbol,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_total_position(self, symbol: str) -> Tuple[float, float]:
        """
        获取总持仓数量和加权平均成本
        
        Returns:
            (总数量, 加权平均价格)
        """
        lots = self.get_position_lots(symbol)
        
        if not lots:
            return 0, 0
        
        total_qty = sum(lot['quantity'] for lot in lots)
        total_value = sum(lot['quantity'] * lot['entry_price'] for lot in lots)
        
        avg_price = total_value / total_qty if total_qty > 0 else 0
        
        return total_qty, avg_price
    
    def sell_fifo(
        self,
        symbol: str,
        exit_price: float,
        quantity: float
    ) -> SellResult:
        """
        FIFO 方式卖出（先进先出）
        
        Args:
            symbol: 交易对
            exit_price: 卖出价格
            quantity: 卖出数量
            
        Returns:
            SellResult 包含盈亏明细
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 获取持仓批次（按时间升序）
        cursor.execute("""
            SELECT * FROM position_lots 
            WHERE symbol = ? AND quantity > 0
            ORDER BY created_at ASC
        """, (symbol,))
        
        lots = cursor.fetchall()
        
        remaining_to_sell = quantity
        total_pnl = 0
        total_cost = 0
        matched_lots = []
        
        for lot in lots:
            if remaining_to_sell <= 0:
                break
            
            lot_id = lot['id']
            lot_qty = lot['quantity']
            lot_price = lot['entry_price']
            
            # 计算从这个批次卖出多少
            sell_from_lot = min(remaining_to_sell, lot_qty)
            
            # 计算这部分的盈亏
            pnl = (exit_price - lot_price) * sell_from_lot
            pnl_pct = ((exit_price - lot_price) / lot_price) * 100
            
            total_pnl += pnl
            total_cost += lot_price * sell_from_lot
            
            matched_lots.append({
                'lot_id': lot_id,
                'entry_price': lot_price,
                'quantity': sell_from_lot,
                'pnl': pnl,
                'pnl_pct': pnl_pct
            })
            
            # 更新批次剩余数量
            new_qty = lot_qty - sell_from_lot
            cursor.execute("""
                UPDATE position_lots SET quantity = ? WHERE id = ?
            """, (new_qty, lot_id))
            
            remaining_to_sell -= sell_from_lot
            
            self.logger.info(
                f"FIFO 匹配: 批次#{lot_id} 卖出 {sell_from_lot}张, "
                f"买入价 ${lot_price:.2f} -> 卖出价 ${exit_price:.2f}, "
                f"盈亏 ${pnl:.2f} ({pnl_pct:+.2f}%)"
            )
        
        conn.commit()
        conn.close()
        
        # 计算加权平均买入价
        actual_sold = quantity - remaining_to_sell
        avg_entry = total_cost / actual_sold if actual_sold > 0 else 0
        
        return SellResult(
            total_quantity=actual_sold,
            total_pnl=total_pnl,
            avg_entry_price=avg_entry,
            exit_price=exit_price,
            matched_lots=matched_lots
        )
    
    def sync_initial_position(
        self,
        symbol: str,
        okx_quantity: float,
        okx_avg_price: float
    ) -> bool:
        """
        同步初始持仓（启动时调用）
        
        如果数据库中没有持仓记录，但 OKX 有持仓，
        则将 OKX 持仓作为初始持仓添加到数据库。
        
        Args:
            symbol: 交易对
            okx_quantity: OKX 持仓数量
            okx_avg_price: OKX 持仓均价
            
        Returns:
            是否进行了同步
        """
        db_qty, db_avg = self.get_total_position(symbol)
        
        if db_qty > 0:
            # 数据库已有持仓记录
            self.logger.info(f"数据库已有持仓: {db_qty}张 @ ${db_avg:.2f}")
            return False
        
        if okx_quantity <= 0:
            # OKX 也没有持仓
            self.logger.info("OKX 无持仓，无需同步")
            return False
        
        # 将 OKX 持仓作为初始持仓添加
        self.add_position_lot(
            symbol=symbol,
            entry_price=okx_avg_price,
            quantity=okx_quantity,
            is_manual=True,
            notes=f"初始持仓同步: OKX 均价 ${okx_avg_price:.2f}"
        )
        
        self.logger.info(f"同步初始持仓: {okx_quantity}张 @ ${okx_avg_price:.2f}")
        return True
    
    def add_manual_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        notes: str = None
    ) -> int:
        """
        手动添加持仓（用于记录手动买入）
        
        Args:
            symbol: 交易对
            entry_price: 买入价格
            quantity: 买入数量
            notes: 备注
            
        Returns:
            批次 ID
        """
        return self.add_position_lot(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            is_manual=True,
            notes=notes or "手动添加持仓"
        )
    
    # ==================== 交易记录操作 ====================
    
    def record_buy(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        direction: str = "LONG",
        drop_type: str = None,
        drop_amount: float = None,
        notes: str = None,
        is_manual: bool = False
    ) -> Tuple[int, int]:
        """
        记录买入交易（同时添加持仓批次）
        
        Returns:
            (交易记录 ID, 持仓批次 ID)
        """
        # 添加持仓批次
        lot_id = self.add_position_lot(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            is_manual=is_manual,
            notes=notes
        )
        
        contract_value = entry_price * quantity
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trades (
                symbol, direction, side, entry_price, quantity, 
                contract_value, drop_type, drop_amount, status, lot_id, notes
            ) VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (symbol, direction, entry_price, quantity, contract_value, 
              drop_type, drop_amount, lot_id, notes))
        
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        self.logger.info(f"记录买入: 交易ID={trade_id}, 批次ID={lot_id}, {quantity}张 @ ${entry_price:.2f}")
        return trade_id, lot_id
    
    def record_sell_fifo(
        self,
        symbol: str,
        exit_price: float,
        quantity: float,
        direction: str = "LONG",
        is_reserve: bool = False,
        notes: str = None
    ) -> Tuple[int, SellResult]:
        """
        记录卖出交易（FIFO 方式）
        
        Returns:
            (交易记录 ID, SellResult)
        """
        # FIFO 卖出
        sell_result = self.sell_fifo(symbol, exit_price, quantity)
        
        if sell_result.total_quantity == 0:
            self.logger.warning("无持仓可卖出")
            return 0, sell_result
        
        contract_value = exit_price * sell_result.total_quantity
        pnl_pct = (sell_result.total_pnl / (sell_result.avg_entry_price * sell_result.total_quantity)) * 100
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 记录卖出交易
        cursor.execute("""
            INSERT INTO trades (
                symbol, direction, side, entry_price, exit_price, quantity,
                contract_value, pnl, pnl_pct, is_reserve, status, closed_at, notes
            ) VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, 'CLOSED', CURRENT_TIMESTAMP, ?)
        """, (symbol, direction, sell_result.avg_entry_price, exit_price, 
              sell_result.total_quantity, contract_value, sell_result.total_pnl, 
              pnl_pct, 1 if is_reserve else 0, notes))
        
        trade_id = cursor.lastrowid
        
        # 更新每日统计
        self._update_daily_stats(conn, sell_result.total_pnl, contract_value)
        
        conn.commit()
        conn.close()
        
        self.logger.info(
            f"记录卖出: ID={trade_id}, {sell_result.total_quantity}张 @ ${exit_price:.2f}, "
            f"FIFO 均价 ${sell_result.avg_entry_price:.2f}, 盈亏 ${sell_result.total_pnl:.2f}"
        )
        
        return trade_id, sell_result
    
    def _update_daily_stats(self, conn: sqlite3.Connection, pnl: float, volume: float):
        """更新每日统计"""
        today = datetime.now().strftime("%Y-%m-%d")
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM daily_stats WHERE date = ?", (today,))
        row = cursor.fetchone()
        
        if row:
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
            cursor.execute("""
                INSERT INTO daily_stats (date, total_trades, win_count, loss_count, total_pnl, total_volume)
                VALUES (?, 1, ?, ?, ?, ?)
            """, (today, 1 if pnl > 0 else 0, 1 if pnl <= 0 else 0, pnl, volume))
    
    # ==================== 保留仓位操作 ====================
    
    def add_reserved_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        target_price: float = None,
        lot_id: int = None
    ) -> int:
        """添加保留仓位"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reserved_positions (symbol, entry_price, quantity, target_price, lot_id)
            VALUES (?, ?, ?, ?, ?)
        """, (symbol, entry_price, quantity, target_price, lot_id))
        
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
    
    # ==================== 查询操作 ====================
    
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
    
    def get_statistics(self, symbol: str = None) -> Dict:
        """获取交易统计"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
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
        
        # 获取持仓信息
        db_qty, db_avg = self.get_total_position(symbol) if symbol else (0, 0)
        
        return {
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": row["loss_count"] or 0,
            "win_rate": (win_count / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl": row["total_pnl"] or 0,
            "total_volume": row["total_volume"] or 0,
            "reserved_quantity": self.get_total_reserved_quantity(symbol),
            "position_quantity": db_qty,
            "position_avg_price": db_avg
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
    
    def get_position_lots_summary(self, symbol: str) -> str:
        """获取持仓批次摘要（用于显示）"""
        lots = self.get_position_lots(symbol)
        
        if not lots:
            return "无持仓"
        
        lines = []
        for i, lot in enumerate(lots, 1):
            manual_tag = " (手动)" if lot['is_manual'] else ""
            lines.append(
                f"  #{i}: {lot['quantity']:.0f}张 @ ${lot['entry_price']:.2f}{manual_tag}"
            )
        
        total_qty, avg_price = self.get_total_position(symbol)
        lines.append(f"  合计: {total_qty:.0f}张, 均价 ${avg_price:.2f}")
        
        return "\n".join(lines)


if __name__ == "__main__":
    # 测试代码
    import os
    logging.basicConfig(level=logging.INFO)
    
    db = TradingDatabase("test_fifo.db")
    
    print("=" * 60)
    print("测试 FIFO 记账系统")
    print("=" * 60)
    
    # 模拟买入
    print("\n1. 模拟买入:")
    db.record_buy("SOL-USDT-SWAP", 120.0, 1, notes="第一笔买入")
    db.record_buy("SOL-USDT-SWAP", 110.0, 1, notes="第二笔买入")
    db.record_buy("SOL-USDT-SWAP", 100.0, 2, notes="第三笔买入")
    
    # 显示持仓批次
    print("\n2. 当前持仓批次:")
    print(db.get_position_lots_summary("SOL-USDT-SWAP"))
    
    # 模拟卖出 (FIFO)
    print("\n3. 卖出 2 张 @ $115 (FIFO):")
    trade_id, result = db.record_sell_fifo("SOL-USDT-SWAP", 115.0, 2)
    
    print(f"\n卖出结果:")
    print(f"  卖出数量: {result.total_quantity} 张")
    print(f"  FIFO 均价: ${result.avg_entry_price:.2f}")
    print(f"  卖出价格: ${result.exit_price:.2f}")
    print(f"  总盈亏: ${result.total_pnl:.2f}")
    print(f"\n  匹配明细:")
    for lot in result.matched_lots:
        print(f"    批次#{lot['lot_id']}: {lot['quantity']}张 @ ${lot['entry_price']:.2f} -> ${result.exit_price:.2f}, 盈亏 ${lot['pnl']:.2f} ({lot['pnl_pct']:+.2f}%)")
    
    # 显示剩余持仓
    print("\n4. 剩余持仓批次:")
    print(db.get_position_lots_summary("SOL-USDT-SWAP"))
    
    # 获取统计
    print("\n5. 交易统计:")
    stats = db.get_statistics("SOL-USDT-SWAP")
    print(f"  总交易次数: {stats['total_trades']}")
    print(f"  胜率: {stats['win_rate']:.1f}%")
    print(f"  累计盈亏: ${stats['total_pnl']:.2f}")
    print(f"  当前持仓: {stats['position_quantity']:.0f}张 @ ${stats['position_avg_price']:.2f}")
    
    # 测试初始持仓同步
    print("\n6. 测试初始持仓同步:")
    db2 = TradingDatabase("test_sync.db")
    synced = db2.sync_initial_position("SOL-USDT-SWAP", 5, 125.0)
    print(f"  同步结果: {'已同步' if synced else '无需同步'}")
    print(f"  持仓批次:\n{db2.get_position_lots_summary('SOL-USDT-SWAP')}")
    
    # 清理测试数据库
    os.remove("test_fifo.db")
    os.remove("test_sync.db")
    print("\n测试完成，已清理测试数据库")
