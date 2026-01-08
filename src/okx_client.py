"""
OKX API 客户端模块
支持正式网和测试网（模拟盘）
"""
import hmac
import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import requests
from dataclasses import dataclass

from config import OKXConfig


class OKXClient:
    """OKX API 客户端"""
    
    def __init__(self, config: OKXConfig):
        self.config = config
        self.base_url = config.base_url
        self.session = requests.Session()
        
    def _get_timestamp(self) -> str:
        """获取 ISO 格式时间戳"""
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    
    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """生成签名"""
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            self.config.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode('utf-8')
    
    def _get_headers(self, method: str, request_path: str, body: str = "") -> Dict[str, str]:
        """获取请求头"""
        timestamp = self._get_timestamp()
        sign = self._sign(timestamp, method, request_path, body)
        
        headers = {
            'OK-ACCESS-KEY': self.config.api_key,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.config.passphrase,
            'Content-Type': 'application/json',
            'x-simulated-trading': self.config.simulated_trading  # 模拟盘标志
        }
        return headers
    
    def _request(self, method: str, endpoint: str, params: Dict = None, data: Dict = None) -> Dict:
        """发送请求"""
        url = self.base_url + endpoint
        body = ""
        
        if method.upper() == "GET" and params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            endpoint = f"{endpoint}?{query_string}"
            url = self.base_url + endpoint
        elif data:
            body = json.dumps(data)
        
        headers = self._get_headers(method, endpoint, body)
        
        try:
            if method.upper() == "GET":
                response = self.session.get(url, headers=headers, timeout=30)
            elif method.upper() == "POST":
                response = self.session.post(url, headers=headers, data=body, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"code": "-1", "msg": str(e), "data": []}
    
    # ==================== 公共接口 ====================
    
    def get_ticker(self, inst_id: str = "SOL-USDT-SWAP") -> Dict:
        """获取行情数据"""
        endpoint = "/api/v5/market/ticker"
        params = {"instId": inst_id}
        return self._request("GET", endpoint, params=params)
    
    def get_mark_price(self, inst_id: str = "SOL-USDT-SWAP") -> Dict:
        """获取标记价格"""
        endpoint = "/api/v5/public/mark-price"
        params = {"instId": inst_id}
        return self._request("GET", endpoint, params=params)
    
    def get_instruments(self, inst_type: str = "SWAP", inst_id: str = None) -> Dict:
        """获取交易产品信息"""
        endpoint = "/api/v5/public/instruments"
        params = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", endpoint, params=params)
    
    # ==================== 账户接口 ====================
    
    def get_account_balance(self, ccy: str = None) -> Dict:
        """获取账户余额"""
        endpoint = "/api/v5/account/balance"
        params = {}
        if ccy:
            params["ccy"] = ccy
        return self._request("GET", endpoint, params=params if params else None)
    
    def get_positions(self, inst_type: str = "SWAP", inst_id: str = None) -> Dict:
        """获取持仓信息"""
        endpoint = "/api/v5/account/positions"
        params = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", endpoint, params=params)
    
    def set_leverage(self, inst_id: str, lever: int, mgn_mode: str = "cross", pos_side: str = None) -> Dict:
        """设置杠杆倍数
        
        Args:
            inst_id: 产品ID
            lever: 杠杆倍数
            mgn_mode: 保证金模式 cross-全仓 isolated-逐仓
            pos_side: 持仓方向 long-多仓 short-空仓 (仅逐仓需要)
        """
        endpoint = "/api/v5/account/set-leverage"
        data = {
            "instId": inst_id,
            "lever": str(lever),
            "mgnMode": mgn_mode
        }
        if pos_side and mgn_mode == "isolated":
            data["posSide"] = pos_side
        return self._request("POST", endpoint, data=data)
    
    def set_position_mode(self, pos_mode: str = "long_short_mode") -> Dict:
        """设置持仓模式
        
        Args:
            pos_mode: long_short_mode-双向持仓 net_mode-单向持仓
        """
        endpoint = "/api/v5/account/set-position-mode"
        data = {"posMode": pos_mode}
        return self._request("POST", endpoint, data=data)
    
    def get_account_config(self) -> Dict:
        """获取账户配置"""
        endpoint = "/api/v5/account/config"
        return self._request("GET", endpoint)
    
    # ==================== 交易接口 ====================
    
    def place_order(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        order_type: str,
        sz: str,
        pos_side: str = None,
        px: str = None,
        reduce_only: bool = False,
        tp_trigger_px: str = None,
        tp_ord_px: str = None,
        sl_trigger_px: str = None,
        sl_ord_px: str = None
    ) -> Dict:
        """下单
        
        Args:
            inst_id: 产品ID
            td_mode: 交易模式 cross-全仓 isolated-逐仓 cash-现金
            side: 订单方向 buy-买入 sell-卖出
            order_type: 订单类型 market-市价 limit-限价
            sz: 委托数量
            pos_side: 持仓方向 long-多仓 short-空仓 (双向持仓模式必填)
            px: 委托价格 (限价单必填)
            reduce_only: 是否只减仓
            tp_trigger_px: 止盈触发价
            tp_ord_px: 止盈委托价 (-1 为市价)
            sl_trigger_px: 止损触发价
            sl_ord_px: 止损委托价 (-1 为市价)
        """
        endpoint = "/api/v5/trade/order"
        data = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": order_type,
            "sz": sz
        }
        
        if pos_side:
            data["posSide"] = pos_side
        if px and order_type == "limit":
            data["px"] = px
        if reduce_only:
            data["reduceOnly"] = "true"
        
        # 止盈止损
        if tp_trigger_px:
            data["tpTriggerPx"] = tp_trigger_px
            data["tpOrdPx"] = tp_ord_px or "-1"
        if sl_trigger_px:
            data["slTriggerPx"] = sl_trigger_px
            data["slOrdPx"] = sl_ord_px or "-1"
        
        return self._request("POST", endpoint, data=data)
    
    def close_position(self, inst_id: str, mgn_mode: str = "cross", pos_side: str = None) -> Dict:
        """市价全平
        
        Args:
            inst_id: 产品ID
            mgn_mode: 保证金模式
            pos_side: 持仓方向 (双向持仓模式必填)
        """
        endpoint = "/api/v5/trade/close-position"
        data = {
            "instId": inst_id,
            "mgnMode": mgn_mode
        }
        if pos_side:
            data["posSide"] = pos_side
        return self._request("POST", endpoint, data=data)
    
    def cancel_order(self, inst_id: str, ord_id: str = None, cl_ord_id: str = None) -> Dict:
        """撤单"""
        endpoint = "/api/v5/trade/cancel-order"
        data = {"instId": inst_id}
        if ord_id:
            data["ordId"] = ord_id
        if cl_ord_id:
            data["clOrdId"] = cl_ord_id
        return self._request("POST", endpoint, data=data)
    
    def get_order(self, inst_id: str, ord_id: str = None, cl_ord_id: str = None) -> Dict:
        """获取订单信息"""
        endpoint = "/api/v5/trade/order"
        params = {"instId": inst_id}
        if ord_id:
            params["ordId"] = ord_id
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        return self._request("GET", endpoint, params=params)
    
    def get_orders_pending(self, inst_type: str = "SWAP", inst_id: str = None) -> Dict:
        """获取未成交订单列表"""
        endpoint = "/api/v5/trade/orders-pending"
        params = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", endpoint, params=params)
    
    def get_orders_history(self, inst_type: str = "SWAP", inst_id: str = None, limit: int = 100) -> Dict:
        """获取历史订单"""
        endpoint = "/api/v5/trade/orders-history-archive"
        params = {
            "instType": inst_type,
            "limit": str(limit)
        }
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", endpoint, params=params)
    
    # ==================== 策略委托接口 ====================
    
    def place_algo_order(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        order_type: str,
        sz: str,
        pos_side: str = None,
        tp_trigger_px: str = None,
        tp_ord_px: str = None,
        sl_trigger_px: str = None,
        sl_ord_px: str = None
    ) -> Dict:
        """策略委托下单（止盈止损）
        
        Args:
            inst_id: 产品ID
            td_mode: 交易模式
            side: 订单方向
            order_type: 订单类型 conditional-条件单
            sz: 委托数量
            pos_side: 持仓方向
            tp_trigger_px: 止盈触发价
            tp_ord_px: 止盈委托价
            sl_trigger_px: 止损触发价
            sl_ord_px: 止损委托价
        """
        endpoint = "/api/v5/trade/order-algo"
        data = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": order_type,
            "sz": sz
        }
        
        if pos_side:
            data["posSide"] = pos_side
        if tp_trigger_px:
            data["tpTriggerPx"] = tp_trigger_px
            data["tpOrdPx"] = tp_ord_px or "-1"
        if sl_trigger_px:
            data["slTriggerPx"] = sl_trigger_px
            data["slOrdPx"] = sl_ord_px or "-1"
        
        return self._request("POST", endpoint, data=data)
    
    def cancel_algo_order(self, algo_id: str, inst_id: str) -> Dict:
        """撤销策略委托"""
        endpoint = "/api/v5/trade/cancel-algos"
        data = [{"algoId": algo_id, "instId": inst_id}]
        return self._request("POST", endpoint, data=data)
    
    def get_algo_orders_pending(self, inst_type: str = "SWAP", order_type: str = "conditional") -> Dict:
        """获取未完成策略委托"""
        endpoint = "/api/v5/trade/orders-algo-pending"
        params = {
            "instType": inst_type,
            "ordType": order_type
        }
        return self._request("GET", endpoint, params=params)


@dataclass
class TickerInfo:
    """行情信息"""
    inst_id: str
    last_price: float
    bid_price: float
    ask_price: float
    high_24h: float
    low_24h: float
    vol_24h: float
    timestamp: int
    
    @classmethod
    def from_response(cls, data: Dict) -> Optional["TickerInfo"]:
        """从 API 响应解析"""
        if not data or not data.get("data"):
            return None
        ticker = data["data"][0]
        return cls(
            inst_id=ticker.get("instId", ""),
            last_price=float(ticker.get("last", 0)),
            bid_price=float(ticker.get("bidPx", 0)),
            ask_price=float(ticker.get("askPx", 0)),
            high_24h=float(ticker.get("high24h", 0)),
            low_24h=float(ticker.get("low24h", 0)),
            vol_24h=float(ticker.get("vol24h", 0)),
            timestamp=int(ticker.get("ts", 0))
        )


@dataclass
class PositionInfo:
    """持仓信息"""
    inst_id: str
    pos_side: str  # long, short, net
    pos: float  # 持仓数量
    avg_px: float  # 开仓均价
    upl: float  # 未实现盈亏
    upl_ratio: float  # 未实现盈亏率
    lever: int  # 杠杆倍数
    margin: float  # 保证金
    
    @classmethod
    def from_response(cls, data: Dict) -> List["PositionInfo"]:
        """从 API 响应解析"""
        positions = []
        if not data or not data.get("data"):
            return positions
        
        for pos in data["data"]:
            try:
                # 安全解析数值，处理空字符串情况
                pos_val = pos.get("pos", "0") or "0"
                avg_px_val = pos.get("avgPx", "0") or "0"
                upl_val = pos.get("upl", "0") or "0"
                upl_ratio_val = pos.get("uplRatio", "0") or "0"
                lever_val = pos.get("lever", "1") or "1"
                margin_val = pos.get("margin", "0") or "0"
                
                pos_float = float(pos_val)
                if pos_float != 0:
                    positions.append(cls(
                        inst_id=pos.get("instId", ""),
                        pos_side=pos.get("posSide", "net"),
                        pos=pos_float,
                        avg_px=float(avg_px_val),
                        upl=float(upl_val),
                        upl_ratio=float(upl_ratio_val),
                        lever=int(float(lever_val)),
                        margin=float(margin_val)
                    ))
            except (ValueError, TypeError) as e:
                # 跳过无法解析的持仓数据
                continue
        return positions


if __name__ == "__main__":
    # 测试代码
    from config import OKXConfig
    
    config = OKXConfig(use_testnet=True)
    client = OKXClient(config)
    
    # 测试获取行情（公共接口，不需要 API Key）
    ticker = client.get_ticker("SOL-USDT-SWAP")
    print("Ticker:", json.dumps(ticker, indent=2))
    
    ticker_info = TickerInfo.from_response(ticker)
    if ticker_info:
        print(f"SOL 当前价格: {ticker_info.last_price}")
