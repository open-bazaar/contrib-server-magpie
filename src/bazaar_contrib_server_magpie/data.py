"""Projections of Magpie's route feed and usage ledger."""

from collections import Counter, defaultdict
from datetime import datetime

from .api import Data


def stamp(value: str | None) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000) if value else 0


def route_snapshot(snapshot: Data, concurrency: Data) -> Data:
    active = concurrency.get("active") or []
    by_route = {row["route"]: row for row in active if row.get("route")}
    routes = [
        {
            **route,
            "queue": current.get("phase"),
            "elapsed": current.get("ms"),
        }
        for route in snapshot.get("routes") or []
        for current in (by_route.get(int(route["id"], 36), {}),)
    ]
    represented = {int(route["id"], 36) for route in routes}
    unrouted = [
        {
            key: row[key]
            for key in ("agent", "provider", "model", "phase", "started", "ms")
            if key in row
        }
        for row in active
        if not row.get("route") or row["route"] not in represented
    ]
    counts = {key: concurrency.get(key) for key in ("requests", "waiting", "tools")}
    return {**snapshot, "routes": routes, "unrouted": unrouted, "concurrency": counts}


def source_name(source: str, label: str = "") -> str:
    return "Codex" if source == "bazaar_contrib_codex" else label or source


def totals_tokens(row: Data) -> int | None:
    if not row:
        return None
    return sum(
        row.get(field, 0) for field in ("input", "cache_read", "cache_write", "output")
    )


def failure_type(row: Data) -> str:
    status, kind = row.get("status"), row.get("err_type", "")
    if status == 429 or kind in ("rate_limit_error", "usage_limit_reached"):
        return "rate"
    if status in (408, 504) or "timeout" in kind:
        return "timeout"
    if status in (401, 403):
        return "auth"
    if status == 503:
        return "service"
    return "other"


def route_statistics(ledger: Data, failed: Data) -> list[Data]:
    reasons: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in failed.get("rows") or []:
        if not row.get("rejected") and outcome(row) in ("failed", "handed_on"):
            reasons[row["provider"], row["model"]][failure_type(row)] += 1
    stats = []
    for item in ledger.get("provider_speeds") or []:
        errors = item.get("failed", 0)
        kinds = reasons[item["provider"], item["model"]]
        stats.append(
            {
                **item,
                "failed": errors,
                "reasons": kinds if kinds.total() == errors else {},
            }
        )
    return sorted(stats, key=lambda item: (-item["failed"], -item["tries"]))


def outcome(row: Data) -> str:
    if row.get("handed_on"):
        return "handed_on"
    if row.get("canceled"):
        return "canceled"
    if row.get("rejected") or row.get("status", 0) >= 400 or row.get("err"):
        return "failed"
    if row.get("status", 0) == 0:
        return "unknown"
    return "retried" if row.get("tries", 0) > 1 else "success"


def listed_requests(rows: list[Data]) -> list[tuple[int, Data]]:
    seen: dict[int, int] = {}
    result: list[tuple[int, Data]] = []
    for index, row in enumerate(rows):
        route = row.get("route_id", 0)
        if route and route in seen:
            earlier = seen[route]
            if result[earlier][1].get("handed_on") and not row.get("handed_on"):
                result[earlier] = (index, row)
            continue
        if route:
            seen[route] = len(result)
        result.append((index, row))
    return result


def attempts(rows: list[Data]) -> list[Data]:
    return sorted(rows, key=lambda row: (stamp(row["t"]), not row.get("handed_on")))


def final_attempt(rows: list[Data]) -> Data | None:
    return next((row for row in reversed(rows) if not row.get("handed_on")), None)


def usage_bars(summary: Data) -> list[Data]:
    points = summary.get("series") or []
    peak = max((point["calls"] for point in points), default=0)
    return [
        {
            "label": point["label"],
            "time": point["time"],
            "calls": point["calls"],
            "height": round(point["calls"] * 100 / peak) if peak else 0,
        }
        for point in points
    ]
