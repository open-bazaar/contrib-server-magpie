import json
import os
import socket
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from aiohttp import web
from playwright.async_api import async_playwright, expect

CURSOR = "feed:4"
STAMP = "2026-11-19T14:10:03Z"
NOW = {
    "calls": 3,
    "errors": 1,
    "input": 432,
    "output": 95,
    "cost": 0.003,
    "series": [{"label": "22:00", "time": STAMP, "calls": 3}],
    "agents": [{"id": "codex", "calls": 3}],
    "models": [{"id": "relay/gpt", "model": "gpt", "calls": 3}],
}
SPEED = {
    "provider": "relay",
    "provider_name": "Relay",
    "model": "gpt",
    "tries": 4,
    "failed": 1,
}
ROWS = [
    {
        "route_id": 36,
        "t": STAMP,
        "agent": "codex",
        "agentName": "Codex",
        "provider": "backup",
        "providerName": "Backup",
        "model": "gpt",
        "ms": 600,
        "ttft_ms": 180,
        "status": 200,
        "tries": 2,
        "in": 100,
        "out": 20,
        "priced": True,
        "cost": 0.002,
        "up_bytes": 924,
    },
    {
        "route_id": 36,
        "t": STAMP,
        "agent": "codex",
        "agentName": "Codex",
        "provider": "relay",
        "providerName": "Relay",
        "model": "gpt",
        "ms": 160,
        "status": 429,
        "handed_on": True,
        "err": "body from upstream must not be shown",
        "priced": False,
    },
]
PROVIDER = {
    "id": "relay",
    "name": "Relay",
    "chat": "https://relay.example/v1",
    "fallback": ["backup/gpt"],
    "headers": {"X-Tenant": "team"},
    "modelsURL": "https://relay.example/catalog",
    "key": {"set": True, "masked": "masked-provider"},
    "chosen": ["gpt", "unreachable"],
    "keyList": [
        {"id": "ref1", "name": "Existing", "masked": "masked-provider", "on": True}
    ],
}


@pytest.fixture
async def upstream():
    state = {
        "allow_write": False,
        "models": [
            {"id": "gpt", "name": "GPT", "on": True},
            {"id": "unreachable", "name": "Does not answer", "on": True},
            {"id": "listed", "name": "Listed", "on": False},
        ],
        "groups": [
            {
                "id": "auto-gpt",
                "name": "Found GPT",
                "auto": True,
                "members": ["relay/gpt"],
            },
            {"id": "team", "name": "Team route", "members": ["relay/gpt"]},
        ],
    }

    async def magpie(request):
        if request.headers.get("Authorization") != "Bearer gateway-test-credential":
            return web.json_response({"error": "invalid key"}, status=401)
        path = request.match_info["path"]
        if path == "route-events/snapshot":
            return web.json_response(
                {
                    "cursor": CURSOR,
                    "active": 1,
                    "routes": [
                        {
                            "id": "10",
                            "time": STAMP,
                            "agent": "codex",
                            "phase": "attempt",
                            "provider": "relay",
                            "model": "gpt",
                            "attempt": 1,
                        },
                    ],
                }
            )
        if path == "concurrency":
            return web.json_response(
                {
                    "requests": 1,
                    "waiting": 1,
                    "tools": 0,
                    "active": [
                        {
                            "id": 91,
                            "route": 36,
                            "phase": "slot",
                            "agent": "codex",
                            "session": "not-rendered",
                            "ms": 160,
                        },
                    ],
                }
            )
        if path == "route-events":
            if (
                request.headers.get("Last-Event-ID", request.query.get("after"))
                == "expired"
            ):
                return web.json_response({"error": "snapshot_required"}, status=409)
            event = json.dumps(
                {
                    "cursor": "feed:5",
                    "active": 1,
                    "route": {
                        "id": "10",
                        "time": STAMP,
                        "agent": "codex",
                        "phase": "responding",
                        "provider": "backup",
                        "model": "gpt",
                        "attempt": 2,
                    },
                }
            )
            return web.Response(
                text=f"id: feed:5\nevent: route\ndata: {event}\n\n",
                content_type="text/event-stream",
            )
        if path == "usage":
            return web.json_response(
                {**NOW, "calls": 8 if request.query.get("period") == "7d" else 3}
            )
        if path == "usage/requests":
            return web.json_response(
                {
                    "rows": ROWS if request.query.get("route") else ROWS[:1],
                    "total": 1,
                    "provider_speeds": [
                        SPEED,
                        {"provider": "backup", "model": "gpt", "tries": 1},
                    ],
                }
            )
        if path == "quotas" and request.query.get("kept") == "1":
            return web.json_response(
                {
                    "data": [
                        {
                            "provider": "relay",
                            "name": "Relay quota",
                            "windows": [
                                {
                                    "name": "5h",
                                    "used": 20,
                                    "remaining": 80,
                                    "resetsAt": STAMP,
                                },
                            ],
                        }
                    ]
                }
            )
        if path == "admin/gateway-keys":
            return web.json_response(
                {"keys": [{"id": "key1", "name": "Client", "masked": "masked"}]}
            )
        if path == "admin/gateway-keys/models":
            return web.json_response(
                {"models": [{"id": "relay/gpt", "name": "GPT"}], "accounts": []}
            )
        if path == "admin/routing-groups":
            return web.json_response({"groups": state["groups"], "models": []})
        if path == "admin/providers":
            return web.json_response(
                {"providers": [{**PROVIDER, "models": state["models"]}]}
            )
        if path == "admin/account-groups":
            return web.json_response(
                {
                    "groups": [
                        {
                            "id": "ag",
                            "name": "Team accounts",
                            "accounts": ["relay/ref1"],
                        }
                    ]
                }
            )
        if request.method == "POST" and path.startswith("admin/"):
            if not state["allow_write"]:
                return web.json_response({"error": "permission denied"}, status=403)
            payload = await request.json()
            state["write"] = (path, payload)
            if path == "admin/provider/save":
                existing = {model["id"] for model in state["models"]}
                state["models"].extend(
                    {"id": model, "on": True}
                    for model in payload["models"]
                    if model not in existing
                )
            if path == "admin/provider/models":
                state["models"].append({"id": "refreshed", "on": False})
            if path == "admin/provider/test":
                return web.json_response(
                    {
                        "results": [
                            {
                                "model": model,
                                "ok": model != "unreachable",
                                "status": 503 if model == "unreachable" else 200,
                                "protocol": "chat",
                                "ms": 23,
                                "error": "upstream diagnostic body",
                            }
                            for model in payload["test"]
                        ]
                    }
                )
            return web.json_response(
                {"secret": "fake-new-gateway-key"}
                if path.endswith("/add-key")
                else {"ok": True}
            )
        return web.json_response({"error": "not found"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/v1/magpie/{path:.*}", magpie)
    runner = web.AppRunner(app)
    await runner.setup()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        site = web.SockSite(runner, listener)
        await site.start()
        try:
            yield f"http://127.0.0.1:{listener.getsockname()[1]}", state
        finally:
            await runner.cleanup()


async def add_gateway(client, endpoint, name):
    result = await client.post(
        "/ui/magpie/gateways?gateway_edit=add",
        data={
            "name": name,
            "endpoint": endpoint,
            "key": "gateway-test-credential",
        },
    )
    assert result.status_code == 303, result.text[:900]
    return parse_qs(urlsplit(result.headers["location"]).query)["gateway"][0]


@pytest.mark.asyncio
async def test_server_http_pages_and_public_management_contract(served, upstream):
    endpoint, state = upstream
    async with httpx.AsyncClient(base_url=served.url) as client:
        await served.sign_in(client)
        gateway = await add_gateway(client, endpoint, "Main gateway")
        for query, expected in (
            ("", "Today's routing statistics"),
            ("&view=requests&route=36", "Succeeded after retry"),
            ("&view=usage&period=7d", "Relay quota"),
            ("&view=manage&section=gateway-keys", "Client"),
            ("&view=manage&section=provider-keys", "masked-provider"),
            ("&view=manage&section=account-groups", "Team accounts"),
            ("&view=manage&section=routing-groups", "Found GPT"),
            ("&view=manage&section=models", "Refresh model list"),
        ):
            page = await client.get(f"/ui/magpie/?gateway={gateway}{query}")
            assert page.status_code == 200, page.text[:500]
            assert expected.replace("'", "&#39;") in page.text
            assert "gateway-test-credential" not in page.text
            assert "body from upstream must not be shown" not in page.text
        card = await client.get("/ui/magpie/overview")
        assert card.status_code == 200 and "Main gateway" in card.text
        snapshot = await client.get(f"/ui/magpie/snapshot?gateway={gateway}")
        assert snapshot.json()["routes"][0]["queue"] == "slot"
        assert "not-rendered" not in snapshot.text
        reset = await client.get(
            f"/ui/magpie/events?gateway={gateway}", headers={"Last-Event-ID": "expired"}
        )
        assert "event: reset" in reset.text

        async def operation(section, action, **data):
            return await client.post(
                f"/ui/magpie/manage?gateway={gateway}&section={section}",
                data={"operation": action, **data},
            )

        denied = await operation(
            "gateway-keys", "rename-key", id="key1", name="New name"
        )
        assert denied.status_code == 403 and "HTTP 403" in denied.text
        assert (
            await client.get(f"/ui/magpie/?gateway={gateway}&view=usage")
        ).status_code == 200
        state["allow_write"] = True
        for section, action, fields, expected in (
            (
                "gateway-keys",
                "models-key",
                {"id": "key1", "models": "relay/gpt\nbackup/gpt"},
                (
                    "admin/gateway-keys/models-key",
                    {"key": "key1", "models": ["relay/gpt", "backup/gpt"]},
                ),
            ),
            (
                "provider-keys",
                "replace",
                {"id": "ref1", "provider": "relay", "key": "fixture-secret"},
                (
                    "admin/keys/replace",
                    {"id": "relay", "ref": "ref1", "key": "fixture-secret"},
                ),
            ),
            (
                "account-groups",
                "save",
                {"id": "ag", "name": "Team", "accounts": "relay/ref1"},
                (
                    "admin/account-groups/save",
                    {"id": "ag", "name": "Team", "accounts": ["relay/ref1"]},
                ),
            ),
            (
                "routing-groups",
                "switch",
                {"id": "team", "on": "0"},
                ("admin/routing-groups/switch", {"id": "team", "on": False}),
            ),
            (
                "models",
                "refresh",
                {"provider": "relay"},
                ("admin/provider/models", {"id": "relay"}),
            ),
        ):
            result = await operation(section, action, **fields)
            assert result.status_code == 303
            assert state["write"] == expected
        state.pop("write")
        automatic = await operation("routing-groups", "delete", id="auto-gpt")
        assert automatic.status_code == 400 and "write" not in state
        created = await operation("gateway-keys", "add-key", name="Short lived display")
        assert created.status_code == 200 and "fake-new-gateway-key" in created.text
        edit = await client.post(
            f"/ui/magpie/gateways?gateway_edit=edit&gateway={gateway}",
            data={"name": "Renamed gateway", "endpoint": endpoint},
        )
        assert edit.status_code == 303
        assert "Renamed gateway" in (await client.get(edit.headers["location"])).text
        remove = await client.post(
            f"/ui/magpie/gateways?gateway_edit=remove&gateway={gateway}"
        )
        assert remove.status_code == 303
        assert (
            "No gateways connected"
            in (await client.get(remove.headers["location"])).text
        )


@pytest.mark.asyncio
async def test_browser_selector_live_feed_and_denial(served, upstream):
    endpoint, state = upstream
    async with httpx.AsyncClient(base_url=served.url) as client:
        await served.sign_in(client)
        gateway = await add_gateway(client, endpoint, "Main gateway")
        await add_gateway(client, endpoint, "Second gateway")
        cookies = [
            {"name": key, "value": value, "url": served.url}
            for key, value in client.cookies.items()
        ]
    async with async_playwright() as driver, await driver.chromium.launch() as browser:
        context = await browser.new_context(
            viewport={"width": 1440, "height": 860}, locale="en-US"
        )
        await context.add_cookies(cookies)
        page = await context.new_page()
        await page.goto(f"{served.url}/magpie?gateway={gateway}")
        shell = page.frame_locator("iframe.frame")
        picker = shell.get_by_role("button", name="Switch gateway: Main gateway")
        await expect(picker).to_be_visible()
        await expect(shell.locator("[data-provider]").first).to_have_text("backup")
        center = await picker.evaluate("""button => {
                const a = button.getBoundingClientRect(), b = button.querySelector('.chevron svg').getBoundingClientRect();
                return {delta: Math.abs(a.y + a.height / 2 - b.y - b.height / 2), width: b.width, right: b.right <= a.right};
            }""")
        assert center == {"delta": 0, "width": 14, "right": True}
        await picker.click()
        await expect(shell.locator("#gateway-menu")).to_be_visible()
        if directory := os.environ.get("MAGPIE_SCREENSHOTS"):
            await page.screenshot(path=Path(directory) / "switch-gateway.png")
        await shell.get_by_role("link", name="Second gateway", exact=False).click()
        await expect(
            shell.get_by_role("button", name="Switch gateway: Second gateway")
        ).to_be_visible()
        await shell.get_by_role("link", name="Add gateway", exact=True).click()
        drawer = shell.locator("dialog:modal")
        await expect(drawer).to_be_visible()
        assert (
            await drawer.evaluate("dialog => dialog.getBoundingClientRect().width")
            == 500
        )
        await drawer.get_by_role("link", name="Cancel", exact=True).click()
        await expect(drawer).to_have_count(0)
        await shell.get_by_role("link", name="Gateway settings", exact=True).click()
        await expect(shell.locator(".management .lantern")).to_have_count(0)
        group = shell.locator("summary").filter(has_text="Team route")
        await expect(group.locator(".tag")).to_have_text("Enabled")
        await group.press("Enter")
        await expect(group.locator(".chev svg")).to_have_css(
            "transform", "matrix(-1, 0, 0, -1, 0, 0)"
        )
        await shell.get_by_role("link", name="Gateway Keys", exact=True).click()
        await shell.locator("summary").filter(has_text="Create Gateway Key").click()
        creation = shell.locator('form:has(input[value="add-key"])')
        await expect(creation.get_by_role("radio", name="Standard")).to_be_checked()
        await creation.get_by_role("radio", name="Read-only").click()
        await expect(creation.get_by_role("radio", name="Read-only")).to_be_checked()
        await creation.get_by_role("button", name="Create", exact=True).click()
        await expect(creation.get_by_role("alert")).to_contain_text("Check the inputs")

        await shell.locator("summary").filter(has_text="Client").click()
        await shell.get_by_role("button", name="Rotate Key", exact=True).click()
        confirmation = shell.get_by_role("alertdialog", name="Rotate Key")
        await expect(confirmation).to_be_visible()
        await confirmation.get_by_role("button", name="Cancel").click()
        await expect(confirmation).to_be_hidden()
        assert "write" not in state

        form = shell.locator("form").filter(
            has=shell.locator('input[value="rename-key"]')
        )
        await form.get_by_role("textbox", name="Name").fill("Not lost on 403")
        await form.get_by_role("button", name="Save", exact=True).click()
        await expect(form.get_by_role("alert")).to_contain_text("HTTP 403")
        await expect(form.get_by_role("textbox")).to_have_value("Not lost on 403")
        assert "write" not in state
        if directory := os.environ.get("MAGPIE_SCREENSHOTS"):
            await page.screenshot(path=Path(directory) / "permission-denied.png")
        requests = shell.get_by_role("link", name="Requests", exact=True)
        await expect(requests.locator('svg path[d="M8 5h13"]')).to_have_count(1)
        await requests.click()
        await expect(shell.locator("#request-detail-title")).to_have_text("gpt")
        assert await shell.locator(".panes").evaluate(
            "panes => getComputedStyle(panes).gridTemplateColumns.split(' ').filter(Boolean).filter((width, index) => index !== 1)"
        ) == ["322px", "264px"]
        await page.set_viewport_size({"width": 390, "height": 844})
        await shell.locator(".request-row").first.click()
        await expect(shell.locator("#request-detail-title")).to_have_text("gpt")
        await expect(
            shell.locator(".request-info .kv-row").filter(has_text="Route ID")
        ).to_contain_text("36")
        await expect(shell.get_by_text("HTTP 429", exact=False)).to_be_visible()
        assert await shell.locator("body").evaluate(
            "body => body.scrollWidth <= body.clientWidth"
        )
        await shell.locator(".mobile-back").click()
        await expect(shell.locator(".request-list")).to_be_visible()


@pytest.mark.asyncio
async def test_model_configuration_from_the_browser(served, upstream):
    endpoint, state = upstream
    state["allow_write"] = True
    async with httpx.AsyncClient(base_url=served.url) as client:
        await served.sign_in(client)
        gateway = await add_gateway(client, endpoint, "Model test gateway")
        cookies = [
            {"name": key, "value": value, "url": served.url}
            for key, value in client.cookies.items()
        ]

    async with async_playwright() as driver, await driver.chromium.launch() as browser:
        context = await browser.new_context(viewport={"width": 1200, "height": 840})
        await context.add_cookies(cookies)
        page = await context.new_page()
        await page.goto(
            f"{served.url}/magpie?gateway={gateway}&view=manage&section=models"
        )
        shell = page.frame_locator("iframe.frame")
        editor = shell.locator(".management .manage-item[open]")
        await expect(editor.locator(".provider-models .chip")).to_have_count(3)
        await editor.get_by_role("button", name="Refresh model list").click()
        await expect(
            editor.get_by_role("button", name="Test refreshed", exact=True)
        ).to_be_visible()
        assert state["write"] == ("admin/provider/models", {"id": "relay"})

        field = editor.get_by_role("textbox", name="Model IDs (separate with commas)")
        await field.fill("custom-one, custom two, custom-one")
        await editor.get_by_role("button", name="Add models").click()
        await expect(
            editor.get_by_role("button", name="Test custom two", exact=True)
        ).to_be_visible()
        assert state["write"] == (
            "admin/provider/save",
            {
                "id": "relay",
                "name": "Relay",
                "chat": "https://relay.example/v1",
                "headers": {"X-Tenant": "team"},
                "fallback": ["backup/gpt"],
                "modelsURL": "https://relay.example/catalog",
                "models": ["gpt", "unreachable", "custom-one", "custom two"],
            },
        )

        await editor.get_by_role("button", name="Test unreachable", exact=True).click()
        await expect(editor.locator('[data-probe="unreachable"]')).to_contain_text(
            "HTTP 503"
        )
        await expect(editor.locator('button[value="unreachable"]')).to_have_attribute(
            "data-state", "failed"
        )
        assert state["write"] == (
            "admin/provider/test",
            {"id": "relay", "test": ["unreachable"]},
        )
        await expect(editor.get_by_text("upstream diagnostic body")).to_have_count(0)
        await editor.get_by_role("button", name="Test active models").click()
        await expect(editor.locator("[data-probe]")).to_have_count(4)
        await expect(editor.locator('button[value="gpt"]')).to_have_attribute(
            "data-state", "answered"
        )
        assert state["write"] == (
            "admin/provider/test",
            {"id": "relay", "test": ["gpt", "unreachable", "custom-one", "custom two"]},
        )
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await shell.locator("body").evaluate(
            "body => body.scrollWidth <= body.clientWidth"
        )
