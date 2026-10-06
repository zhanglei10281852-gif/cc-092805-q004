from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.database import get_connection, transaction
from app.mortuary.repository import MortuaryRepository


class MortuaryService:
    def __init__(self, connection: sqlite3.Connection | None = None, clock: Clock | None = None) -> None:
        self.connection = connection or get_connection()
        self.clock = clock or SystemClock()
        self.repository = MortuaryRepository(self.connection)
        self.repository.ensure_schema()

    def now(self) -> str:
        return to_storage(self.clock.now())

    def create_case(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        now = self.now()
        values = dict(payload)
        values["death_time"] = to_storage(values["death_time"])
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            if repo.case_ref(values["external_ref"]):
                raise ConflictError("外部业务编号已存在")
            cursor = connection.execute(
                "INSERT INTO mortuary_cases(external_ref,decedent_name,identity_number,death_time,received_from,family_contact,family_phone,special_notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (values["external_ref"], values["decedent_name"], values.get("identity_number"), values["death_time"], values["received_from"], values["family_contact"], values["family_phone"], values.get("special_notes", ""), now, now),
            )
            case = repo.case(int(cursor.lastrowid)) or {}
            repo.event("case", case["id"], "case.registered", actor, {"external_ref": case["external_ref"], "received_from": case["received_from"]}, now)
            return case

    def list_cases(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mortuary_cases"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def get_case(self, case_id: int) -> dict[str, Any]:
        case = self.repository.case(case_id)
        if case is None:
            raise NotFoundError("逝者业务档案不存在")
        case["custody_transfers"] = [dict(row) for row in self.connection.execute("SELECT * FROM custody_transfers WHERE case_id=? ORDER BY id", (case_id,)).fetchall()]
        case["reservations"] = [dict(row) for row in self.connection.execute("SELECT r.*,f.code resource_code,f.kind resource_kind FROM facility_reservations r JOIN facility_resources f ON f.id=r.resource_id WHERE r.case_id=? ORDER BY r.start_at", (case_id,)).fetchall()]
        case["service_orders"] = [self._decorate_order(dict(row), self.repository) for row in self.connection.execute("SELECT * FROM funeral_service_orders WHERE case_id=? ORDER BY id", (case_id,)).fetchall()]
        for order in case["service_orders"]:
            order["adjustments"] = self.repository.order_adjustments(order["id"])
        case["timeline"] = self.repository.timeline("case", case_id)
        return case

    def request_transfer(self, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            case = repo.case(case_id)
            if case is None:
                raise NotFoundError("逝者业务档案不存在")
            existing = repo.transfer_key(case_id, payload["idempotency_key"])
            if existing:
                return existing
            if case["current_location"] and case["current_location"] != payload["from_location"]:
                raise ConflictError("交接起点与当前保管位置不一致", context={"current_location": case["current_location"]})
            cursor = connection.execute("INSERT INTO custody_transfers(case_id,from_location,to_location,seal_code,requested_by,idempotency_key,requested_at) VALUES(?,?,?,?,?,?,?)", (case_id, payload["from_location"], payload["to_location"], payload["seal_code"], payload["requested_by"], payload["idempotency_key"], now))
            transfer = repo.transfer(int(cursor.lastrowid)) or {}
            repo.event("case", case_id, "custody.requested", payload["requested_by"], {"transfer_id": transfer["id"], "from": payload["from_location"], "to": payload["to_location"]}, now)
            return transfer

    def accept_transfer(self, transfer_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            transfer = repo.transfer(transfer_id)
            if transfer is None:
                raise NotFoundError("保管交接记录不存在")
            if transfer["status"] == "accepted":
                return transfer
            if transfer["status"] != "pending":
                raise ConflictError("当前交接状态不能确认")
            if transfer["seal_code"] != payload["observed_seal_code"]:
                connection.execute("UPDATE custody_transfers SET status='rejected',accepted_by=?,observed_seal_code=?,condition_note=?,accepted_at=? WHERE id=?", (payload["accepted_by"], payload["observed_seal_code"], payload["condition_note"], now, transfer_id))
                repo.event("case", transfer["case_id"], "custody.rejected", payload["accepted_by"], {"transfer_id": transfer_id, "reason": "seal_mismatch"}, now)
                raise ConflictError("封签编号不一致，交接已拒绝")
            connection.execute("UPDATE custody_transfers SET status='accepted',accepted_by=?,observed_seal_code=?,condition_note=?,accepted_at=? WHERE id=?", (payload["accepted_by"], payload["observed_seal_code"], payload["condition_note"], now, transfer_id))
            connection.execute("UPDATE mortuary_cases SET status='in_custody',current_location=?,version=version+1,updated_at=? WHERE id=?", (transfer["to_location"], now, transfer["case_id"]))
            repo.event("case", transfer["case_id"], "custody.accepted", payload["accepted_by"], {"transfer_id": transfer_id, "location": transfer["to_location"]}, now)
            return repo.transfer(transfer_id) or {}

    def create_resource(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            if repo.resource_code(payload["code"]):
                raise ConflictError("设施资源编码已存在")
            cursor = connection.execute("INSERT INTO facility_resources(code,name,kind,site_code,capacity,attributes_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (payload["code"], payload["name"], payload["kind"], payload["site_code"], payload["capacity"], json.dumps(payload["attributes"], ensure_ascii=False, sort_keys=True), now, now))
            resource = repo.resource(int(cursor.lastrowid)) or {}
            repo.event("resource", resource["id"], "resource.created", actor, {"code": payload["code"], "kind": payload["kind"]}, now)
            return resource

    def list_resources(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = self.connection.execute("SELECT * FROM facility_resources WHERE kind=? ORDER BY code", (kind,)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM facility_resources ORDER BY kind,code").fetchall()
        return [dict(row) for row in rows]

    def reserve(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        start_at = to_storage(payload["start_at"])
        end_at = to_storage(payload["end_at"])
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            resource = repo.resource_code(payload["resource_code"])
            if resource is None or not resource["active"]:
                raise NotFoundError("设施资源不存在或已停用")
            if repo.case(payload["case_id"]) is None:
                raise NotFoundError("逝者业务档案不存在")
            existing = repo.reservation_key(resource["id"], payload["idempotency_key"])
            if existing:
                if existing["case_id"] != payload["case_id"] or existing["start_at"] != start_at or existing["end_at"] != end_at:
                    raise ConflictError("同一幂等键对应了不同预约内容")
                return repo.reservation(existing["id"]) or existing
            conflicts = repo.conflicts(resource["id"], start_at, end_at)
            if len(conflicts) >= int(resource["capacity"]):
                raise ConflictError("预约时段与现有安排冲突", context={"conflict_ids": [x["id"] for x in conflicts]})
            cursor = connection.execute("INSERT INTO facility_reservations(resource_id,case_id,start_at,end_at,purpose,created_by,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (resource["id"], payload["case_id"], start_at, end_at, payload["purpose"], payload["created_by"], payload["idempotency_key"], now, now))
            reservation = repo.reservation(int(cursor.lastrowid)) or {}
            connection.execute("UPDATE mortuary_cases SET status='services_planned',version=version+1,updated_at=? WHERE id=? AND status IN ('registered','in_custody')", (now, payload["case_id"]))
            repo.event("case", payload["case_id"], "reservation.confirmed", payload["created_by"], {"reservation_id": reservation["id"], "resource_code": payload["resource_code"], "start_at": start_at, "end_at": end_at}, now)
            return reservation

    def cancel_reservation(self, reservation_id: int, actor: str, reason: str) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            reservation = repo.reservation(reservation_id)
            if reservation is None:
                raise NotFoundError("预约记录不存在")
            if reservation["status"] == "cancelled":
                return reservation
            if reservation["status"] == "completed":
                raise ConflictError("已完成的预约不能取消")
            connection.execute("UPDATE facility_reservations SET status='cancelled',updated_at=? WHERE id=?", (now, reservation_id))
            repo.event("case", reservation["case_id"], "reservation.cancelled", actor, {"reservation_id": reservation_id, "reason": reason}, now)
            return repo.reservation(reservation_id) or {}

    # ------------------------------------------------------------------
    # 价格目录版本
    # ------------------------------------------------------------------
    def create_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        items = payload["items"]
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            if repo.catalog_label(payload["label"]):
                raise ConflictError("价格目录版本号已存在")
            cursor = connection.execute(
                "INSERT INTO price_catalogs(label,status,notes,effective_from,published_at,published_by,created_by,created_at,updated_at) VALUES(?, 'draft', '', NULL, NULL, '', ?, ?, ?)",
                (payload["label"], payload["created_by"], now, now),
            )
            catalog_id = int(cursor.lastrowid)
            if payload.get("notes"):
                connection.execute("UPDATE price_catalogs SET notes=? WHERE id=?", (payload["notes"], catalog_id))
            repo.replace_items(catalog_id, items, now)
            catalog = repo.catalog(catalog_id) or {}
            catalog["items"] = repo.catalog_items(catalog_id)
            repo.event("price_catalog", catalog_id, "price_catalog.created", payload["created_by"], {"label": payload["label"], "item_count": len(items)}, now)
            return catalog

    def update_catalog(self, catalog_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            catalog = repo.catalog(catalog_id)
            if catalog is None:
                raise NotFoundError("价格目录版本不存在")
            if catalog["status"] != "draft":
                raise ConflictError("已发布的价格目录不能原地修改，请新建版本")
            if payload.get("notes") is not None:
                connection.execute("UPDATE price_catalogs SET notes=?,updated_at=? WHERE id=?", (payload["notes"], now, catalog_id))
            if payload.get("items") is not None:
                repo.replace_items(catalog_id, payload["items"], now)
            catalog = repo.catalog(catalog_id) or {}
            catalog["items"] = repo.catalog_items(catalog_id)
            return catalog

    def get_catalog(self, catalog_id: int) -> dict[str, Any]:
        catalog = self.repository.catalog(catalog_id)
        if catalog is None:
            raise NotFoundError("价格目录版本不存在")
        catalog["items"] = self.repository.catalog_items(catalog_id)
        return catalog

    def list_catalogs(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.repository.catalogs(status)

    def current_pricing(self) -> dict[str, Any]:
        """当前下单所依据的价格目录；未来生效版本不会提前出现。"""
        at = self.now()
        catalog = self.repository.effective_catalog(at)
        if catalog is None:
            return {"at": at, "catalog": None, "items": []}
        catalog["items"] = self.repository.catalog_items(catalog["id"])
        return {"at": at, "catalog": catalog, "items": [item for item in catalog["items"] if item["active"]]}

    def publish_catalog(self, catalog_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        effective_from = to_storage(payload["effective_from"])
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            catalog = repo.catalog(catalog_id)
            if catalog is None:
                raise NotFoundError("价格目录版本不存在")
            if catalog["status"] == "published":
                raise ConflictError("价格目录已发布，不能重复发布或原地覆盖")
            items = repo.catalog_items(catalog_id)
            if not items:
                raise ValidationError("空目录不能发布")
            if not any(item["active"] for item in items):
                raise ValidationError("目录至少需要保留一个启用项目")
            # 生效时间必须严格晚于最近一版：保证任意时刻只有一个有效版本，且版本链可追溯。
            latest = repo.latest_published()
            if latest is not None and effective_from <= (latest["effective_from"] or ""):
                raise ConflictError("新版本生效时间必须晚于最近已发布版本", context={"latest_effective_from": latest["effective_from"]})
            try:
                connection.execute(
                    "UPDATE price_catalogs SET status='published',effective_from=?,published_at=?,published_by=?,updated_at=? WHERE id=? AND status='draft'",
                    (effective_from, now, payload["published_by"], now, catalog_id),
                )
            except sqlite3.IntegrityError:  # 并发发布撞上同一生效时间
                raise ConflictError("该生效时间已有有效价格目录，同一时刻只能存在一个有效版本")
            catalog = repo.catalog(catalog_id) or {}
            catalog["items"] = items
            repo.event("price_catalog", catalog_id, "price_catalog.published", payload["published_by"], {"effective_from": effective_from, "item_count": len(items)}, now)
            return catalog

    def diff_catalogs(self, base_id: int, target_id: int) -> dict[str, Any]:
        base = self.repository.catalog(base_id)
        target = self.repository.catalog(target_id)
        if base is None or target is None:
            raise NotFoundError("待比较的价格目录版本不存在")
        base_items = {item["service_code"]: item for item in self.repository.catalog_items(base_id)}
        target_items = {item["service_code"]: item for item in self.repository.catalog_items(target_id)}
        added, removed, changed = [], [], []
        for code in sorted(set(base_items) | set(target_items)):
            before, after = base_items.get(code), target_items.get(code)
            if before is None:
                added.append({"service_code": code, "service_name": after["service_name"], "unit_price_cents": after["unit_price_cents"], "active": after["active"]})
            elif after is None:
                removed.append({"service_code": code, "service_name": before["service_name"], "unit_price_cents": before["unit_price_cents"], "active": before["active"]})
            else:
                entry: dict[str, Any] = {"service_code": code, "before": before, "after": after}
                if before["unit_price_cents"] != after["unit_price_cents"]:
                    entry["price_delta_cents"] = after["unit_price_cents"] - before["unit_price_cents"]
                if before["conditions"] != after["conditions"]:
                    entry["conditions_changed"] = True
                if before["service_name"] != after["service_name"] or before["unit"] != after["unit"]:
                    entry["description_changed"] = True
                if before["active"] != after["active"]:
                    entry["active_changed"] = {"before": before["active"], "after": after["active"]}
                if len(entry) > 2:
                    changed.append(entry)
        return {
            "base_catalog": {"id": base["id"], "label": base["label"], "status": base["status"], "effective_from": base["effective_from"]},
            "target_catalog": {"id": target["id"], "label": target["label"], "status": target["status"], "effective_from": target["effective_from"]},
            "added": added,
            "removed": removed,
            "changed": changed,
        }

    # ------------------------------------------------------------------
    # 服务订单：按下单时有效目录冻结价格
    # ------------------------------------------------------------------
    def add_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            if repo.case(payload["case_id"]) is None:
                raise NotFoundError("逝者业务档案不存在")
            manual_price = payload.get("unit_price_cents")
            if manual_price is None:
                catalog = repo.effective_catalog(now)
                if catalog is None:
                    raise ConflictError("当前没有已生效的价格目录，无法自动计价")
                price_item = repo.catalog_item(catalog["id"], payload["service_code"])
                if price_item is None or not price_item["active"]:
                    raise NotFoundError("当前有效价格目录中不存在该服务项目或项目已停用")
                unit_price = int(price_item["unit_price_cents"])
                pricing = "catalog"
                catalog_id: int | None = catalog["id"]
                # 草稿只记录取价来源；项目/单价/条件的正式冻结发生在确认时。
                item_id: int | None = price_item["id"]
            else:
                unit_price = int(manual_price)
                pricing = "manual"
                catalog_id = None
                item_id = None
            amount = payload["quantity"] * unit_price
            cursor = connection.execute(
                "INSERT INTO funeral_service_orders(case_id,service_code,quantity,unit_price_cents,amount_cents,status,requested_by,notes,price_catalog_id,price_item_id,pricing_mode,created_at,updated_at) VALUES(?,?,?,?,?, 'draft', ?,?,?,?,?,?,?)",
                (payload["case_id"], payload["service_code"], payload["quantity"], unit_price, amount, payload["requested_by"], payload["notes"], catalog_id, item_id, pricing, now, now),
            )
            order = repo.order(int(cursor.lastrowid)) or {}
            event_payload = {"order_id": order["id"], "service_code": payload["service_code"], "amount_cents": amount, "pricing_mode": pricing}
            if catalog_id is not None:
                event_payload["price_catalog_id"] = catalog_id
            repo.event("case", payload["case_id"], "service_order.created", payload["requested_by"], event_payload, now)
            return order

    def confirm_order(self, order_id: int, actor: str) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            order = repo.order(order_id)
            if order is None:
                raise NotFoundError("服务订单不存在")
            if order["status"] == "confirmed":
                return self._order_with_pricing(order_id, repo)
            if order["status"] != "draft":
                raise ConflictError("当前订单状态不能确认")
            frozen: dict[str, Any]
            if order["pricing_mode"] == "catalog":
                # 确认时以当时有效目录为准冻结项目、单价、适用条件与目录版本；
                # 未来生效的版本此前不影响下单，此刻若已生效则按新版本冻结。
                catalog = repo.effective_catalog(now)
                if catalog is None:
                    raise ConflictError("当前没有已生效的价格目录，无法确认订单")
                price_item = repo.catalog_item(catalog["id"], order["service_code"])
                if price_item is None or not price_item["active"]:
                    raise ConflictError("该服务项目在当前有效价格目录中已停用，无法确认订单", context={"service_code": order["service_code"], "price_catalog_id": catalog["id"]})
                amount = int(order["quantity"]) * int(price_item["unit_price_cents"])
                frozen = {
                    "price_catalog_id": catalog["id"],
                    "price_item_id": price_item["id"],
                    "unit_price_cents": price_item["unit_price_cents"],
                    "service_name": price_item["service_name"],
                    "unit": price_item["unit"],
                    "conditions": price_item["conditions"],
                    "amount_cents": amount,
                    "basis": "catalog",
                }
                connection.execute(
                    "UPDATE funeral_service_orders SET unit_price_cents=?,amount_cents=?,price_catalog_id=?,price_item_id=?,frozen_service_name=?,frozen_unit=?,frozen_conditions_json=?,frozen_at=?,updated_at=? WHERE id=?",
                    (price_item["unit_price_cents"], amount, catalog["id"], price_item["id"], price_item["service_name"], price_item["unit"], json.dumps(price_item["conditions"], ensure_ascii=False, sort_keys=True), now, now, order_id),
                )
            else:
                frozen = {
                    "price_catalog_id": None,
                    "price_item_id": None,
                    "unit_price_cents": order["unit_price_cents"],
                    "service_name": order["service_code"],
                    "unit": "项",
                    "conditions": {},
                    "amount_cents": int(order["amount_cents"]),
                    "basis": "manual",
                }
                connection.execute(
                    "UPDATE funeral_service_orders SET frozen_service_name=?,frozen_unit=?,frozen_conditions_json=?,frozen_at=?,updated_at=? WHERE id=?",
                    (order["service_code"], "项", "{}", now, now, order_id),
                )
            connection.execute("UPDATE funeral_service_orders SET status='confirmed' WHERE id=?", (order_id,))
            repo.event(
                "case", order["case_id"], "service_order.confirmed", actor,
                {"order_id": order_id, "price_catalog_id": frozen["price_catalog_id"], "unit_price_cents": frozen["unit_price_cents"], "frozen_at": now},
                now,
            )
            return self._order_with_pricing(order_id, repo)

    @staticmethod
    def _decorate_order(row: dict[str, Any], repo: MortuaryRepository) -> dict[str, Any]:
        order = dict(row)
        order["frozen_conditions"] = json.loads(order.pop("frozen_conditions_json") or "{}")
        catalog = None
        if order["price_catalog_id"]:
            catalog = repo.catalog(order["price_catalog_id"])
        order["price_catalog"] = None if catalog is None else {
            "id": catalog["id"], "label": catalog["label"], "status": catalog["status"], "effective_from": catalog["effective_from"],
        }
        order["price_basis"] = {
            "service_code": order["service_code"],
            "service_name": order.get("frozen_service_name") or "",
            "unit": order.get("frozen_unit") or "项",
            "unit_price_cents": order["unit_price_cents"],
            "conditions": order["frozen_conditions"],
            "catalog_id": order["price_catalog_id"],
            "catalog_label": None if catalog is None else catalog["label"],
            "catalog_effective_from": None if catalog is None else catalog["effective_from"],
            "frozen_at": order.get("frozen_at"),
            "basis": order["pricing_mode"],
        }
        return order

    def _order_with_pricing(self, order_id: int, repo: MortuaryRepository) -> dict[str, Any]:
        order = repo.order(order_id)
        if order is None:
            raise NotFoundError("服务订单不存在")
        result = self._decorate_order(order, repo)
        result["adjustments"] = repo.order_adjustments(order_id)
        return result

    def get_order(self, order_id: int) -> dict[str, Any]:
        return self._order_with_pricing(order_id, self.repository)

    def create_order_adjustment(self, order_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """退款/补差只追加调整记录并关联原订单，原订单的冻结金额永不改写。"""
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            order = repo.order(order_id)
            if order is None:
                raise NotFoundError("服务订单不存在")
            if order["status"] not in {"confirmed", "invoiced"}:
                raise ConflictError("只有已确认订单可以登记退款或补差")
            if repo.adjustment_reference(payload["reference"]):
                raise ConflictError("调整凭证号已存在")
            connection.execute(
                "INSERT INTO service_order_adjustments(order_id,kind,amount_cents,reason,reference,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (order_id, payload["kind"], payload["amount_cents"], payload["reason"], payload["reference"], payload["created_by"], now),
            )
            adjustment = repo.adjustment_reference(payload["reference"]) or {}
            repo.event(
                "case", order["case_id"], f"service_order.{payload['kind']}", payload["created_by"],
                {"order_id": order_id, "amount_cents": payload["amount_cents"], "reference": payload["reference"], "reason": payload["reason"]},
                now,
            )
            return {"order": self._order_with_pricing(order_id, repo), "adjustment": adjustment}


    def create_right(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            if repo.right_plot(payload["plot_code"]):
                raise ConflictError("墓位已经登记权属")
            cursor = connection.execute("INSERT INTO burial_rights(plot_code,holder_name,holder_identity,starts_on,expires_on,case_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (payload["plot_code"], payload["holder_name"], payload["holder_identity"], payload["starts_on"].isoformat(), payload["expires_on"].isoformat(), payload.get("case_id"), now, now))
            right = repo.right(int(cursor.lastrowid)) or {}
            repo.event("burial_right", right["id"], "right.created", actor, {"plot_code": right["plot_code"], "expires_on": right["expires_on"]}, now)
            return right

    def renew_right(self, right_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            right = repo.right(right_id)
            if right is None:
                raise NotFoundError("墓位权属不存在")
            if right["status"] not in {"active", "expired"}:
                raise ConflictError("当前权属状态不能续期")
            duplicate = connection.execute("SELECT 1 FROM burial_right_renewals WHERE payment_reference=?", (payload["payment_reference"],)).fetchone()
            if duplicate:
                return repo.right(right_id) or {}
            previous = date.fromisoformat(right["expires_on"])
            new_expiry = previous.replace(year=previous.year + payload["years"])
            connection.execute("UPDATE burial_rights SET expires_on=?,status='active',version=version+1,updated_at=? WHERE id=?", (new_expiry.isoformat(), now, right_id))
            connection.execute("INSERT INTO burial_right_renewals(right_id,previous_expires_on,new_expires_on,years,payment_reference,handled_by,created_at) VALUES(?,?,?,?,?,?,?)", (right_id, previous.isoformat(), new_expiry.isoformat(), payload["years"], payload["payment_reference"], payload["handled_by"], now))
            repo.event("burial_right", right_id, "right.renewed", payload["handled_by"], {"previous_expires_on": previous.isoformat(), "new_expires_on": new_expiry.isoformat(), "payment_reference": payload["payment_reference"]}, now)
            return repo.right(right_id) or {}

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        order_ids = list(dict.fromkeys(payload["order_ids"]))
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            orders = [repo.order(order_id) for order_id in order_ids]
            if any(order is None for order in orders):
                raise NotFoundError("存在找不到的服务订单")
            typed = [order for order in orders if order is not None]
            if any(order["case_id"] != payload["case_id"] for order in typed):
                raise ValidationError("账单只能包含同一业务档案的订单")
            if any(order["status"] != "confirmed" for order in typed):
                raise ConflictError("只有已确认订单可以开具账单")
            amount = sum(int(order["amount_cents"]) for order in typed)
            cursor = connection.execute("INSERT INTO invoices(case_id,amount_cents,created_by,created_at,updated_at) VALUES(?,?,?,?,?)", (payload["case_id"], amount, payload["created_by"], now, now))
            invoice_id = int(cursor.lastrowid)
            for order in typed:
                connection.execute("INSERT INTO invoice_items(invoice_id,order_id,amount_cents) VALUES(?,?,?)", (invoice_id, order["id"], order["amount_cents"]))
                connection.execute("UPDATE funeral_service_orders SET status='invoiced',updated_at=? WHERE id=?", (now, order["id"]))
            repo.event("case", payload["case_id"], "invoice.issued", payload["created_by"], {"invoice_id": invoice_id, "amount_cents": amount, "order_ids": order_ids}, now)
            return self.get_invoice(invoice_id, repo)

    def pay(self, invoice_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        with transaction(immediate=True) as connection:
            repo = MortuaryRepository(connection)
            invoice = repo.invoice(invoice_id)
            if invoice is None:
                raise NotFoundError("账单不存在")
            duplicate = connection.execute("SELECT * FROM payments WHERE external_reference=?", (payload["external_reference"],)).fetchone()
            if duplicate:
                if duplicate["invoice_id"] != invoice_id or duplicate["amount_cents"] != payload["amount_cents"]:
                    raise ConflictError("支付流水号已用于其他款项")
                return self.get_invoice(invoice_id, repo)
            adjustments = repo.invoice_adjustments(invoice_id)
            refund_total = sum(int(row["amount_cents"]) for row in adjustments if row["kind"] == "refund")
            surcharge_total = sum(int(row["amount_cents"]) for row in adjustments if row["kind"] == "surcharge")
            remaining = int(invoice["amount_cents"]) + surcharge_total - refund_total - int(invoice["paid_cents"])
            if payload["amount_cents"] > remaining:
                raise ValidationError("支付金额超过账单未付余额")
            connection.execute("INSERT INTO payments(invoice_id,amount_cents,channel,external_reference,received_by,received_at) VALUES(?,?,?,?,?,?)", (invoice_id, payload["amount_cents"], payload["channel"], payload["external_reference"], payload["received_by"], now))
            paid = int(invoice["paid_cents"]) + payload["amount_cents"]
            status = "paid" if paid == int(invoice["amount_cents"]) else "partially_paid"
            connection.execute("UPDATE invoices SET paid_cents=?,status=?,updated_at=? WHERE id=?", (paid, status, now, invoice_id))
            repo.event("case", invoice["case_id"], "invoice.payment_received", payload["received_by"], {"invoice_id": invoice_id, "amount_cents": payload["amount_cents"], "external_reference": payload["external_reference"]}, now)
            return self.get_invoice(invoice_id, repo)

    def get_invoice(self, invoice_id: int, repo: MortuaryRepository | None = None) -> dict[str, Any]:
        repo = repo or self.repository
        invoice = repo.invoice(invoice_id)
        if invoice is None:
            raise NotFoundError("账单不存在")
        invoice["items"] = []
        for row in repo.connection.execute("SELECT ii.*,o.service_code,o.quantity,o.unit_price_cents,o.price_catalog_id,o.frozen_service_name,o.frozen_unit,o.frozen_conditions_json,o.frozen_at,o.pricing_mode FROM invoice_items ii JOIN funeral_service_orders o ON o.id=ii.order_id WHERE ii.invoice_id=? ORDER BY ii.order_id", (invoice_id,)).fetchall():
            line = dict(row)
            line["frozen_conditions"] = json.loads(line.pop("frozen_conditions_json") or "{}")
            catalog = repo.catalog(line["price_catalog_id"]) if line["price_catalog_id"] else None
            line["price_basis"] = {
                "service_code": line["service_code"],
                "service_name": line.get("frozen_service_name") or "",
                "unit": line.get("frozen_unit") or "项",
                "unit_price_cents": line["unit_price_cents"],
                "catalog_id": line["price_catalog_id"],
                "catalog_label": None if catalog is None else catalog["label"],
                "catalog_effective_from": None if catalog is None else catalog["effective_from"],
                "conditions": line["frozen_conditions"],
                "frozen_at": line.get("frozen_at"),
                "basis": line["pricing_mode"],
            }
            invoice["items"].append(line)
        invoice["payments"] = [dict(row) for row in repo.connection.execute("SELECT * FROM payments WHERE invoice_id=? ORDER BY id", (invoice_id,)).fetchall()]
        invoice["adjustments"] = repo.invoice_adjustments(invoice_id)
        refund_total = sum(int(row["amount_cents"]) for row in invoice["adjustments"] if row["kind"] == "refund")
        surcharge_total = sum(int(row["amount_cents"]) for row in invoice["adjustments"] if row["kind"] == "surcharge")
        invoice["refund_cents"] = refund_total
        invoice["surcharge_cents"] = surcharge_total
        invoice["remaining_cents"] = int(invoice["amount_cents"]) - int(invoice["paid_cents"]) - refund_total + surcharge_total
        return invoice
