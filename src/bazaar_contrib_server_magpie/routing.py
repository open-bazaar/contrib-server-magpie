"""Attempt and request outcomes from Magpie's remote route trace."""

import asyncio
from collections import defaultdict, deque

from .admin import Read
from .api import CONCURRENCY, SNAPSHOT, Data, Magpie, Rejected
from .data import attempts, listed_requests, outcome, route_snapshot
from .gateways import Connection

RECENT = 5


class RouteFeed:
    def __init__(self, magpie: Magpie, connection: Connection) -> None:
        self.magpie = magpie
        self.connection = connection
        self.lock = asyncio.Lock()
        self.cursor = ""
        self.failed: dict[str, dict[int, Data]] = defaultdict(dict)
        self.finished: deque[Data] = deque(maxlen=RECENT)

    def project(self, route: Data) -> Data:
        return {
            **route,
            "route_id": int(route["id"], 36),
            "failed_attempts": [
                item
                for number, item in self.failed.get(route["id"], {}).items()
                if number < route["attempt"]
            ],
        }

    def record(self, route: Data) -> None:
        phase = route["phase"]
        if phase == "attempt_done" and route.get("failed"):
            self.failed[route["id"]][route["attempt"]] = route
        if phase in ("done", "dropped"):
            status = route.get("status", 0)
            result = phase
            if phase == "done":
                if route.get("failed"):
                    result = "failed"
                elif status == 499:
                    result = "canceled"
                elif 200 <= status < 300:
                    result = "retried" if route["attempt"] > 1 else "success"
            self.finished.appendleft({**self.project(route), "result": result})
            self.failed.pop(route["id"], None)

    async def snapshot(self, read: Read) -> Data:
        async with self.lock:
            snapshot, concurrency = await asyncio.gather(
                read(SNAPSHOT), read(CONCURRENCY)
            )
            cursor = snapshot.get("cursor", "")
            error = None
            changes: list[Data] | None = []
            if self.cursor and cursor and cursor != self.cursor:
                try:
                    changes = await self.magpie.route_changes(
                        self.connection, self.cursor, cursor
                    )
                except Rejected as rejected:
                    changes = None
                    if rejected.status != 409:
                        error = rejected
            if not cursor or changes is None:
                self.failed.clear()
                self.finished.clear()
            else:
                for route in changes:
                    self.record(route)
            self.cursor = cursor

            live = route_snapshot(snapshot, concurrency)
            routes = [self.project(route) for route in live["routes"]]
            for route in live["routes"]:
                self.record(route)
            active = {route["id"] for route in routes}
            for route_id in self.failed.keys() - active:
                del self.failed[route_id]
            return {
                **live,
                "routes": routes,
                "finished": list(self.finished),
                "feed_error": error,
            }


def with_ledger(live: Data, ledger: Data) -> Data:
    rows = ledger.get("rows") or []
    handed_on: dict[int, list[Data]] = defaultdict(list)
    for row in attempts(rows):
        if row.get("route_id") and row.get("handed_on"):
            handed_on[row["route_id"]].append(row)

    def enrich(route: Data) -> Data:
        route_id = route["route_id"]
        earlier = route.get("failed_attempts") or handed_on.get(route_id, [])
        latest = earlier[-1] if earlier else {}
        changed = bool(latest.get("provider") and route.get("provider")) and (
            latest["provider"],
            latest.get("served") or latest.get("model"),
        ) != (route["provider"], route.get("model"))
        return {**route, "failed_attempts": earlier, "changed_destination": changed}

    complete = {
        row["route_id"]: row
        for _, row in listed_requests(rows)
        if row.get("route_id") and not row.get("handed_on")
    }
    routes = [enrich(route) for route in live["routes"]]
    used = {route["route_id"] for route in routes}
    recent = []
    for route in live["finished"]:
        route_id = route["route_id"]
        if route_id in used:
            continue
        used.add(route_id)
        recorded = complete.get(route_id)
        recent.append(
            enrich(
                {
                    **route,
                    "recorded": recorded is not None,
                    "result": outcome(recorded) if recorded else route["result"],
                }
            )
        )
    # The ledger also covers requests finished before this view started following.
    for route_id, row in complete.items():
        if route_id in used:
            continue
        recent.append(
            enrich(
                {
                    "route_id": route_id,
                    "agent": row.get("agent", ""),
                    "provider": row.get("provider", ""),
                    "model": row.get("served") or row.get("model", ""),
                    "time": row["t"],
                    "attempt": row.get("tries", 1),
                    "status": row.get("status", 0),
                    "ms": row.get("ms"),
                    "result": outcome(row),
                    "recorded": True,
                }
            )
        )
    for route in routes:
        route["recorded"] = (
            route["route_id"] in handed_on or route["route_id"] in complete
        )
    return {**live, "routes": routes, "recent": recent[:RECENT]}
