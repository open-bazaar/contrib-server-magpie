"""An isolated, installed Server reached over its normal HTTP interface."""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from bazaar_contrib_http import HTTP
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load

PLUGINS = (
    ("timer-wheel", {}),
    ("http", {"listen": "127.0.0.1:0"}),
    ("server-database", {}),
    ("server-migrations", {}),
    ("server", {}),
    ("server-theme-night", {}),
    ("server-theme-day", {}),
    ("server-access", {}),
    ("server-overview", {}),
    ("server-magpie", {}),
)


@dataclass(frozen=True)
class Served:
    url: str
    password: str = field(repr=False)

    async def sign_in(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/login/", data={"name": "admin", "password": self.password}
        )
        assert response.status_code == 204


@pytest.fixture
async def served(tmp_path, monkeypatch):
    monkeypatch.setenv("BCN_HOME", str(tmp_path))
    async with serving() as server:
        yield server


@asynccontextmanager
async def serving(plugins=PLUGINS):
    urls: list[str] = []
    passwords: list[str] = []

    class Startup(logging.Handler):
        def emit(self, record):
            if match := re.search(
                r"created account admin with password (\S+);", record.getMessage()
            ):
                passwords.append(match.group(1))

    @asynccontextmanager
    async def address(ctx: Context):
        urls.append(ctx.accept(HTTP).url)
        yield

    config = await asyncio.to_thread(
        load, [ContribSpec(contrib=name, config=options) for name, options in plugins]
    )
    config = config.model_copy(
        update={
            "entries": (
                *config.entries,
                ContribEntry(
                    name="test-address",
                    definition=Contrib(accepts=[HTTP], lifespan=address),
                ),
            ),
            "isolate_errors": False,
        }
    )

    handler = Startup()
    logger = logging.getLogger("bazaar_contrib_server_access")
    propagate = logger.propagate
    logger.propagate = False
    logger.addHandler(handler)
    try:
        async with ContribHub() as hub, hub.enable(config):
            yield Served(urls[0], passwords[0])
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate
