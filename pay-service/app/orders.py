"""订单持久化与幂等履约。

验证阶段使用 SQLite；生产部署必须替换为具备事务与唯一约束的数据库实现，
但接口契约（create_pending / find_by_out_trade_no /
prepare_fulfillment / mark_fulfilled）保持不变。

幂等保证：
- `out_trade_no` 唯一约束 → 同一订单不会重复建单。
- `prepare_fulfillment` 在同一事务内校验归属与状态，
  资源只生成一次；已生成时直接返回已持久化的结果。
- `trade_no` 唯一约束 → 同一笔支付宝交易不会被重复履约。
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Callable, Dict, Optional

from . import signing

DEFAULT_DB_PATH = os.environ.get("ARSENAL_ORDERS_DB", "")

PAY_OPEN = ("PENDING_PAYMENT", "PAID")
FULFILL_OPEN = ("UNFULFILLED", "PENDING_CONFIRM", "FULFILLED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    out_trade_no TEXT PRIMARY KEY,
    amount       TEXT NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'CNY',
    resource_id  TEXT NOT NULL,
    goods_name   TEXT NOT NULL,
    pay_before   TEXT NOT NULL,
    order_status TEXT NOT NULL,
    fulfill_status TEXT NOT NULL,
    trade_no     TEXT,
    service_result TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_trade_no
    ON orders(trade_no) WHERE trade_no IS NOT NULL AND trade_no <> '';
"""


class OrderRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def create_pending(self, order: Dict[str, Any]) -> None:
        now = signing.format_alipay_timestamp()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO orders (
                       out_trade_no, amount, currency, resource_id, goods_name,
                       pay_before, order_status, fulfill_status, trade_no,
                       service_result, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order["out_trade_no"],
                    signing.normalize_amount(order["amount"]),
                    order.get("currency", "CNY"),
                    order["resource_id"],
                    order["goods_name"],
                    order["pay_before"],
                    order.get("order_status", "PENDING_PAYMENT"),
                    order.get("fulfill_status", "UNFULFILLED"),
                    order.get("trade_no") or None,
                    order.get("service_result"),
                    now,
                    now,
                ),
            )

    def find_by_out_trade_no(self, out_trade_no: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE out_trade_no = ?", (out_trade_no,)
            ).fetchone()
        return dict(row) if row else None

    def _mark_paid(self, connection: sqlite3.Connection, out_trade_no: str) -> None:
        connection.execute(
            "UPDATE orders SET order_status = 'PAID', updated_at = ? WHERE out_trade_no = ?",
            (signing.format_alipay_timestamp(), out_trade_no),
        )

    def prepare_fulfillment(
        self,
        payload: Dict[str, Any],
        create_resource: Callable[[str], str],
    ) -> Optional[Dict[str, Any]]:
        """原子地校验订单并只生成一次资源。

        返回 `FULFILLED | PENDING_CONFIRM` 且带 `service_result`，
        或 `None`（订单不存在 / 不匹配 / 状态不允许）。
        """
        out_trade_no = payload["out_trade_no"]
        trade_no = payload["trade_no"]
        expected_amount = signing.normalize_amount(payload["expected_amount"])
        expected_resource_id = payload["expected_resource_id"]

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM orders WHERE out_trade_no = ?", (out_trade_no,)
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                return None
            order = dict(row)

            if order["resource_id"] != expected_resource_id:
                connection.execute("ROLLBACK")
                return None
            if not signing.amounts_equal(order["amount"], expected_amount):
                connection.execute("ROLLBACK")
                return None
            if order["order_status"] not in PAY_OPEN:
                connection.execute("ROLLBACK")
                return None
            if order["fulfill_status"] not in FULFILL_OPEN:
                connection.execute("ROLLBACK")
                return None

            # 已经生成过资源：只允许重试确认或返回已保存结果
            if order["fulfill_status"] in ("PENDING_CONFIRM", "FULFILLED"):
                if order["trade_no"] and order["trade_no"] != trade_no:
                    connection.execute("ROLLBACK")
                    return None
                connection.execute("COMMIT")
                return {
                    "state": order["fulfill_status"],
                    "service_result": order["service_result"] or "",
                }

            # 首次进入履约：仍需在 pay_before 内
            if not signing.is_future(order["pay_before"]):
                connection.execute("ROLLBACK")
                return None

            service_result = create_resource(expected_resource_id)
            self._mark_paid(connection, out_trade_no)
            connection.execute(
                """UPDATE orders
                       SET fulfill_status = 'PENDING_CONFIRM',
                           trade_no = ?,
                           service_result = ?,
                           updated_at = ?
                     WHERE out_trade_no = ?""",
                (
                    trade_no,
                    service_result,
                    signing.format_alipay_timestamp(),
                    out_trade_no,
                ),
            )
            connection.execute("COMMIT")
            return {"state": "PENDING_CONFIRM", "service_result": service_result}
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def mark_fulfilled(self, out_trade_no: str, trade_no: str) -> None:
        """履约确认成功后再把订单标记为最终完成。"""
        with self._connect() as connection:
            connection.execute(
                """UPDATE orders
                       SET fulfill_status = 'FULFILLED',
                           trade_no = ?,
                           updated_at = ?
                     WHERE out_trade_no = ?""",
                (trade_no, signing.format_alipay_timestamp(), out_trade_no),
            )


def resolve_db_path(project_root) -> str:
    if DEFAULT_DB_PATH:
        return DEFAULT_DB_PATH
    return str(project_root / "var" / "orders.db")


def build_repository(project_root) -> OrderRepository:
    return OrderRepository(resolve_db_path(project_root))


def new_order(resource_id: str, goods_name: str, amount: str) -> Dict[str, Any]:
    return {
        "out_trade_no": signing.generate_out_trade_no(),
        "amount": amount,
        "currency": "CNY",
        "resource_id": resource_id,
        "goods_name": goods_name,
        "pay_before": signing.build_pay_before(),
        "order_status": "PENDING_PAYMENT",
        "fulfill_status": "UNFULFILLED",
    }
