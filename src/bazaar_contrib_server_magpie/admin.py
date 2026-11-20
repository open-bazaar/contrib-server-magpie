"""Named forms backed by the public Magpie management API."""

import asyncio
from collections.abc import Awaitable, Callable

from starlette.datastructures import FormData

from .api import Data

SECTIONS = (
    "routing-groups",
    "gateway-keys",
    "provider-keys",
    "account-groups",
    "models",
)
PATHS = {
    "gateway-keys": "admin/gateway-keys",
    "provider-keys": "admin/providers",
    "account-groups": "admin/account-groups",
    "routing-groups": "admin/routing-groups",
    "models": "admin/providers",
}
CATALOG = "admin/gateway-keys/models"
MODEL_SAVE_FIELDS = (
    "id",
    "name",
    "preset",
    "icon",
    "iconFrom",
    "chat",
    "responses",
    "anthropic",
    "gemini",
    "decide",
    "catalog",
    "headers",
    "fallback",
    "balanceURL",
    "balancePath",
    "balanceMethod",
    "modelsURL",
    "searches",
    "unredacted",
    "pinUpstream",
    "countFree",
    "billing",
    "unlisted",
)
type Read = Callable[..., Awaitable[Data]]


async def load(section: str, read: Read) -> Data:
    path = PATHS[section]
    if section in ("gateway-keys", "account-groups", "routing-groups"):
        config, catalog = await asyncio.gather(read(path), read(CATALOG))
    else:
        config, catalog = await read(path), {}
    values = {
        "section": section,
        "sections": SECTIONS,
        "path": path,
        "config": config,
        "catalog": catalog,
    }
    if section == "models":
        chips = {
            provider["id"]: provider_models(provider)
            for provider in config.get("providers") or ()
        }
        values.update(
            model_chips=chips,
            model_batch={
                provider_id: bool(test_models(models))
                for provider_id, models in chips.items()
            },
        )
    return values


def group_members(group: Data) -> list[str]:
    matched = set(group.get("matched") or [])
    return [member for member in (group.get("members") or []) if member not in matched]


def member_choices(config: Data, members: list[str], group: str = "") -> list[Data]:
    groups = [
        {"id": f"group/{item['id']}", "name": item.get("name") or item["id"]}
        for item in config.get("groups") or ()
        if item["id"] != group
    ]
    return picker_choices([*groups, *(config.get("models") or ())], members)


def picker_choices(entries: list[Data], selected: list[str]) -> list[Data]:
    choices = {item["id"]: item.get("name") or item["id"] for item in entries}
    return [
        {"id": value, "name": choices.get(value, value), "checked": value in selected}
        for value in dict.fromkeys([*selected, *choices])
    ]


def gateway_models(catalog: Data) -> list[Data]:
    models = catalog.get("models") or []
    wildcards = {
        f"{provider}/*": item.get("providerName") or provider
        for item in [*models, *(catalog.get("providers") or [])]
        if (provider := "group" if item.get("group") else item.get("provider"))
    }
    return [
        *({"id": value, "name": name} for value, name in wildcards.items()),
        *models,
    ]


def provider_models(provider: Data) -> list[Data]:
    can_test = not provider.get("modelTest")
    deciders = set(provider.get("deciders") or ())
    chips: dict[str, Data] = {}
    for model in provider.get("models") or ():
        model_id = model["id"]
        chips[model_id] = {
            "id": model_id,
            "name": model.get("name") or model_id,
            "active": bool(model.get("on")),
            "testable": not (model.get("disabled") or model.get("noAPI"))
            and (
                bool(provider.get("decideTest")) if model_id in deciders else can_test
            ),
        }
    for model_id in provider.get("chosen") or ():
        chips.setdefault(
            model_id,
            {"id": model_id, "name": model_id, "active": False, "testable": can_test},
        )
    hidden = set(provider.get("hiddenDrawers") or ())
    for model_id in provider.get("drawIds") or ():
        chips.setdefault(
            model_id,
            {
                "id": model_id,
                "name": (provider.get("drawNames") or {}).get(model_id, model_id),
                "active": model_id not in hidden,
                "testable": can_test,
                "image": True,
            },
        )
    return list(chips.values())


def test_models(models: list[Data], model: str = "") -> list[str]:
    if model:
        return [
            entry["id"]
            for entry in models
            if entry["id"] == model and entry["testable"]
        ]
    active = [entry for entry in models if entry["active"] and entry["testable"]]
    return [entry["id"] for entry in active if not entry.get("image")] or [
        entry["id"] for entry in active
    ]


def add_models(provider: Data, additions: list[str]) -> Data:
    picks = provider.get("chosen") or []
    if not picks and not provider.get("noModels"):
        picks = [
            model["id"] for model in provider.get("models") or () if model.get("on")
        ]
    selected = list(dict.fromkeys([*picks, *additions]))
    if selected == picks:
        return {}
    return {
        **{field: provider[field] for field in MODEL_SAVE_FIELDS if field in provider},
        "models": selected,
    }


def model_results(answer: Data, models: list[str]) -> list[Data]:
    return [
        {
            "model": model,
            "state": (
                "queued"
                if result.get("queued")
                else "answered"
                if result.get("ok")
                else "failed"
            ),
            "status": result.get("status"),
            "protocol": result.get("protocol"),
            "ms": result.get("ms"),
        }
        for model, result in zip(models, answer["results"], strict=True)
    ]


def command(section: str, action: str, form: FormData) -> tuple[str, Data]:
    def value(name: str, *, required: bool = False) -> str:
        text = str(form.get(name, "")).strip()
        if required and not text:
            raise ValueError
        return text

    def lines(name: str) -> list[str]:
        return list(
            dict.fromkeys(
                text.strip()
                for item in form.getlist(name)
                for text in str(item).splitlines()
                if text.strip()
            )
        )

    if section == "gateway-keys":
        if action == "add-key":
            return f"gateway-keys/{action}", {
                "name": value("name", required=True),
                "readOnly": value("readOnly") == "1",
            }
        if action in (
            "rename-key",
            "rotate-key",
            "on-key",
            "off-key",
            "remove-key",
            "models-key",
        ):
            body: Data = {"key": value("id", required=True)}
            if action == "rename-key":
                body["name"] = value("name", required=True)
            if action == "models-key":
                models = lines("models")
                if (pattern := value("model_pattern")) and pattern not in models:
                    models.append(pattern)
                body["models"] = models
            return f"gateway-keys/{action}", body
    elif section == "provider-keys":
        if action in ("add", "rename", "replace", "on", "off", "remove"):
            body = {"id": value("provider", required=True)}
            if action != "add":
                body["ref"] = value("id", required=True)
            if action in ("rename", "add"):
                body["name"] = value("name")
            if action in ("add", "replace"):
                body["key"] = value("key", required=True)
            return f"keys/{action}", body
    elif section == "account-groups":
        if action in ("save", "remove"):
            body = {"id": value("id", required=action == "remove")}
            if action == "save":
                body.update(
                    name=value("name", required=True), accounts=lines("accounts")
                )
            return f"account-groups/{action}", body
    elif section == "routing-groups":
        if action in ("create", "update", "switch", "delete"):
            body = {"id": value("id", required=True)}
            if action in ("create", "update"):
                body.update(name=value("name", required=True), note=value("note"))
                members = lines("members")
                if action == "create" or members != lines("original_members"):
                    body["members"] = members
                if action == "update":
                    body["from"] = value("original_id", required=True)
            elif action == "switch":
                body["on"] = value("on") == "1"
            return f"routing-groups/{action}", body
    elif section == "models":
        body = {"id": value("provider", required=True)}
        if action == "refresh":
            return "provider/models", body
        if action == "add-model":
            typed = value("models", required=True).replace(",", "\n")
            models = [model.strip() for model in typed.splitlines() if model.strip()]
            if models:
                return "provider/save", {**body, "models": models}
        if action == "test-models":
            return "provider/test", {**body, "model": value("model")}
    raise ValueError
