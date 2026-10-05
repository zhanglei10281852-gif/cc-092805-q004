from __future__ import annotations


def make_case(client, ref):
    response = client.post("/api/mortuary/cases?actor=intake-clerk", json={"external_ref": ref, "decedent_name": "王秀兰", "identity_number": "ID-440200-1942", "death_time": "2026-09-27T08:30:00Z", "received_from": "市第一医院", "family_contact": "王强", "family_phone": "13900000000", "special_notes": ""})
    assert response.status_code == 201, response.text
    return response.json()


BODY_CARE = {"service_code": "body-care", "service_name": "遗体护理", "unit": "次", "unit_price_cents": 80000, "applicability": {}}
HALL = {"service_code": "farewell-hall", "service_name": "送别厅服务", "unit": "场", "unit_price_cents": 120000, "applicability": {}}


def create_catalog(client, items, *, effective_on="2020-01-01", name="价格目录", created_by="finance-manager"):
    response = client.post("/api/mortuary/price-catalogs", json={"name": name, "effective_on": effective_on, "notes": "草稿备注", "created_by": created_by, "items": items})
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, catalog, *, published_by="finance-manager", effective_on=None):
    payload = {"published_by": published_by}
    if effective_on is not None:
        payload["effective_on"] = effective_on
    response = client.post(f"/api/mortuary/price-catalogs/{catalog['id']}/publish", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_no_effective_catalog_blocks_ordering(client):
    case = make_case(client, "CASE-PRICE-000")
    response = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 1, "requested_by": "family-service"})
    assert response.status_code == 409
    assert client.get("/api/mortuary/price-catalogs/effective").status_code == 404


def test_draft_editable_but_published_is_immutable(client):
    catalog = create_catalog(client, [BODY_CARE, HALL])
    assert catalog["status"] == "draft" and catalog["version_no"] == 1
    patched = client.patch(f"/api/mortuary/price-catalogs/{catalog['id']}?actor=finance-manager", json={"name": "修订目录", "items": [{**BODY_CARE, "unit_price_cents": 82000}, HALL]})
    assert patched.status_code == 200
    assert patched.json()["name"] == "修订目录" and patched.json()["items"][0]["unit_price_cents"] == 82000
    hall_item = next(item for item in patched.json()["items"] if item["service_code"] == "farewell-hall")

    discontinued = client.post(f"/api/mortuary/price-catalogs/{catalog['id']}/items/{hall_item['id']}/discontinue?actor=finance-manager")
    assert discontinued.status_code == 200
    assert next(item for item in discontinued.json()["items"] if item["id"] == hall_item["id"])["status"] == "discontinued"
    # 仍有启用项目，可以发布
    published = publish(client, catalog)
    assert published["status"] == "published"
    again = client.post(f"/api/mortuary/price-catalogs/{catalog['id']}/publish", json={"published_by": "finance-manager"})
    assert again.status_code == 200 and again.json()["status"] == "published"

    conflict = client.patch(f"/api/mortuary/price-catalogs/{catalog['id']}?actor=finance-manager", json={"name": "篡改已发布版本"})
    assert conflict.status_code == 409
    conflict_item = client.post(f"/api/mortuary/price-catalogs/{catalog['id']}/items/{hall_item['id']}/discontinue?actor=finance-manager")
    assert conflict_item.status_code == 409
    # 发布后内容未被原地覆盖
    unchanged = client.get(f"/api/mortuary/price-catalogs/{catalog['id']}").json()
    assert unchanged["name"] == "修订目录"


def test_future_effective_version_does_not_affect_current_orders(client):
    publish(client, create_catalog(client, [BODY_CARE], effective_on="2020-01-01"))
    # 预约 2099 年生效的新版本，单价更高
    future = create_catalog(client, [{**BODY_CARE, "unit_price_cents": 99000}], effective_on="2099-01-01")
    publish(client, future)

    effective = client.get("/api/mortuary/price-catalogs/effective").json()
    assert effective["version_no"] == 1

    case = make_case(client, "CASE-PRICE-001")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 1, "requested_by": "family-service"})
    assert order.status_code == 201 and order.json()["unit_price_cents"] == 80000


def test_only_one_published_version_per_effective_date(client):
    first = create_catalog(client, [BODY_CARE], effective_on="2030-06-01")
    publish(client, first)
    second = create_catalog(client, [{**BODY_CARE, "unit_price_cents": 90000}], effective_on="2030-06-01")
    response = client.post(f"/api/mortuary/price-catalogs/{second['id']}/publish", json={"published_by": "finance-manager"})
    assert response.status_code == 409
    assert response.json()["error"]["context"]["effective_on"] == "2030-06-01"
    # 草稿仍可改到其他生效日再发布
    ok = client.post(f"/api/mortuary/price-catalogs/{second['id']}/publish", json={"published_by": "finance-manager", "effective_on": "2030-07-01"})
    assert ok.status_code == 200 and ok.json()["effective_on"] == "2030-07-01"


def test_order_freezes_price_basis_and_history_survives_new_version(client):
    v1 = publish(client, create_catalog(client, [BODY_CARE, HALL], effective_on="2020-01-01"))
    case = make_case(client, "CASE-PRICE-002")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 2, "requested_by": "family-service"}).json()
    assert order["unit_price_cents"] == 80000 and order["amount_cents"] == 160000

    confirmed = client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer").json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["catalog_version_no"] == 1
    assert confirmed["price_catalog_id"] == v1["id"]
    assert confirmed["frozen_service_name"] == "遗体护理"
    assert confirmed["frozen_unit"] == "次"
    assert confirmed["frozen_applicability"] == {}
    assert confirmed["frozen_by"] == "finance-reviewer" and confirmed["frozen_at"]

    invoice = client.post("/api/mortuary/invoices", json={"case_id": case["id"], "order_ids": [order["id"]], "created_by": "cashier"}).json()
    assert invoice["amount_cents"] == 160000

    # 发布调价新版
    publish(client, create_catalog(client, [{**BODY_CARE, "unit_price_cents": 90000}, HALL], effective_on="2021-01-01"))

    # 历史订单与账单仍按冻结口径展示，并可追溯到当时有效的目录版本
    historical_order = client.get(f"/api/mortuary/service-orders/{order['id']}").json()
    assert historical_order["unit_price_cents"] == 80000
    assert historical_order["catalog_version_no"] == 1

    historical_invoice = client.get(f"/api/mortuary/invoices/{invoice['id']}").json()
    assert historical_invoice["amount_cents"] == 160000
    basis = historical_invoice["items"][0]["price_basis"]
    assert basis["catalog_version_no"] == 1
    assert basis["unit_price_cents"] == 80000
    assert basis["frozen_service_name"] == "遗体护理"

    # 新订单按新版计价
    new_order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 1, "requested_by": "family-service"})
    assert new_order.json()["unit_price_cents"] == 90000


def test_draft_confirmed_against_currently_effective_version(client):
    publish(client, create_catalog(client, [BODY_CARE], effective_on="2020-01-01"))
    case = make_case(client, "CASE-PRICE-003")
    draft = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 1, "requested_by": "family-service"}).json()
    assert draft["unit_price_cents"] == 80000
    # 确认前新版已生效：确认时按当时有效目录冻结
    publish(client, create_catalog(client, [{**BODY_CARE, "unit_price_cents": 88000}], effective_on="2021-01-01"))
    confirmed = client.post(f"/api/mortuary/service-orders/{draft['id']}/confirm?actor=finance-reviewer").json()
    assert confirmed["catalog_version_no"] == 2
    assert confirmed["unit_price_cents"] == 88000 and confirmed["amount_cents"] == 88000


def test_discontinued_item_blocks_new_orders_but_keeps_history(client):
    v1 = publish(client, create_catalog(client, [BODY_CARE, HALL], effective_on="2020-01-01"))
    case = make_case(client, "CASE-PRICE-004")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "farewell-hall", "quantity": 1, "requested_by": "family-service"}).json()
    client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer")
    invoice = client.post("/api/mortuary/invoices", json={"case_id": case["id"], "order_ids": [order["id"]], "created_by": "cashier"}).json()

    # 新版中停用送别厅项目
    publish(client, create_catalog(client, [BODY_CARE], effective_on="2021-01-01"))
    blocked = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "farewell-hall", "quantity": 1, "requested_by": "family-service"})
    assert blocked.status_code == 404

    # 历史订单、账单、旧版目录均可查询
    assert client.get(f"/api/mortuary/service-orders/{order['id']}").json()["frozen_service_name"] == "送别厅服务"
    old_invoice = client.get(f"/api/mortuary/invoices/{invoice['id']}").json()
    assert old_invoice["items"][0]["price_basis"]["catalog_version_no"] == 1
    old_catalog = client.get(f"/api/mortuary/price-catalogs/{v1['id']}").json()
    assert {item["service_code"] for item in old_catalog["items"]} == {"body-care", "farewell-hall"}


def test_refund_and_surcharge_link_to_original_order(client):
    publish(client, create_catalog(client, [BODY_CARE], effective_on="2020-01-01"))
    v2 = publish(client, create_catalog(client, [{**BODY_CARE, "unit_price_cents": 90000}], effective_on="2021-01-01"))
    case = make_case(client, "CASE-PRICE-005")
    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "quantity": 1, "requested_by": "family-service"}).json()

    # 未确认订单不能登记调整
    denied = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json={"kind": "refund", "amount_cents": 1000, "reason": "误收", "created_by": "cashier", "idempotency_key": "adj-0001"})
    assert denied.status_code == 409

    client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer")
    invoice = client.post("/api/mortuary/invoices", json={"case_id": case["id"], "order_ids": [order["id"]], "created_by": "cashier"}).json()

    surcharge = {"kind": "surcharge", "amount_cents": 10000, "reason": "节假日加收，按新版目录补差", "created_by": "cashier", "price_catalog_id": v2["id"], "idempotency_key": "adj-surcharge-1"}
    first = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json=surcharge)
    assert first.status_code == 200 and first.json()["order_id"] == order["id"]
    repeated = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json=surcharge)
    assert repeated.status_code == 200 and repeated.json()["id"] == first.json()["id"]

    refund = {"kind": "refund", "amount_cents": 3000, "reason": "家属投诉退差价", "created_by": "cashier", "price_catalog_id": v2["id"], "idempotency_key": "adj-refund-1"}
    assert client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json=refund).status_code == 200

    detail = client.get(f"/api/mortuary/service-orders/{order['id']}").json()
    assert [a["kind"] for a in detail["adjustments"]] == ["surcharge", "refund"]

    invoice_view = client.get(f"/api/mortuary/invoices/{invoice['id']}").json()
    assert invoice_view["adjustment_summary"] == {"surcharge_cents": 10000, "refund_cents": 3000}
    assert len(invoice_view["price_adjustments"]) == 2
    # 账单本金不被调整记录改写
    assert invoice_view["amount_cents"] == 90000

    # 调整引用的目录必须已发布
    bad = client.post(f"/api/mortuary/service-orders/{order['id']}/adjustments", json={"kind": "refund", "amount_cents": 1, "created_by": "cashier", "price_catalog_id": 999999, "idempotency_key": "adj-bad-1"})
    assert bad.status_code == 422


def test_catalog_version_diff(client):
    v1 = publish(client, create_catalog(client, [
        BODY_CARE,
        HALL,
        {"service_code": "cold-storage", "service_name": "冷藏保管", "unit": "日", "unit_price_cents": 5000, "applicability": {}},
    ], effective_on="2020-01-01"))
    v2 = publish(client, create_catalog(client, [
        {**BODY_CARE, "unit_price_cents": 90000},
        HALL,
        {"service_code": "flower", "service_name": "鲜花布置", "unit": "套", "unit_price_cents": 6000, "applicability": {}},
    ], effective_on="2021-01-01"))

    diff = client.get(f"/api/mortuary/price-catalogs/diff/{v1['version_no']}/{v2['version_no']}").json()
    assert diff["summary"] == {"added": 1, "removed": 1, "changed": 1}
    assert [x["service_code"] for x in diff["added"]] == ["flower"]
    assert [x["service_code"] for x in diff["removed"]] == ["cold-storage"]
    changed = diff["changed"][0]
    assert changed["service_code"] == "body-care"
    assert changed["changes"]["unit_price_cents"] == {"from": 80000, "to": 90000, "delta_cents": 10000}

    assert client.get("/api/mortuary/price-catalogs/diff/1/1").status_code == 422
    assert client.get("/api/mortuary/price-catalogs/diff/1/99").status_code == 404


def test_applicability_conditions_are_respected_and_frozen(client):
    items = [
        {"service_code": "body-care", "service_name": "遗体护理（标准）", "unit": "次", "unit_price_cents": 80000, "applicability": {}},
        {"service_code": "body-care", "service_name": "遗体护理（传染病）", "unit": "次", "unit_price_cents": 150000, "applicability": {"case_type": "infectious"}},
    ]
    publish(client, create_catalog(client, items, effective_on="2020-01-01"))
    case = make_case(client, "CASE-PRICE-006")

    quote = client.post("/api/mortuary/service-orders/quote", json={"service_code": "body-care", "applicability": {"case_type": "infectious"}, "quantity": 1})
    assert quote.status_code == 200
    assert quote.json()["unit_price_cents"] == 150000
    assert quote.json()["price_basis"]["version_no"] == 1

    order = client.post("/api/mortuary/service-orders", json={"case_id": case["id"], "service_code": "body-care", "applicability": {"case_type": "infectious"}, "quantity": 1, "requested_by": "family-service"}).json()
    confirmed = client.post(f"/api/mortuary/service-orders/{order['id']}/confirm?actor=finance-reviewer").json()
    assert confirmed["frozen_applicability"] == {"case_type": "infectious"}
    assert confirmed["frozen_service_name"] == "遗体护理（传染病）"
    assert confirmed["amount_cents"] == 150000

    # 未提供匹配条件且无通用条目时
    missing = client.post("/api/mortuary/service-orders/quote", json={"service_code": "body-care", "applicability": {"case_type": "vip"}, "quantity": 1})
    assert missing.status_code == 409
