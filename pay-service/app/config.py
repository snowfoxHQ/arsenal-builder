"""支付配置加载。

约定：
- 沙箱：运行时直接读取项目根的 `.alipay-sandbox.json`（由 Skill 生成并校验）。
- 生产：从环境变量注入，不读取任何仓库内文件。

私钥格式：Python 属非 Java 语言，必须使用 `appPrivatePkcsKey`（PKCS#1），
禁止使用 `appPrivateKey`，也禁止任何 PEM 头尾加工或格式转换。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SANDBOX_CONFIG_PATH = PROJECT_ROOT / ".alipay-sandbox.json"

DEFAULT_SANDBOX_GATEWAY = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"
PRODUCTION_GATEWAY = "https://openapi.alipay.com/gateway.do"

SANDBOX_SERVICE_ID = "api_mock_service_id"

RESOURCE_PATH = "/arsenal/run"
GOODS_NAME = "AI 兵工厂沙箱测试执行"
PRICE = "0.01"


class PaymentConfigError(RuntimeError):
    """配置缺失或非法时抛出，调用方必须直接失败而不是回退示例值。"""


def _normalize_key(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise PaymentConfigError(f"缺少或非法字段：{field}")
    return "".join(raw.split())


def _is_sandbox() -> bool:
    return os.environ.get("ALIPAY_ENV", "sandbox").strip().lower() != "production"


def _load_sandbox() -> Dict[str, Any]:
    path = Path(os.environ.get("ALIPAY_SANDBOX_CONFIG", DEFAULT_SANDBOX_CONFIG_PATH)).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PaymentConfigError(f"未找到沙箱配置：{path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PaymentConfigError(f"沙箱配置不是合法 JSON：{path}") from exc

    apps = payload.get("appIds")
    if not isinstance(apps, list) or not apps:
        raise PaymentConfigError(f"沙箱配置缺少 appIds：{path}")
    app = apps[0]
    if not isinstance(app, dict):
        raise PaymentConfigError(f"沙箱配置 appIds[0] 非法：{path}")

    accounts = payload.get("sandboxAccounts") or {}
    partner = accounts.get("partner") or {}
    seller_id = app.get("pid") or partner.get("userId")
    if not seller_id:
        raise PaymentConfigError(f"沙箱配置缺少卖家 pid/userId：{path}")

    return {
        "env": "sandbox",
        "app_id": app.get("appId"),
        # Python 必须使用 PKCS#1 字段
        "app_private_key": _normalize_key(app.get("appPrivatePkcsKey"), "appPrivatePkcsKey"),
        "alipay_public_key": _normalize_key(app.get("alipayPublicKey"), "alipayPublicKey"),
        "gateway": os.environ.get("ALIPAY_GATEWAY", DEFAULT_SANDBOX_GATEWAY),
        "seller_id": seller_id,
        "service_id": SANDBOX_SERVICE_ID,
        "seller_name": "Arsenal 测试商户",
        "config_path": str(path),
    }


def _load_production() -> Dict[str, Any]:
    mapping = {
        "app_id": "ALIPAY_APP_ID",
        "app_private_key": "ALIPAY_APP_PRIVATE_KEY",
        "alipay_public_key": "ALIPAY_PUBLIC_KEY",
        "seller_id": "ALIPAY_SELLER_ID",
        "service_id": "ALIPAY_SERVICE_ID",
    }
    values = {key: os.environ.get(name, "").strip() for key, name in mapping.items()}
    missing = [name for key, name in mapping.items() if not values[key]]
    if missing:
        raise PaymentConfigError("生产环境缺少环境变量：" + "、".join(missing))
    values["app_private_key"] = _normalize_key(values["app_private_key"], "ALIPAY_APP_PRIVATE_KEY")
    values["alipay_public_key"] = _normalize_key(values["alipay_public_key"], "ALIPAY_PUBLIC_KEY")
    values["env"] = "production"
    values["gateway"] = os.environ.get("ALIPAY_GATEWAY", PRODUCTION_GATEWAY)
    values["seller_name"] = os.environ.get("ALIPAY_SELLER_NAME", "Arsenal").strip()
    values["config_path"] = ""
    return values


def load_config() -> Dict[str, Any]:
    """返回运行时支付配置；失败即抛错，禁止占位值兜底。"""
    config = _load_sandbox() if _is_sandbox() else _load_production()
    for field in ("app_id", "seller_id", "service_id"):
        if not config.get(field):
            raise PaymentConfigError(f"配置缺少字段：{field}")
    return config
