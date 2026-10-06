from fastapi import APIRouter, Query

from app.mortuary.schemas import (
    BurialRightCreate,
    BurialRightRenew,
    CaseCreate,
    CustodyAccept,
    CustodyTransferCreate,
    InvoiceCreate,
    PaymentCreate,
    PriceCatalogCreate,
    PriceCatalogDiffRequest,
    PriceCatalogPublish,
    PriceCatalogUpdate,
    ReservationCreate,
    ResourceCreate,
    ServiceOrderAdjustmentCreate,
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

# ------------------------------------------------------------------
# 价格目录版本
# ------------------------------------------------------------------
@router.post("/price-catalogs", status_code=201)
def create_price_catalog(payload: PriceCatalogCreate) -> dict:
    return MortuaryService().create_catalog(payload.model_dump())

@router.get("/price-catalogs")
def list_price_catalogs(status: str | None = Query(default=None, pattern=r"^(draft|published)$")) -> list[dict]:
    return MortuaryService().list_catalogs(status)

@router.get("/price-catalogs/current")
def current_price_catalog() -> dict:
    return MortuaryService().current_pricing()

@router.post("/price-catalogs/diff")
def diff_price_catalogs(payload: PriceCatalogDiffRequest) -> dict:
    data = payload.model_dump()
    return MortuaryService().diff_catalogs(data["base_catalog_id"], data["target_catalog_id"])

@router.get("/price-catalogs/{catalog_id}")
def get_price_catalog(catalog_id: int) -> dict:
    return MortuaryService().get_catalog(catalog_id)

@router.patch("/price-catalogs/{catalog_id}")
def update_price_catalog(catalog_id: int, payload: PriceCatalogUpdate) -> dict:
    return MortuaryService().update_catalog(catalog_id, payload.model_dump(exclude_unset=True))

@router.post("/price-catalogs/{catalog_id}/publish")
def publish_price_catalog(catalog_id: int, payload: PriceCatalogPublish) -> dict:
    return MortuaryService().publish_catalog(catalog_id, payload.model_dump())

# ------------------------------------------------------------------
# 服务订单
# ------------------------------------------------------------------
@router.post("/service-orders", status_code=201)
def order(payload: ServiceOrderCreate) -> dict:
    return MortuaryService().add_order(payload.model_dump())

@router.get("/service-orders/{order_id}")
def get_order(order_id: int) -> dict:
    return MortuaryService().get_order(order_id)

@router.post("/service-orders/{order_id}/confirm")
def confirm_order(order_id: int, actor: str = Query(min_length=2, max_length=80)) -> dict:
    return MortuaryService().confirm_order(order_id, actor)

@router.post("/service-orders/{order_id}/adjustments", status_code=201)
def adjust_order(order_id: int, payload: ServiceOrderAdjustmentCreate) -> dict:
    return MortuaryService().create_order_adjustment(order_id, payload.model_dump())

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
