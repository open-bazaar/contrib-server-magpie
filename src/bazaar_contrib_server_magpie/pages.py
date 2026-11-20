"""The Server module's pages and its same-origin route feed."""

import asyncio
import json
from collections.abc import Mapping
from functools import partial
from hashlib import sha256
from urllib.parse import urlencode

from bazaar_contrib_server import Account, Pages, account
from pydantic import AnyHttpUrl, ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from . import admin
from .api import (
    CONCURRENCY,
    EVENTS,
    QUOTAS,
    REQUESTS,
    SNAPSHOT,
    USAGE,
    Data,
    Magpie,
    Rejected,
)
from .data import (
    attempts,
    failure_type,
    final_attempt,
    listed_requests,
    outcome,
    route_snapshot,
    route_statistics,
    source_name,
    stamp,
    totals_tokens,
    usage_bars,
)
from .gateways import Connection, Gateway, Gateways

VIEW = "magpie.view"
MANAGE = "magpie.manage"
LIVE = "magpie.routing"
HOME = "/ui/magpie/"
TABS = ("routing", "requests", "usage", "manage")
PERIODS = ("today", "7d", "30d", "all")
SOURCES = ("through", "direct", "reported")
PER_PAGE = 40


def link(tab: str = "routing", gateway: str = "", **filters: str | int | None) -> str:
    values = {"view": tab, "gateway": gateway, **filters}
    return (
        HOME
        + "?"
        + urlencode(
            {key: value for key, value in values.items() if value not in (None, "")}
        )
    )


def require(request: Request, *points: str) -> None:
    if not all(account(request).allows(point) for point in points):
        raise HTTPException(403)


def redirect(request: Request, address: str) -> Response:
    if request.headers.get("HX-Request") == "true":
        return Response(
            status_code=204,
            headers={
                "HX-Location": json.dumps(
                    {"path": address, "target": "body", "swap": "innerMorph show:top"}
                )
            },
        )
    return RedirectResponse(address, 303)


def number(value: str) -> int:
    return (
        int(value) if value.isascii() and value.isdecimal() and len(value) <= 19 else 0
    )


def endpoint(value: str) -> str:
    url = AnyHttpUrl(value)
    if (
        url.username is not None
        or url.password is not None
        or url.query is not None
        or url.fragment is not None
    ):
        raise ValueError
    return str(url).rstrip("/")


def error_label(error: Rejected) -> str:
    return {401: "unauthorized", 403: "forbidden", 502: "unreachable"}.get(
        error.status, "upstream"
    )


async def live_routes(read: admin.Read) -> Data:
    snapshot, concurrency = await asyncio.gather(read(SNAPSHOT), read(CONCURRENCY))
    return route_snapshot(snapshot, concurrency)


def live_seen(live: Data, errors: dict[str, tuple[str, int]]) -> dict[str, str]:
    state = (
        live.get("cursor"),
        live["routes"],
        live["unrouted"],
        errors.get(SNAPSHOT),
        errors.get(CONCURRENCY),
    )
    return {"state": sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()}


def routing_catalog(ledger: Data) -> Data:
    return {
        "clients": {row["id"]: row for row in ledger.get("agents") or []},
        "providers": {row["id"]: row for row in ledger.get("providers") or []},
    }


class Views:
    def __init__(self, pages: Pages, gateways: Gateways, magpie: Magpie) -> None:
        self.pages = pages
        self.gateways = gateways
        self.magpie = magpie
        self.routes = [
            Route("/", self.index),
            Route("/gateways", self.change_gateway, methods=["POST"]),
            Route("/manage", self.change_config, methods=["POST"]),
            Route("/live", self.live),
            Route("/snapshot", self.snapshot),
            Route("/events", self.events),
            Route("/overview", self.overview),
        ]

    async def connection(self, request: Request) -> Connection:
        selected = await self.gateways.get(request.query_params.get("gateway", ""))
        if selected is None:
            raise HTTPException(404)
        return selected

    async def index(self, request: Request) -> Response:
        require(request, VIEW)
        return await self.page(request)

    async def read(
        self,
        connection: Connection,
        errors: dict[str, tuple[str, int]],
        path: str,
        **params: str | int,
    ) -> Data:
        try:
            return await self.magpie.read(connection, path, **params)
        except Rejected as error:
            errors[path] = (error_label(error), error.status)
            return {}

    async def page(
        self,
        request: Request,
        *,
        gateway_error: str = "",
        gateway_form: Data | None = None,
        status: int = 200,
    ) -> Response:
        query = request.query_params
        gateways = await self.gateways.list()
        chosen = query.get("gateway")
        selected = (
            next((item for item in gateways if item.id == chosen), None)
            if chosen
            else next(iter(gateways), None)
        )
        if chosen and selected is None:
            raise HTTPException(404)
        tab = query.get("view", "routing")
        if tab not in TABS:
            raise HTTPException(404)
        if tab == "manage":
            require(request, MANAGE)
        period = query.get("period", "today")
        period = period if period in PERIODS else "today"
        errors: dict[str, tuple[str, int]] = {}
        reached = False

        async def read(path: str, **params: str | int) -> Data:
            nonlocal reached
            assert connection is not None
            data = await self.read(connection, errors, path, **params)
            reached |= path not in errors
            return data

        values: Data = {}
        gateway_edit = query.get("gateway_edit", "")
        if gateway_edit:
            require(request, MANAGE)
            if gateway_edit not in ("add", "edit", "remove"):
                raise HTTPException(404)
        connection = await self.gateways.get(selected.id) if selected else None
        if connection and tab == "routing":
            live, ledger, today = await asyncio.gather(
                live_routes(read),
                read(REQUESTS, period="today", via="through", limit=1),
                read(USAGE, period="today"),
            )
            failed = {}
            if any(item.get("failed") for item in ledger.get("provider_speeds") or []):
                try:
                    failed = await self.magpie.read(
                        connection,
                        REQUESTS,
                        period="today",
                        via="through",
                        failed=1,
                        limit=500,
                    )
                except Rejected:
                    pass
            latest = next(
                (row for row in ledger.get("rows") or [] if not row.get("handed_on")),
                None,
            )
            values.update(
                live=live,
                seen=live_seen(live, errors),
                live_topic=LIVE,
                statistics=route_statistics(ledger, failed),
                today=today,
                latest=latest,
                **routing_catalog(ledger),
            )
        elif connection and tab == "requests":
            values.update(await self.requests(request, read, period))
        elif connection and tab == "usage":
            usage, quotas = await asyncio.gather(
                read(USAGE, period=period), read(QUOTAS, kept=1)
            )
            values.update(
                usage=usage,
                bars=usage_bars(usage),
                peak=max(
                    (point["calls"] for point in usage.get("series") or []), default=0
                ),
                quotas=quotas.get("data") or [],
            )
        elif connection and tab == "manage":
            section = query.get("section", admin.SECTIONS[0])
            if section not in admin.SECTIONS:
                raise HTTPException(404)
            values.update(await admin.load(section, read))
            if section == "models":
                values["model_provider"] = query.get("provider", "")

        def target(view: str = tab, **params: str | int | None) -> str:
            return link(view, selected.id if selected else "", **params)

        return await self.pages.render(
            request,
            "magpie/index.html",
            status_code=status,
            gateways=gateways,
            selected=selected,
            tab=tab,
            period=period,
            target=target,
            link=link,
            reached=reached,
            errors=errors,
            stamp=stamp,
            outcome=outcome,
            source_name=source_name,
            totals_tokens=totals_tokens,
            failure_type=failure_type,
            group_members=admin.group_members,
            gateway_edit=gateway_edit,
            gateway_error=gateway_error,
            gateway_form=gateway_form or {},
            unavailable=bool(selected and connection is None),
            **values,
        )

    async def requests(self, request: Request, read: admin.Read, period: str) -> Data:
        query = request.query_params
        offset = number(query.get("offset", ""))
        filters: dict[str, str] = {"period": period}
        filters.update(
            {key: query[key] for key in ("provider", "model", "q") if query.get(key)}
        )
        if query.get("via") in SOURCES:
            filters["via"] = query["via"]
        for flag in ("failed", "retried"):
            if query.get(flag) == "1":
                filters[flag] = "1"
        ledger = await read(REQUESTS, **filters, offset=offset, limit=PER_PAGE)
        rows = ledger.get("rows") or []
        shown = listed_requests(rows)
        route = number(query.get("route", ""))
        row_query = query.get("row", "")
        row_index = (
            number(row_query) if row_query.isascii() and row_query.isdecimal() else None
        )
        opened = bool(route or row_index is not None)
        if shown and not opened:
            index, first = shown[0]
            route = first.get("route_id", 0)
            if not route:
                row_index = index
        detail: list[Data] = []
        if route:
            response = await read(
                REQUESTS, period="all", route=route, via="through", limit=500
            )
            detail = attempts(response.get("rows") or [])
        elif (
            row_index is not None
            and row_index < len(rows)
            and not rows[row_index].get("route_id")
        ):
            detail = [rows[row_index]]
        return {
            "ledger": ledger,
            "rows": shown,
            "detail": detail,
            "trials": [
                row for row in detail if not row.get("rejected") and row.get("provider")
            ],
            "final": final_attempt(detail),
            "opened": opened,
            "providers": {row["id"]: row for row in ledger.get("providers") or []},
            "current_route": route,
            "current_row": row_index,
            "filters": filters,
            "offset": offset,
            "page_size": PER_PAGE,
        }

    async def change_gateway(self, request: Request) -> Response:
        require(request, VIEW, MANAGE)
        action = request.query_params.get("gateway_edit", "")
        if action not in ("add", "edit", "remove"):
            raise HTTPException(404)
        async with request.form() as form:
            name = str(form.get("name", "")).strip()
            address = str(form.get("endpoint", "")).strip()
            key = str(form.get("key", "")).strip()
        selected: Gateway | None = None
        if action != "add":
            selected = (await self.connection(request)).gateway
        if action == "remove" and selected:
            await self.gateways.remove(selected.id)
            return redirect(request, HOME)
        try:
            address = endpoint(address)
            if not name or (action == "add" and not key):
                raise ValueError
            if selected and not key and address != selected.endpoint:
                raise ValueError
        except ValidationError, ValueError:
            return await self.page(
                request,
                gateway_error="gateway_invalid",
                gateway_form={"name": name, "endpoint": address},
                status=400,
            )
        if selected:
            await self.gateways.edit(selected.id, name, address, key)
        else:
            selected = await self.gateways.add(name, address, key)
        return redirect(request, link(gateway=selected.id))

    async def change_config(self, request: Request) -> Response:
        require(request, VIEW, MANAGE)
        connection = await self.connection(request)
        section = request.query_params.get("section", "")
        back = link("manage", connection.gateway.id, section=section)
        probes = None
        async with request.form() as form:
            operation = str(form.get("operation", ""))
            try:
                path, body = admin.command(section, operation, form)
                if section == "routing-groups" and operation != "create":
                    groups = await self.magpie.read(connection, admin.PATHS[section])
                    group = next(
                        (
                            item
                            for item in groups.get("groups") or []
                            if item["id"] == body.get("from", body["id"])
                        ),
                        None,
                    )
                    if group is None or group.get("auto"):
                        raise ValueError
                if section == "models":
                    back = link(
                        "manage",
                        connection.gateway.id,
                        section=section,
                        provider=body["id"],
                    )
                if operation in ("add-model", "test-models"):
                    state = await self.magpie.read(connection, admin.PATHS["models"])
                    provider = next(
                        (
                            item
                            for item in state.get("providers") or ()
                            if item["id"] == body["id"]
                        ),
                        None,
                    )
                    if provider is None:
                        raise ValueError
                    if operation == "add-model":
                        body = admin.add_models(provider, body["models"])
                        if not body:
                            return redirect(request, back)
                    else:
                        models = admin.test_models(
                            admin.provider_models(provider), body.pop("model")
                        )
                        if not models:
                            raise ValueError
                        body["test"] = models
                result = await self.magpie.write(connection, path, body)
                if operation == "test-models":
                    probes = admin.model_results(result, body["test"])
            except Rejected as error:
                label, status, upstream = (
                    error_label(error),
                    403 if error.status == 403 else 400,
                    error.status,
                )
                return await self.pages.render(
                    request,
                    "magpie/operation.html",
                    status_code=status,
                    error=label,
                    upstream=upstream,
                    back=back,
                    secret="",
                )
            except ValueError:
                return await self.pages.render(
                    request,
                    "magpie/operation.html",
                    status_code=400,
                    error="invalid",
                    upstream=0,
                    back=back,
                    secret="",
                )
        secret = (
            result.get("secret", "") if operation in ("add-key", "rotate-key") else ""
        )
        if secret or probes is not None:
            return await self.pages.render(
                request,
                "magpie/operation.html",
                error="",
                upstream=0,
                back=back,
                secret=secret,
                probes=probes,
            )
        return redirect(request, back)

    async def live_state(
        self, _: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        connection = await self.gateways.get(scope["gateway"])
        if connection is None:
            return None
        errors: dict[str, tuple[str, int]] = {}
        live = await live_routes(partial(self.read, connection, errors))
        return live_seen(live, errors)

    async def live(self, request: Request) -> Response:
        require(request, VIEW)
        connection = await self.connection(request)
        errors: dict[str, tuple[str, int]] = {}
        read = partial(self.read, connection, errors)
        live, ledger = await asyncio.gather(
            live_routes(read),
            read(REQUESTS, period="today", via="through", limit=1),
        )
        return await self.pages.render(
            request,
            "magpie/live.html",
            selected=connection.gateway,
            live=live,
            seen=live_seen(live, errors),
            live_topic=LIVE,
            errors=errors,
            stamp=stamp,
            source_name=source_name,
            **routing_catalog(ledger),
        )

    async def snapshot(self, request: Request) -> Response:
        require(request, VIEW)
        connection = await self.connection(request)
        try:
            snapshot, concurrency = await asyncio.gather(
                self.magpie.read(connection, SNAPSHOT),
                self.magpie.read(connection, CONCURRENCY),
            )
        except Rejected as error:
            return JSONResponse({"error": error_label(error)}, status_code=error.status)
        return JSONResponse(
            route_snapshot(snapshot, concurrency), headers={"Cache-Control": "no-store"}
        )

    async def events(self, request: Request) -> Response:
        require(request, VIEW)
        connection = await self.connection(request)
        after = request.query_params.get("after", "")
        cursor = request.headers.get("Last-Event-ID", "")

        async def stream():
            try:
                async with self.magpie.request(
                    connection, EVENTS, params={"after": after}, cursor=cursor
                ) as upstream:
                    async for chunk in upstream.content.iter_any():
                        yield chunk
            except Rejected as error:
                if error.status == 409:
                    yield "event: reset\ndata: {}\n\n"
                elif error.status != 502:
                    yield f"event: unavailable\ndata: {json.dumps(error_label(error))}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def overview(self, request: Request) -> Response:
        require(request, VIEW)
        gateways = await self.gateways.list()

        async def summarize(selected: Gateway) -> Data:
            connection = await self.gateways.get(selected.id)
            if connection is None:
                return {"gateway": selected, "error": ("unreachable", 502)}
            try:
                usage, concurrency, ledger = await asyncio.gather(
                    self.magpie.read(connection, USAGE, period="today"),
                    self.magpie.read(connection, CONCURRENCY),
                    self.magpie.read(
                        connection, REQUESTS, period="today", via="through", limit=1
                    ),
                )
            except Rejected as error:
                return {
                    "gateway": selected,
                    "error": (error_label(error), error.status),
                }
            failed_attempts = sum(
                speed.get("failed", 0) for speed in ledger.get("provider_speeds") or []
            )
            return {
                "gateway": selected,
                "usage": usage,
                "active": concurrency.get("requests"),
                "failed_attempts": failed_attempts,
                "error": None,
            }

        cards = await asyncio.gather(*(summarize(item) for item in gateways))
        return await self.pages.render(
            request, "magpie/overview.html", cards=cards, link=link
        )
