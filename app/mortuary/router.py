from fastapi import APIRouter, Query

from app.mortuary.schemas import (
    BurialRightCreate,
    BurialRightRenew,
    CaseCreate,
    CatalogCreate,
    CatalogPublish,
    CatalogUpdate,
    CustodyAccept,
    CustodyTransferCreate,
    InvoiceCreate,
    OrderAdjustmentCreate,
    PaymentCreate,
    QuoteRequest,
    ReservationCreate,
    ResourceCreate,
    ServiceOrderCreate,
)
from app.mortuary.service import MortuaryService

router = APIRouter(prefix="/api/mortuary", tags=["mortuary"])

@router.post("/cases", status_code=201)
def create_case(payload: CaseCreate, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().create_case(payload.model_dump(), actor)

@router.get("/cases")
def list_cases(status: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return MortuaryService().list_cases(status, limit)

@router.get("/cases/{case_id}")
def get_case(case_id: int) -> dict:
    return MortuaryService().get_case(case_id)

@router.post("/cases/{case_id}/custody-transfers", status_code=201)
def request_transfer(case_id: int, payload: CustodyTransferCreate) -> dict:
    return MortuaryService().request_transfer(case_id, payload.model_dump())

@router.post("/custody-transfers/{transfer_id}/accept")
def accept_transfer(transfer_id: int, payload: CustodyAccept) -> dict:
    return MortuaryService().accept_transfer(transfer_id, payload.model_dump())

@router.post("/resources", status_code=201)
def create_resource(payload: ResourceCreate, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().create_resource(payload.model_dump(mode="json"), actor)

@router.get("/resources")
def resources(kind: str | None = None) -> list[dict]:
    return MortuaryService().list_resources(kind)

@router.post("/reservations", status_code=201)
def reserve(payload: ReservationCreate) -> dict:
    return MortuaryService().reserve(payload.model_dump())

@router.post("/reservations/{reservation_id}/cancel")
def cancel(reservation_id: int, actor: str = Query(min_length=2), reason: str = Query(min_length=2, max_length=500)) -> dict:
    return MortuaryService().cancel_reservation(reservation_id, actor, reason)

@router.post("/service-orders", status_code=201)
def order(payload: ServiceOrderCreate) -> dict:
    return MortuaryService().add_order(payload.model_dump())

@router.post("/service-orders/quote")
def quote_order(payload: QuoteRequest) -> dict:
    return MortuaryService().quote_order(payload.model_dump())

@router.get("/service-orders/{order_id}")
def get_order(order_id: int) -> dict:
    return MortuaryService().get_order(order_id)

@router.post("/service-orders/{order_id}/confirm")
def confirm_order(order_id: int, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().confirm_order(order_id, actor)

@router.post("/service-orders/{order_id}/adjustments")
def adjust_order(order_id: int, payload: OrderAdjustmentCreate) -> dict:
    return MortuaryService().create_adjustment(order_id, payload.model_dump())

@router.post("/price-catalogs", status_code=201)
def create_catalog(payload: CatalogCreate) -> dict:
    return MortuaryService().create_catalog(payload.model_dump())

@router.get("/price-catalogs")
def list_catalogs() -> list[dict]:
    return MortuaryService().list_catalogs()

@router.get("/price-catalogs/effective")
def effective_catalog() -> dict:
    return MortuaryService().effective_catalog()

@router.get("/price-catalogs/{catalog_id}")
def get_catalog(catalog_id: int) -> dict:
    return MortuaryService().get_catalog(catalog_id)

@router.patch("/price-catalogs/{catalog_id}")
def update_catalog(catalog_id: int, payload: CatalogUpdate, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().update_catalog(catalog_id, payload.model_dump(exclude_unset=True), actor)

@router.post("/price-catalogs/{catalog_id}/publish")
def publish_catalog(catalog_id: int, payload: CatalogPublish) -> dict:
    return MortuaryService().publish_catalog(catalog_id, payload.model_dump())

@router.post("/price-catalogs/{catalog_id}/retire")
def retire_catalog(catalog_id: int, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().retire_catalog(catalog_id, actor)

@router.post("/price-catalogs/{catalog_id}/items/{item_id}/discontinue")
def discontinue_item(catalog_id: int, item_id: int, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().discontinue_item(catalog_id, item_id, actor)

@router.get("/price-catalogs/diff/{from_version}/{to_version}")
def diff_catalogs(from_version: int, to_version: int) -> dict:
    return MortuaryService().diff_catalogs(from_version, to_version)

@router.post("/burial-rights", status_code=201)
def create_right(payload: BurialRightCreate, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().create_right(payload.model_dump(), actor)

@router.post("/burial-rights/{right_id}/renew")
def renew_right(right_id: int, payload: BurialRightRenew) -> dict:
    return MortuaryService().renew_right(right_id, payload.model_dump())

@router.post("/invoices", status_code=201)
def create_invoice(payload: InvoiceCreate) -> dict:
    return MortuaryService().create_invoice(payload.model_dump())

@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int) -> dict:
    return MortuaryService().get_invoice(invoice_id)

@router.post("/invoices/{invoice_id}/payments")
def pay(invoice_id: int, payload: PaymentCreate) -> dict:
    return MortuaryService().pay(invoice_id, payload.model_dump())
