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
 service_code TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL,
 amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', requested_by TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '',
 requested_applicability_json TEXT NOT NULL DEFAULT '{}',
 price_catalog_id INTEGER, price_item_id INTEGER,
 catalog_version_no INTEGER, frozen_service_name TEXT NOT NULL DEFAULT '',
 frozen_unit TEXT NOT NULL DEFAULT '', frozen_applicability_json TEXT NOT NULL DEFAULT '{}',
 frozen_by TEXT NOT NULL DEFAULT '', frozen_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_price_adjustments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES funeral_service_orders(id),
 kind TEXT NOT NULL CHECK(kind IN ('surcharge','refund')), amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
 reason TEXT NOT NULL DEFAULT '', price_catalog_id INTEGER REFERENCES price_catalogs(id),
 created_by TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_adjustment ON order_price_adjustments(order_id,id);
CREATE TABLE IF NOT EXISTS price_catalogs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, version_no INTEGER NOT NULL UNIQUE,
 name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','retired')),
 effective_on TEXT, notes TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL DEFAULT '', published_by TEXT NOT NULL DEFAULT '', published_at TEXT,
 retired_by TEXT NOT NULL DEFAULT '', retired_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_catalog_effective ON price_catalogs(effective_on) WHERE status='published' AND effective_on IS NOT NULL;
CREATE TABLE IF NOT EXISTS price_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL REFERENCES price_catalogs(id),
 service_code TEXT NOT NULL, service_name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '次',
 unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
 applicability_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','discontinued')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(catalog_id,service_code,applicability_json)
);
CREATE INDEX IF NOT EXISTS idx_price_item_lookup ON price_items(catalog_id,service_code,status);
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


class MortuaryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_schema(self) -> None:
        self.connection.executescript(SCHEMA)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """对早于价格目录版本功能上线的旧库做幂等补列。"""
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(funeral_service_orders)").fetchall()}
        additions = {
            "requested_applicability_json": "TEXT NOT NULL DEFAULT '{}'",
            "price_catalog_id": "INTEGER",
            "price_item_id": "INTEGER",
            "catalog_version_no": "INTEGER",
            "frozen_service_name": "TEXT NOT NULL DEFAULT ''",
            "frozen_unit": "TEXT NOT NULL DEFAULT ''",
            "frozen_applicability_json": "TEXT NOT NULL DEFAULT '{}'",
            "frozen_by": "TEXT NOT NULL DEFAULT ''",
            "frozen_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE funeral_service_orders ADD COLUMN {name} {declaration}")

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

    def order_adjustments(self, order_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM order_price_adjustments WHERE order_id=? ORDER BY id", (order_id,)).fetchall()]

    def adjustment_key(self, key: str) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM order_price_adjustments WHERE idempotency_key=?", (key,)).fetchone())

    def catalog(self, catalog_id: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE id=?", (catalog_id,)).fetchone())

    def catalog_version(self, version_no: int) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE version_no=?", (version_no,)).fetchone())

    def catalog_draft(self) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE status='draft' ORDER BY version_no DESC LIMIT 1").fetchone())

    def catalog_published(self) -> dict[str, Any] | None:
        return self.one(self.connection.execute("SELECT * FROM price_catalogs WHERE status='published'").fetchone())

    def catalogs(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM price_catalogs ORDER BY version_no DESC").fetchall()]

    def price_items(self, catalog_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM price_items WHERE catalog_id=? ORDER BY service_code,id", (catalog_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["applicability"] = json.loads(item.pop("applicability_json"))
            result.append(item)
        return result

    def price_item(self, catalog_id: int, item_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM price_items WHERE catalog_id=? AND id=?", (catalog_id, item_id)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["applicability"] = json.loads(item.pop("applicability_json"))
        return item

    def effective_catalog(self, on_or_after: str | None = None) -> dict[str, Any] | None:
        """当前可用于下单计价的目录：已发布且生效日不晚于给定日期（默认今天），取最近生效的一版。"""
        row = self.connection.execute(
            "SELECT * FROM price_catalogs WHERE status='published' AND effective_on IS NOT NULL AND effective_on<=? ORDER BY effective_on DESC,version_no DESC LIMIT 1",
            (on_or_after,),
        ).fetchone()
        return self.one(row)

    def next_catalog_version(self) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM price_catalogs").fetchone()
        return int(row[0])

    def replace_catalog_items(self, catalog_id: int, items: list[tuple]) -> None:
        self.connection.execute("DELETE FROM price_items WHERE catalog_id=?", (catalog_id,))
        self.connection.executemany(
            "INSERT INTO price_items(catalog_id,service_code,service_name,unit,unit_price_cents,applicability_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            items,
        )

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
