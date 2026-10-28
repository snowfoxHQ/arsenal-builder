#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arsenal 按量付费（AI 按量付费 / A2M）服务端。

只实现 AI 按量付费的三个适用接口：
1. HTTP 402 + `Payment-Needed` Header（无有效 Payment-Proof 时）
2. `alipay.aipay.agent.payment.verify`（Payment-Proof 验付）
3. `alipay.aipay.agent.fulfillment.confirm`（履约确认）

不混入统一收单、异步通知或 notify_url。

私钥：Python 属非 Java 语言，统一使用 `appPrivatePkcsKey`（PKCS#1）。
SDK 请求签名直接使用原始字段，不做 PEM 包装、Base64 重编码或换行处理。

启动：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config as config_module
from . import orders as orders_module
from . import signing

PROJECT_ROOT = config_module.PROJECT_ROOT

app = FastAPI(title="Arsenal Pay Service", version="1.0.0")

REPOSITORY: Optional[orders_module.OrderRepository] = orders_module.build_repository(PROJECT_ROOT)


# ==================== 工具 ====================


def format_alipay_timestamp(dt=None) -> str:
    return signing.format_alipay_timestamp(dt)


def init_alipay_client(config: Dict[str, Any]):
    """初始化支付宝 SDK 客户端（延迟导入，便于无 SDK 时做静态检查）。"""
    from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
    from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient

    client_config = AlipayClientConfig()
    client_config.server_url = config["gateway"]
    client_config.app_id = config["app_id"]
    client_config.app_private_key = config["app_private_key"]
    client_config.alipay_public_key = config["alipay_public_key"]
    client_config.charset = "utf-8"
    client_config.sign_type = "RSA2"
    return DefaultAlipayClient(alipay_client_config=client_config)


def unwrap_response(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    """兼容扁平结构与嵌套响应键两种情况。"""
    nested = payload.get(key)
    if isinstance(nested, dict):
        return nested
    return payload


def require_repository() -> orders_module.OrderRepository:
    if REPOSITORY is None:
        raise RuntimeError("尚未绑定订单仓储，禁止回退到内存订单")
    return REPOSITORY


# ==================== 402 账单 ====================


def create_payment_required_response():
    """创建 402 响应：先持久化订单，再返回账单 Header。"""
    cfg = config_module.load_config()
    repository = require_repository()

    resource_id = config_module.RESOURCE_PATH
    goods_name = config_module.GOODS_NAME
    amount = config_module.PRICE
    currency = "CNY"

    order = orders_module.new_order(resource_id, goods_name, amount)
    pay_before = order["pay_before"]

    seller_signature = signing.generate_seller_signature(
        {
            "amount": amount,
            "currency": currency,
            "goods_name": goods_name,
            "out_trade_no": order["out_trade_no"],
            "pay_before": pay_before,
            "resource_id": resource_id,
            "seller_id": cfg["seller_id"],
            "service_id": cfg["service_id"],
        },
        cfg["app_private_key"],
    )

    # 持久化成功后再构造 Header，避免出现无订单账单
    repository.create_pending(order)

    payment_needed = {
        "protocol": {
            "out_trade_no": order["out_trade_no"],
            "amount": amount,
            "currency": currency,
            "resource_id": resource_id,
            "pay_before": pay_before,
            "seller_signature": seller_signature,
            "seller_sign_type": "RSA2",
            "seller_unique_id": cfg["seller_id"],
        },
        "method": {
            "seller_name": cfg["seller_name"],
            "seller_id": cfg["seller_id"],
            "seller_app_id": cfg["app_id"],
            "goods_name": goods_name,
            "seller_unique_id_key": "seller_id",
            "service_id": cfg["service_id"],
        },
    }

    encoded = signing.base64url_encode(payment_needed)
    body = {
        "code": "Payment-Needed",
        "message": "需要支付",
        "out_trade_no": order["out_trade_no"],
        "amount": amount,
        "currency": currency,
        "goods_name": goods_name,
    }
    return JSONResponse(status_code=402, content=body, headers={"Payment-Needed": encoded})


# ==================== 验付与交付 ====================


def verify_payment_and_deliver_resource(payment_proof: str, resource_id: str):
    cfg = config_module.load_config()
    repository = require_repository()

    try:
        proof_json = json.loads(signing.base64url_decode(payment_proof))
        protocol = proof_json.get("protocol", {}) or {}
        method = proof_json.get("method", {}) or {}
        proof_value = (protocol.get("payment_proof") or "").strip()
        trade_no = (protocol.get("trade_no") or "").strip()
        client_session = (method.get("client_session") or "").strip()
    except Exception:
        return create_payment_required_response()

    if not proof_value or not trade_no:
        return create_payment_required_response()

    from alipay.aop.api.request.AlipayAipayAgentPaymentVerifyRequest import (
        AlipayAipayAgentPaymentVerifyRequest,
    )
    from alipay.aop.api.domain.AlipayAipayAgentPaymentVerifyModel import (
        AlipayAipayAgentPaymentVerifyModel,
    )

    model = AlipayAipayAgentPaymentVerifyModel()
    model.payment_proof = proof_value
    model.trade_no = trade_no
    if client_session:
        model.client_session = client_session

    request = AlipayAipayAgentPaymentVerifyRequest()
    request.biz_model = model

    client = init_alipay_client(cfg)
    response_payload = json.loads(client.execute(request))
    data = unwrap_response(response_payload, "alipay_aipay_agent_payment_verify_response")

    if data.get("code") != "10000":
        return create_payment_required_response()

    verify_trade_no = (data.get("trade_no") or "").strip()
    verify_out_trade_no = (data.get("out_trade_no") or "").strip()
    verify_amount = data.get("amount")
    verify_resource_id = (data.get("resource_id") or "").strip()
    active = data.get("active")

    order = repository.find_by_out_trade_no(verify_out_trade_no) if verify_out_trade_no else None

    sandbox_mode = cfg["env"] == "sandbox" and cfg["service_id"] == config_module.SANDBOX_SERVICE_ID
    if not verify_trade_no and sandbox_mode:
        verify_trade_no = trade_no
    if verify_amount in (None, "") and sandbox_mode and order:
        verify_amount = order.get("amount")
    if not verify_resource_id and sandbox_mode and order:
        verify_resource_id = order.get("resource_id")

    # 严格验付：SDK 调用成功不等于付款有效
    if active is not True:
        return create_payment_required_response()
    if not verify_trade_no or verify_trade_no != trade_no:
        return create_payment_required_response()
    if not verify_out_trade_no or not verify_resource_id:
        return create_payment_required_response()
    if verify_resource_id != resource_id:
        return create_payment_required_response()
    if not order or not signing.amounts_equal(order.get("amount"), verify_amount):
        return create_payment_required_response()
    if order.get("currency") != "CNY":
        return create_payment_required_response()

    fulfillment = repository.prepare_fulfillment(
        {
            "out_trade_no": verify_out_trade_no,
            "trade_no": verify_trade_no,
            "expected_amount": verify_amount,
            "expected_resource_id": verify_resource_id,
        },
        generate_service_resource,
    )

    if not fulfillment or fulfillment.get("state") not in ("PENDING_CONFIRM", "FULFILLED"):
        # 验付已通过，但本地订单不满足履约条件（已过 pay_before、状态不允许或归属不匹配）。
        # 此处绝不能返回 402 —— 那会让已付款的买家重复付款。返回 409 并带上订单号供人工核对。
        return JSONResponse(
            status_code=409,
            content={
                "code": "ORDER_NOT_FULFILLABLE",
                "message": "付款已核验，但本地订单不满足履约条件（可能已过期或状态不允许）。请勿重复付款，请联系商家并携带订单号核对。",
                "out_trade_no": verify_out_trade_no,
                "trade_no": verify_trade_no,
            },
        )
    if not fulfillment.get("service_result"):
        return JSONResponse(
            status_code=409,
            content={
                "code": "RESOURCE_EMPTY",
                "message": "资源生成为空，禁止交付空资源。请稍后使用同一 Payment-Proof 重试。",
                "out_trade_no": verify_out_trade_no,
                "trade_no": verify_trade_no,
            },
        )

    service_result = fulfillment["service_result"]

    if fulfillment["state"] == "FULFILLED":
        return successful_resource_response(
            verify_trade_no, verify_out_trade_no, verify_resource_id, service_result, True
        )

    if not send_fulfillment_confirm(cfg, verify_trade_no):
        return JSONResponse(
            status_code=502,
            content={
                "code": "FULFILLMENT_CONFIRM_FAILED",
                "message": "资源已生成但履约确认失败，请稍后使用同一 Payment-Proof 重试",
            },
        )

    repository.mark_fulfilled(verify_out_trade_no, verify_trade_no)

    return successful_resource_response(
        verify_trade_no, verify_out_trade_no, verify_resource_id, service_result, False
    )


def successful_resource_response(
    trade_no: str,
    out_trade_no: str,
    resource_id: str,
    service_result: str,
    already_fulfilled: bool,
):
    payment_validation = signing.base64url_encode(
        json.dumps(
            {
                "trade_no": trade_no,
                "out_trade_no": out_trade_no,
                "validated": True,
                "resource_id": resource_id,
            },
            ensure_ascii=False,
        )
    )
    return JSONResponse(
        status_code=200,
        content={
            "resource_id": resource_id,
            "content": service_result,
            "trade_no": trade_no,
            "out_trade_no": out_trade_no,
            "already_fulfilled": already_fulfilled,
            "fulfillment_confirmed": True,
        },
        headers={"Payment-Validation": payment_validation},
    )


# ==================== 业务资源 ====================


def generate_service_resource(resource_id: str) -> str:
    """生成 Arsenal 执行授权资源。

    资源内容按实际业务交付；这里返回一次执行授权，
    由 arsenal-builder 的本地门禁消费。
    """
    return json.dumps(
        {
            "status": "success",
            "service_type": "ARSENAL_SANDBOX_RUN",
            "resource_id": resource_id,
            "ticket": f"ARSENAL-TICKET-{uuid.uuid4().hex}",
            "issued_at": datetime.now(timezone.utc).astimezone().isoformat(),
        },
        ensure_ascii=False,
    )


def send_fulfillment_confirm(cfg: Dict[str, Any], trade_no: str) -> bool:
    """发送履约确认；只在确认成功后才允许标记订单完成。"""
    if not trade_no:
        return False

    from alipay.aop.api.request.AlipayAipayAgentFulfillmentConfirmRequest import (
        AlipayAipayAgentFulfillmentConfirmRequest,
    )
    from alipay.aop.api.domain.AlipayAipayAgentFulfillmentConfirmModel import (
        AlipayAipayAgentFulfillmentConfirmModel,
    )

    model = AlipayAipayAgentFulfillmentConfirmModel()
    model.trade_no = trade_no

    request = AlipayAipayAgentFulfillmentConfirmRequest()
    request.biz_model = model

    client = init_alipay_client(cfg)
    response_payload = json.loads(client.execute(request))
    data = unwrap_response(response_payload, "alipay_aipay_agent_fulfillment_confirm_response")
    return data.get("code") == "10000"


# ==================== 路由 ====================


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.api_route(config_module.RESOURCE_PATH, methods=["GET", "POST"])
async def arsenal_run(request: Request):
    """整体收费门禁：付费成功后才交付 Arsenal 执行授权。"""
    payment_proof = request.headers.get("Payment-Proof")
    if not payment_proof or not payment_proof.strip():
        return create_payment_required_response()
    return verify_payment_and_deliver_resource(payment_proof, config_module.RESOURCE_PATH)
