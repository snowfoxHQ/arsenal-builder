"""商家账单签名与通用工具。

商家签名在本地完成，不请求支付宝服务端。
签名内容按 key 字典序拼接：`k=v` 以 `&` 连接。
时间戳必须是 `yyyy-MM-dd HH:mm:ss`，禁止 ISO 格式。
"""

from __future__ import annotations

import base64
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

# 参与商家签名的字段，顺序即字典序
SIGN_FIELDS = (
    "amount",
    "currency",
    "goods_name",
    "out_trade_no",
    "pay_before",
    "resource_id",
    "seller_id",
    "service_id",
)

AMOUNT_PATTERN = re.compile(r"\d+(?:\.\d{1,2})?")


def format_alipay_timestamp(dt: Optional[datetime] = None) -> str:
    """格式化支付宝时间戳：yyyy-MM-dd HH:mm:ss。"""
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def generate_out_trade_no() -> str:
    return f"ARSENAL_{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"


def build_pay_before(minutes: int = 30) -> str:
    """带时区的 ISO 8601 截止时间。"""
    return (datetime.now(timezone.utc).astimezone() + timedelta(minutes=minutes)).isoformat()


def normalize_amount(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not AMOUNT_PATTERN.fullmatch(text):
        return None
    return f"{Decimal(text):.2f}"


def amounts_equal(left: Any, right: Any) -> bool:
    left_amount = normalize_amount(left)
    right_amount = normalize_amount(right)
    return left_amount is not None and right_amount is not None and left_amount == right_amount


def is_future(value: Any) -> bool:
    try:
        return datetime.fromisoformat(str(value)).timestamp() > time.time()
    except (TypeError, ValueError):
        return False


def generate_seller_signature(params: Dict[str, Any], private_key: str) -> str:
    """生成商家签名 seller_signature（RSA2 / SHA256 / PKCS#1 v1.5）。

    `private_key` 必须是配置中的原始 PKCS#1 裸 Base64 字符串。
    这里只在调用边界临时解码为 DER 密钥对象，不写回配置、不修改原始值。
    """
    sign_content = []
    for key in sorted(params.keys()):
        value = params[key]
        if value is not None and value != "":
            sign_content.append(f"{key}={value}")
    sign_string = "&".join(sign_content)

    key_der = base64.b64decode(private_key, validate=True)
    key = RSA.import_key(key_der)
    digest = SHA256.new(sign_string.encode("utf-8"))
    signature = pkcs1_15.new(key).sign(digest)
    return base64.b64encode(signature).decode("utf-8")


def base64url_encode(data: Any) -> str:
    if isinstance(data, (dict, list)):
        import json

        data = json.dumps(data, ensure_ascii=False)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def base64url_decode(data: str) -> str:
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data).decode("utf-8")
