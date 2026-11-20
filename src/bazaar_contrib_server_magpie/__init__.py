"""Magpie gateways in Bazaar Server."""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from aiohttp import ClientSession
from bazaar_contrib_server import SERVER, PermissionItem, SidebarItem
from bazaar_contrib_server_database import SERVER_DB, SERVER_SCHEMA
from bazaar_contrib_server_overview import OVERVIEW, OverviewItem
from bazaar_core import Context, Contrib

from .api import Magpie
from .gateways import MODULE, Gateways
from .pages import MANAGE, VIEW, Views

__all__ = ["magpie"]
ICON = '<img src="/static/magpie/icons/magpie.svg" width="18" height="18" alt="">'


@asynccontextmanager
async def lifespan(ctx: Context):
    server = ctx.accept(SERVER)
    resources = Path(str(files(__name__))) / "resources"
    async with ctx.accept(SERVER_SCHEMA).register(MODULE), ClientSession() as session:
        pages = await server.pages("magpie", __name__)
        views = Views(pages, Gateways(ctx.accept(SERVER_DB)), Magpie(session))
        async with (
            server.module(
                "magpie",
                ui=pages.application(views.routes),
                locales=resources / "locales",
                static=resources / "static",
            ),
            server.permission(
                PermissionItem(point=VIEW, description="magpie.permission.view")
            ),
            server.permission(
                PermissionItem(point=MANAGE, description="magpie.permission.manage")
            ),
            server.sidebar(
                SidebarItem(
                    text="magpie.title",
                    icon=ICON,
                    path="/magpie",
                    group="magpie.sidebar",
                    permission=VIEW,
                    order=70,
                )
            ),
            ctx.accept(OVERVIEW).register(
                OverviewItem(
                    key="magpie",
                    title="magpie.title",
                    permission=VIEW,
                    src="/ui/magpie/overview",
                    order=60,
                )
            ),
        ):
            yield


magpie = Contrib(
    accepts=[SERVER, SERVER_DB, SERVER_SCHEMA, OVERVIEW], lifespan=lifespan
)
