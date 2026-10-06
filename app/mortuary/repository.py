from __future__ import annotations

import json
import sqlite3
from typing import Any


SCHEMA = r'''
CREATE TABLE IF NOT EXISTS mortuary_cases (
 id INTEGER PRIMARY KEY AUTOINCREMENT, external_ref TEXT NOT NULL UNIQUE,
 decedent_name TEXT NOT NULL, identity_number TEXT, death_time TEXT NOT NULL,
 received_from TEXT NOT NULL, family_contact TEXT NOT NULL, family_phone TEXT NOT NULL,
 special_notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'registered',
 current_location TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custody_transfers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES mortuary_cases(id),
 from_location TEXT NOT NULL, to_location TEXT NOT NULL, seal_code TEXT NOT NULL,
 requested_by TEXT NOT NULL, accepted_by TEXT NOT NULL DEFAULT '', observed_seal_code TEXT NOT NULL DEFAULT '',
 condition_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
 idempotency_key TEXT NOT NULL, requested_at TEXT NOT NULL, accepted_at TEXT,
 UNIQUE(case_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS facility_resources (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 kind TEXT NOT NULL, site_code TEXT NOT NULL, capacity INTEGER NOT NULL,
 attributes_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facility_reservations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, resource_id INTEGER NOT NULL REFERENCES facility_resources(id),
 case_id INTEGER NOT NULL REFERENCES mortuary_cases(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL,
 purpose TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'confirmed', created_by TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(resource_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_reservation_window ON facility_reservations(resource_id,start_at,end_at,status);
CREATE TABLE IF NOT EXISTS funeral_service_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES mortuary_cases(id),
 service_code TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL DEFAULT 0,
 amount_cents INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', requested_by TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '',
 price_catalog_id INTEGER, price_item_id INTEGER,
 pricing_mode TEXT NOT NULL DEFAULT 'manual' CHECK(pricing_mode IN ('catalog','manual')),
 frozen_service_name TEXT NOT NULL DEFAULT '', frozen_unit TEXT NOT NULL DEFAULT '项',
 frozen_conditions_json TEXT NOT NULL DEFAULT '{}', frozen_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_price_catalog ON funeral_service_orders(price_catalog_id);
CREATE TABLE IF NOT EXISTS price_catalogs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published')),
 notes TEXT NOT NULL DEFAULT '', effective_from TEXT,
 published_at TEXT, published_by TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_catalog_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL REFERENCES price_catalogs(id),
 service_code TEXT NOT NULL, service_name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '项',
 unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
 conditions_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 position INTEGER NOT NULL DEFAULT 0,
 UNIQUE(catalog_id,service_code)
);
CREATE INDEX IF NOT EXISTS idx_catalog_items_code ON price_catalog_items(service_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_published_effective_at
 ON price_catalogs(effective_from) WHERE status='published' AND effective_from IS NOT NULL;
CREATE TABLE IF NOT EXISTS service_order_adjustments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES funeral_service_orders(id),
 kind TEXT NOT NULL CHECK(kind IN ('refund','surcharge')), amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
 reason TEXT NOT NULL, reference TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_adjustment ON service_order_adjustments(order_id);
CREATE TABLE IF NOT EXISTS burial_rights (
 id INTEGER PRIMARY KEY AUTOINCREMENT, plot_code TEXT NOT NULL UNIQUE, holder_name TEXT NOT NULL,
 holder_identity TEXT NOT NULL, starts_on TEXT NOT NULL, expires_on TEXT NOT NULL,
 case_id INTEGER REFERENCES mortuary_cases(id), status TEXT NOT NULL DEFAULT 'active',
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS burial_right_renewals (
 id INTEGER PRIMARY KEY AUTOINCREMENT, right_id INTEGER NOT NULL REFERENCES burial_rights(id),
 previous_expires_on TEXT NOT NULL, new_expires_on TEXT NOT NULL, years INTEGER NOT NULL,
 payment_reference TEXT NOT NULL UNIQUE, handled_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES mortuary_cases(id),
 amount_cents INTEGER NOT NULL, paid_cents INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'issued', created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoice_items (
 invoice_id INTEGER NOT NULL REFERENCES invoices(id), order_id INTEGER NOT NULL UNIQUE REFERENCES funeral_service_orders(id),
 amount_cents INTEGER NOT NULL, PRIMARY KEY(invoice_id,order_id)
);
CREATE TABLE IF NOT EXISTS payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 amount_cents INTEGER NOT NULL, channel TEXT NOT NULL, external_reference TEXT NOT NULL UNIQUE,
 received_by TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mortuary_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL,
 event_type TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mortuary_event ON mortuary_events(aggregate_type,aggregate_id,id);
'''

# 旧库升级：订单表补充冻结字段，历史数据不受影响。
ORDER_COLUMN_MIGRATIONS = (
    ("price_catalog_id", "ALTER TABLE funeral_service_orders ADD COLUMN price_catalog_id INTEGER"),
    ("price_item_id", "ALTER TABLE funeral_service_orders ADD COLUMN price_item_id INTEGER"),
    ("pricing_mode", "ALTER TABLE funeral_service_orders ADD COLUMN pricing_mode TEXT NOT NULL DEFAULT 'manual'"),
    ("frozen_service_name", "ALTER TABLE funeral_service_orders ADD COLUMN frozen_service_name TEXT NOT NULL DEFAULT ''"),
    ("frozen_unit", "ALTER TABLE funeral_service_orders ADD COLUMN frozen_unit TEXT NOT NULL DEFAULT '项'"),
    ("frozen_conditions_json", "ALTER TABLE funeral_service_orders ADD COLUMN frozen_conditions_json TEXT NOT NULL DEFAULT '{}'"),
    ("frozen_at", "ALTER TABLE funeral_service_orders ADD COLUMN frozen_at TEXT"),
)


class MortuaryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_schema(self) -> None:
        self.connection.executescript(SCHEMA)
        existing = {row["name"] for row in self.connection.execute("PRAGMA table_info(funeral_service_orders)").fetchall()}
        for column, ddl in ORDER_COLUMN_MIGRATIONS:
            if column not in existing:
                self.connection.execute(ddl)

    @staticmethod
    def one(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    def event(self, kind: str, aggregate_id: int | str, event_type: str, actor: str, payload: dict[str, Any], now: str) -> None:
        self.connection.execute("INSERT INTO mortuary_events(aggregate_type,aggregate_id,event_type,actor,payload_json,created_at) VALUES(?,?,?,?,?,?)", (kind, str(aggregate_id), event_type, actor, json.dumps(payload, ensure_ascii=False, sort_keys=True), now))

    def case(self, case_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM mortuary_cases WHERE id=?", (case_id,)).fetchone())

    def case_ref(self, ref: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM mortuary_cases WHERE external_ref=?", (ref,)).fetchone())

    def transfer(self, transfer_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM custody_transfers WHERE id=?", (transfer_id,)).fetchone())

    def transfer_key(self, case_id: int, key: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM custody_transfers WHERE case_id=? AND idempotency_key=?", (case_id, key)).fetchone())

    def resource_code(self, code: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM facility_resources WHERE code=?", (code,)).fetchone())

    def resource(self, resource_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM facility_resources WHERE id=?", (resource_id,)).fetchone())

    def reservation_key(self, resource_id: int, key: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM facility_reservations WHERE resource_id=? AND idempotency_key=?", (resource_id, key)).fetchone())

    def reservation(self, reservation_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT r.*,f.code resource_code,f.kind resource_kind FROM facility_reservations r JOIN facility_resources f ON f.id=r.resource_id WHERE r.id=?", (reservation_id,)).fetchone())

    def conflicts(self, resource_id: int, start_at: str, end_at: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM facility_reservations WHERE resource_id=? AND status='confirmed' AND start_at>=? AND start_at<? ORDER BY start_at", (resource_id, start_at, end_at)).fetchall()
        return [dict(row) for row in rows]

    def order(self, order_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM funeral_service_orders WHERE id=?", (order_id,)).fetchone())

    def catalog(self, catalog_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE id=?", (catalog_id,)).fetchone())

    def catalog_label(self, label: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE label=?", (label,)).fetchone())

    def catalogs(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.connection.execute("SELECT * FROM price_catalogs WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM price_catalogs ORDER BY id DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["item_count"] = self.connection.execute("SELECT COUNT(*) FROM price_catalog_items WHERE catalog_id=?", (item["id"],)).fetchone()[0]
            result.append(item)
        return result

    @staticmethod
    def decode_item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["conditions"] = json.loads(item.pop("conditions_json"))
        item["active"] = bool(item["active"])
        return item

    def catalog_items(self, catalog_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM price_catalog_items WHERE catalog_id=? ORDER BY position,id", (catalog_id,)).fetchall()
        return [self.decode_item(row) for row in rows]

    def catalog_item(self, catalog_id: int, service_code: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM price_catalog_items WHERE catalog_id=? AND service_code=?", (catalog_id, service_code)).fetchone()
        return None if row is None else self.decode_item(row)

    def catalog_item_by_id(self, item_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM price_catalog_items WHERE id=?", (item_id,)).fetchone()
        return None if row is None else self.decode_item(row)

    def replace_items(self, catalog_id: int, items: list[dict[str, Any]], now: str) -> None:
        self.connection.execute("DELETE FROM price_catalog_items WHERE catalog_id=?", (catalog_id,))
        for position, item in enumerate(items):
            self.connection.execute(
                "INSERT INTO price_catalog_items(catalog_id,service_code,service_name,unit,unit_price_cents,conditions_json,active,position) VALUES(?,?,?,?,?,?,?,?)",
                (catalog_id, item["service_code"], item["service_name"], item["unit"], item["unit_price_cents"], json.dumps(item["conditions"], ensure_ascii=False, sort_keys=True), 1 if item["active"] else 0, position),
            )
        self.connection.execute("UPDATE price_catalogs SET updated_at=? WHERE id=?", (now, catalog_id))

    def effective_catalog(self, at_storage: str) -> dict[str, Any] | None:
        # 仅取生效时间已到的版本；未来生效的目录不会影响当前下单。
        return self.one(self.connection.execute(
            "SELECT * FROM price_catalogs WHERE status='published' AND effective_from IS NOT NULL AND effective_from<=? ORDER BY effective_from DESC,id DESC LIMIT 1",
            (at_storage,),
        ).fetchone())

    def latest_published(self) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE status='published' ORDER BY effective_from DESC,id DESC LIMIT 1").fetchone())

    def adjustment_reference(self, reference: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM service_order_adjustments WHERE reference=?", (reference,)).fetchone())

    def order_adjustments(self, order_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM service_order_adjustments WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
        return [dict(row) for row in rows]

    def invoice_adjustments(self, invoice_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT a.* FROM service_order_adjustments a JOIN invoice_items i ON i.order_id=a.order_id WHERE i.invoice_id=? ORDER BY a.id",
            (invoice_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def right(self, right_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM burial_rights WHERE id=?", (right_id,)).fetchone())

    def right_plot(self, plot_code: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM burial_rights WHERE plot_code=?", (plot_code,)).fetchone())

    def invoice(self, invoice_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone())

    def timeline(self, kind: str, aggregate_id: int | str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM mortuary_events WHERE aggregate_type=? AND aggregate_id=? ORDER BY id", (kind, str(aggregate_id))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
