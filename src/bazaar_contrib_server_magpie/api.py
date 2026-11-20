"""Calls made with the selected gateway's one Key, on the Server only."""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .gateways import Connection

PREFIX = "/v1/magpie/"
SNAPSHOT = "route-events/snapshot"
EVENTS = "route-events"
USAGE = "usage"
REQUESTS = "usage/requests"
CONCURRENCY = "concurrency"
QUOTAS = "quotas"

type Data = dict[str, Any]


class Rejected(Exception):
    def __init__(self, status: int, code: str = "") -> None:
        super().__init__(status, code)
        self.status = status
        self.code = code


class Magpie:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    @asynccontextmanager
    async def request(
        self,
        connection: Connection,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        body: Data | None = None,
        cursor: str = "",
    ) -> AsyncIterator[ClientResponse]:
        headers = {"Authorization": f"Bearer {connection.key}"}
        if cursor:
            headers["Last-Event-ID"] = cursor
        try:
            async with self.session.request(
                "GET" if body is None else "POST",
                connection.gateway.endpoint
                + (path if path.startswith("/") else PREFIX + path),
                headers=headers,
                params=params,
                json=body,
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise Rejected(response.status)
                yield response
        except (ClientError, ValueError, TimeoutError) as error:
            raise Rejected(502) from error

    async def read(
        self, connection: Connection, path: str, **params: str | int
    ) -> Data:
        async with self.request(connection, path, params=params or None) as response:
            return await response.json()

    async def write(self, connection: Connection, path: str, body: Data) -> Data:
        async with self.request(connection, "admin/" + path, body=body) as response:
            return await response.json()

    async def route_changes(
        self, connection: Connection, after: str, through: str
    ) -> list[Data] | None:
        changes = []
        async with self.request(
            connection, EVENTS, params={"after": after}
        ) as response:
            async for line in response.content:
                if not line.startswith(b"data: "):
                    continue
                event = json.loads(line.removeprefix(b"data: "))
                if "route" not in event:
                    return None
                changes.append(event["route"])
                if event["cursor"] == through:
                    return changes
        return None
