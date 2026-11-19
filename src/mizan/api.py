"""FastAPI application for Mizan portfolio management and order drafting."""

import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import mizan.db
from mizan.db import Connection
from mizan.engine.gap import current_weights
from mizan.engine.planner import Mode, Side
from mizan.engine.types import AssetClass
from mizan.services.drafts import (
    AccountNotFound,
    DraftExpired,
    DraftNotFound,
    DraftNotOpen,
    EmptyPlan,
    IdempotencyConflict,
    InvalidToken,
    OrderSetTampered,
    cancel_draft,
    confirm_draft,
    load_portfolio,
    prepare_draft,
)


class HoldingOut(BaseModel):
    symbol: str
    units: int
    price: str
    value: str


class WeightsOut(BaseModel):
    equity: str
    fixed_income: str
    gold: str


class PortfolioResponse(BaseModel):
    cash: str
    holdings_value: str
    total_value: str
    holdings: list[HoldingOut]
    weights: WeightsOut


class CreateDraftRequest(BaseModel):
    mode: Mode


class DraftOrderOut(BaseModel):
    symbol: str
    side: Side
    units: int
    price: str


class DraftResponse(BaseModel):
    draft_id: uuid.UUID
    token: str
    expires_at: datetime
    order_set_hash: str
    cash_after: str
    orders: list[DraftOrderOut]


class ConfirmDraftRequest(BaseModel):
    token: str


class ConfirmDraftResponse(BaseModel):
    order_id: uuid.UUID
    draft_id: uuid.UUID
    replayed: bool


def get_current_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> uuid.UUID:
    if x_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        ) from exc


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="idempotency_key_required",
        )
    return idempotency_key


def create_app(database_url: str) -> FastAPI:
    app = FastAPI()

    def get_db() -> Generator[Connection, None, None]:
        conn = mizan.db.connect(database_url)
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(AccountNotFound)
    async def account_not_found_handler(request: Request, exc: AccountNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "account_not_found"})

    @app.exception_handler(DraftNotFound)
    async def draft_not_found_handler(request: Request, exc: DraftNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "draft_not_found"})

    @app.exception_handler(DraftNotOpen)
    async def draft_not_open_handler(request: Request, exc: DraftNotOpen) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "draft_not_open"})

    @app.exception_handler(DraftExpired)
    async def draft_expired_handler(request: Request, exc: DraftExpired) -> JSONResponse:
        return JSONResponse(status_code=410, content={"detail": "draft_expired"})

    @app.exception_handler(InvalidToken)
    async def invalid_token_handler(request: Request, exc: InvalidToken) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "invalid_token"})

    @app.exception_handler(OrderSetTampered)
    async def order_set_tampered_handler(request: Request, exc: OrderSetTampered) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "order_set_tampered"})

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict_handler(
        request: Request, exc: IdempotencyConflict
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "idempotency_conflict"})

    @app.exception_handler(EmptyPlan)
    async def empty_plan_handler(request: Request, exc: EmptyPlan) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "empty_plan"})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/accounts/{account_id}/portfolio",
        response_model=PortfolioResponse,
    )
    def get_portfolio(
        account_id: uuid.UUID,
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        conn: Annotated[Connection, Depends(get_db)],
    ) -> PortfolioResponse:
        portfolio = load_portfolio(conn, user_id, account_id)
        weights = current_weights(portfolio)

        sorted_symbols = sorted(portfolio.holdings.keys())
        holdings_list: list[HoldingOut] = []
        for symbol in sorted_symbols:
            units = portfolio.holdings[symbol]
            inst = portfolio.instruments[symbol]
            val = inst.price * units
            holdings_list.append(
                HoldingOut(
                    symbol=symbol,
                    units=units,
                    price=str(inst.price),
                    value=str(val),
                )
            )

        return PortfolioResponse(
            cash=str(portfolio.cash),
            holdings_value=str(portfolio.holdings_value()),
            total_value=str(portfolio.total_value()),
            holdings=holdings_list,
            weights=WeightsOut(
                equity=str(weights[AssetClass.EQUITY]),
                fixed_income=str(weights[AssetClass.FIXED_INCOME]),
                gold=str(weights[AssetClass.GOLD]),
            ),
        )

    @app.post(
        "/accounts/{account_id}/drafts",
        status_code=status.HTTP_201_CREATED,
        response_model=DraftResponse,
    )
    def post_draft(
        account_id: uuid.UUID,
        body: CreateDraftRequest,
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        conn: Annotated[Connection, Depends(get_db)],
    ) -> DraftResponse:
        draft = prepare_draft(conn, user_id, account_id, body.mode)
        return DraftResponse(
            draft_id=draft.draft_id,
            token=draft.token,
            expires_at=draft.expires_at,
            order_set_hash=draft.order_set_hash,
            cash_after=str(draft.cash_after),
            orders=[
                DraftOrderOut(
                    symbol=o.symbol,
                    side=o.side,
                    units=o.units,
                    price=str(o.price),
                )
                for o in draft.orders
            ],
        )

    @app.post(
        "/accounts/{account_id}/drafts/{draft_id}/confirm",
        response_model=ConfirmDraftResponse,
    )
    def post_confirm_draft(
        account_id: uuid.UUID,
        draft_id: uuid.UUID,
        body: ConfirmDraftRequest,
        idempotency_key: Annotated[str, Depends(get_idempotency_key)],
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        conn: Annotated[Connection, Depends(get_db)],
    ) -> ConfirmDraftResponse:
        res = confirm_draft(
            conn,
            user_id,
            account_id,
            draft_id,
            body.token,
            idempotency_key,
        )
        return ConfirmDraftResponse(
            order_id=res.order_id,
            draft_id=res.draft_id,
            replayed=res.replayed,
        )

    @app.post(
        "/accounts/{account_id}/drafts/{draft_id}/cancel",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
    )
    def post_cancel_draft(
        account_id: uuid.UUID,
        draft_id: uuid.UUID,
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        conn: Annotated[Connection, Depends(get_db)],
    ) -> Response:
        cancel_draft(conn, user_id, account_id, draft_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
