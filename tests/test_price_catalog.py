from __future__ import annotations

from tests.test_mortuary import create_case


BODY_CARE_V1 = {"service_code": "body-care", "service_name": "遗体护理", "unit": "次", "unit_price_cents": 80000, "conditions": {"duration_hours": 2, "includes": "清洁更衣"}, "active": True}
HALL_V1 = {"service_code": "farewell-hall", "service_name": "礼厅服务", "unit": "场", "unit_price_cents": 120000, "conditions": {"hall": "standard"}, "active": True}
BODY_CARE_V2 = {**BODY_CARE_V1, "unit_price_cents": 90000, "conditions": {"duration_hours": 3, "includes": "清洁更衣"}}
HALL_V2 = {**HALL_V1, "unit_price_cents": 110000}


def publish(client, catalog_id: int, effective_from: str, by: str = "finance-manager") -> dict:
    response = client.post(f"/api/mortuary/price-catalogs/{catalog_id}/publish", json={"effective_from": effective_from, "published_by": by})
    assert response.status_code == 200, response.text
    return response.json()


def make_catalog(client, label: str, items: list[dict], effective_from: str | None = None) -> dict:
    created = client.post("/api/mortuary/price-catalogs", json={"label": label, "notes": label, "created_by": "finance-manager", "items": items})
    assert created.status_code == 201, created.text
    catalog = created.json()
    if effective_from:
        catalog = publish(client, catalog["id"], effective_from)
    return catalog


def test_draft_editable_but_published_is_immutable(client):
    catalog = make_catalog(client, "PRICE-DRAFT-1", [BODY_CARE_V1])
    assert catalog["status"] == "draft"
    patched = client.patch(f"/api/mortuary/price-catalogs/{catalog['id']}", json={"items": [BODY_CARE_V1, HALL_V1]})
    assert patched.status_code == 200
    assert len(patched.json()["items"]) == 2
    publish(client, catalog["id"], "2026-01-01T00:00:00Z")
    republish = client.post(f"/api/mortuary/price-catalogs/{catalog['id']}/publish", json={"effective_from": "2026-02-01T00:00:00Z", "published_by": "finance-manager"})
    assert republish.status_code == 409
    overwrite = client.patch(f"/api/mortuary/price-catalogs/{catalog['id']}", json={"notes": "试图改写已发布版本"})
    assert overwrite.status_code == 409


def test_only_one_effective_version_and_future_version_inert(client):
    v1 = make_catalog(client, "PRICE-V1", [BODY_CARE_V1, HALL_V1], "2026-01-01T00:00:00Z")
    v2 = make_catalog(client, "PRICE-V2", [BODY_CARE_V2], "2026-07-01T00:00:00Z")
    # 生效时间不能早于或等于最近一版
    bad = make_catalog(client, "PRICE-BAD", [HALL_V1])
    conflict = client.post(f"/api/mortuary/price-catalogs/{bad['id']}/publish", json={"effective_from": "2026-06-30T00:00:00Z", "published_by": "finance-manager"})
    assert conflict.status_code == 409
    same_time = make_catalog(client, "PRICE-DUP", [HALL_V1])
    dup = client.post(f"/api/mortuary/price-catalogs/{same_time['id']}/publish", json={"effective_from": "2026-07-01T00:00:00Z", "published_by": "finance-manager"})
    assert dup.status_code == 409
    # 当前（2026-10）有效目录是 V2，其中没有礼厅项目
    current = client.get("/api/mortuary/price-catalogs/current").json()
    assert current["catalog"]["id"] == v2["id"]
    assert [item["service_code"] for item in current["items"]] == ["body-care"]
    # V1 的历史内容仍可完整查询
    history = client.get(f"/api/mortuary/price-catalogs/{v1['id']}").json()
    assert {item["service_code"] for item in history["items"]} == {"body-care", "farewell-hall"}


def test_order_freezes_catalog_price_and_survives_new_version(client):
    v1 = make_catalog(client, "PRICE-FRZ-1", [BODY_CARE_V1, HALL_V1], "2026-01-01T00:00:00Z")
    case = create_case(client, "CASE-PRICE-001")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 2, "requested_by": "family-service"})
    assert order.status_code == 201, order.text
    assert order.json()["unit_price_cents"] == 80000
    # 发布涨价新版本
    make_catalog(client, "PRICE-FRZ-2", [BODY_CARE_V2, HALL_V2], "2026-11-01T00:00:00Z")
    # 未来版本不影响此刻下单
    current = client.get("/api/mortuary/price-catalogs/current").json()
    assert current["catalog"]["id"] == v1["id"]
    confirmed = client.post(f"/api/mortuary/service-orders/{order.json()['id']}/confirm?actor=finance-reviewer")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["unit_price_cents"] == 80000
    assert body["amount_cents"] == 160000
    assert body["price_catalog_id"] == v1["id"]
    assert body["price_basis"]["service_name"] == "遗体护理"
    assert body["price_basis"]["conditions"]["duration_hours"] == 2
    assert body["price_basis"]["catalog_label"] == "PRICE-FRZ-1"
    assert body["frozen_at"]
    # 调价后历史订单金额与依据不变
    again = client.post(f"/api/mortuary/service-orders/{order.json()['id']}/confirm?actor=finance-reviewer")
    assert again.json()["unit_price_cents"] == 80000


def test_future_version_takes_effect_on_confirmation(client):
    make_catalog(client, "PRICE-FUT-1", [BODY_CARE_V1], "2026-01-01T00:00:00Z")
    make_catalog(client, "PRICE-FUT-2", [BODY_CARE_V2], "2099-01-01T00:00:00Z")
    case = create_case(client, "CASE-PRICE-002")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "requested_by": "family-service"}).json()
    # 以 2026 视角确认：冻结 V1
    confirmed = client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer").json()
    assert confirmed["unit_price_cents"] == 80000
    assert confirmed["price_basis"]["catalog_label"] == "PRICE-FUT-1"


def test_disabled_item_blocks_new_orders_but_history_intact(client):
    v1 = make_catalog(client, "PRICE-DIS-1", [BODY_CARE_V1, HALL_V1], "2026-01-01T00:00:00Z")
    case = create_case(client, "CASE-PRICE-003")
    old_order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "farewell-hall", "requested_by": "family-service"})
    assert old_order.status_code == 201
    old_confirmed = client.post(f"/api/mortuary/service-orders/{old_order.json()['id']}/confirm?actor=finance-reviewer").json()
    # 新版本停用礼厅项目（active=false）
    hall_disabled = {**HALL_V2, "active": False}
    make_catalog(client, "PRICE-DIS-2", [BODY_CARE_V2, hall_disabled], "2026-10-01T00:00:00Z")
    blocked = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "farewell-hall", "requested_by": "family-service"})
    assert blocked.status_code in {404, 409}
    # 历史订单及其冻结依据仍可查询
    history = client.get(f"/api/mortuary/service-orders/{old_confirmed['id']}").json()
    assert history["unit_price_cents"] == 120000
    assert history["price_catalog_id"] == v1["id"]
    case_detail = client.get(f"/api/mortuary/cases/{case['id']}").json()
    assert case_detail["service_orders"][0]["price_basis"]["catalog_label"] == "PRICE-DIS-1"


def test_refund_and_surcharge_are_adjustment_records(client):
    make_catalog(client, "PRICE-ADJ-1", [BODY_CARE_V1], "2026-01-01T00:00:00Z")
    case = create_case(client, "CASE-PRICE-004")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 1, "requested_by": "family-service"}).json()
    client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer")
    invoice = client.post("/api/mortuary/invoices", json={"case_id": case["id"], "order_ids": [order["id"]], "created_by": "cashier"}).json()
    assert invoice["amount_cents"] == 80000
    # 退款不动原订单金额
    refund = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json={"kind": "refund", "amount_cents": 20000, "reason": "服务时长缩短协议退款", "reference": "ADJ-REF-001", "created_by": "finance-manager"})
    assert refund.status_code == 201, refund.text
    dup = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json={"kind": "refund", "amount_cents": 1, "reason": "重复凭证", "reference": "ADJ-REF-001", "created_by": "finance-manager"})
    assert dup.status_code == 409
    surcharge = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json={"kind": "surcharge", "amount_cents": 5000, "reason": "临时增加专项护理耗材补差", "reference": "ADJ-SUR-001", "created_by": "finance-manager"})
    assert surcharge.status_code == 201
    detail = client.get(f"/api/mortuary/invoices/{invoice['id']}").json()
    assert detail["refund_cents"] == 20000
    assert detail["surcharge_cents"] == 5000
    assert detail["remaining_cents"] == 65000
    # 账单行可追溯到当时有效价格依据
    line = detail["items"][0]
    assert line["price_basis"]["catalog_label"] == "PRICE-ADJ-1"
    assert line["price_basis"]["unit_price_cents"] == 80000
    # 原订单金额永不被改写
    frozen_order = client.get(f"/api/mortuary/service-orders/{order['id']}").json()
    assert frozen_order["amount_cents"] == 80000
    assert {a["kind"] for a in frozen_order["adjustments"]} == {"refund", "surcharge"}


def test_diff_between_any_two_versions(client):
    v1 = make_catalog(client, "PRICE-DIFF-1", [BODY_CARE_V1, HALL_V1], "2026-01-01T00:00:00Z")
    extra = {"service_code": "wreath", "service_name": "花圈", "unit": "个", "unit_price_cents": 30000, "conditions": {}, "active": True}
    hall_off = {**HALL_V1, "active": False}
    v3 = make_catalog(client, "PRICE-DIFF-3", [BODY_CARE_V2, hall_off, extra], "2026-12-01T00:00:00Z")
    diff = client.post("/api/mortuary/price-catalogs/diff", json={"base_catalog_id": v1["id"], "target_catalog_id": v3["id"]})
    assert diff.status_code == 200, diff.text
    data = diff.json()
    assert [x["service_code"] for x in data["added"]] == ["wreath"]
    assert data["removed"] == []
    changed = {x["service_code"]: x for x in data["changed"]}
    assert changed["body-care"]["price_delta_cents"] == 10000
    assert changed["body-care"]["conditions_changed"] is True
    assert changed["farewell-hall"]["active_changed"] == {"before": True, "after": False}
    # 反向比较：花圈变为移除项
    reverse = client.post("/api/mortuary/price-catalogs/diff", json={"base_catalog_id": v3["id"], "target_catalog_id": v1["id"]}).json()
    assert [x["service_code"] for x in reverse["removed"]] == ["wreath"]
    assert reverse["added"] == []


def test_manual_price_orders_remain_supported_for_legacy_flow(client):
    case = create_case(client, "CASE-PRICE-005")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "custom-rite", "quantity": 1, "unit_price_cents": 50000, "requested_by": "family-service"}).json()
    confirmed = client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer").json()
    assert confirmed["pricing_mode"] == "manual"
    assert confirmed["price_basis"]["basis"] == "manual"
    assert confirmed["price_basis"]["catalog_id"] is None
