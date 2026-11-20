"""The endpoint and one credential for each connected gateway."""

from dataclasses import dataclass, field
from typing import cast
from uuid import uuid7

import bazaar_contrib_storage as storage
from bazaar_contrib_schema import Migration, ModuleSchema

TABLE = "magpie_gateways"
GATEWAYS = storage.Table(
    name=TABLE,
    columns=tuple(
        storage.ColumnDefinition(name=name, type="text")
        for name in (
            "id",
            "name",
            "endpoint",
            "key",
        )
    ),
    primary_key=("id",),
)
MODULE = ModuleSchema(
    tables=(TABLE,),
    migrations=(
        Migration(
            version=1, name=TABLE, operations=(storage.CreateTable(table=GATEWAYS),)
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Gateway:
    id: str
    name: str
    endpoint: str


@dataclass(frozen=True, slots=True)
class Connection:
    gateway: Gateway
    key: str = field(repr=False)


def selection(gateway_id: str) -> storage.Compare:
    return storage.Compare(
        left=storage.Column(name="id"),
        operator="=",
        right=storage.Value(value=gateway_id),
    )


def gateway(row: storage.Record) -> Gateway:
    return Gateway(
        cast(str, row["id"]), cast(str, row["name"]), cast(str, row["endpoint"])
    )


class Gateways:
    def __init__(self, database: storage.Database) -> None:
        self.database = database

    async def list(self) -> list[Gateway]:
        rows = await self.database.read(
            lambda session: session.select(
                storage.Select(
                    table=TABLE,
                    columns={
                        name: storage.Column(name=name)
                        for name in ("id", "name", "endpoint")
                    },
                    order_by=(storage.Sort(expression=storage.Column(name="id")),),
                )
            )
        )
        return [gateway(row) for row in rows]

    async def get(self, gateway_id: str) -> Connection | None:
        rows = await self.database.read(
            lambda session: session.select(
                storage.Select(
                    table=TABLE,
                    where=selection(gateway_id),
                )
            )
        )
        return Connection(gateway(rows[0]), cast(str, rows[0]["key"])) if rows else None

    async def add(self, name: str, endpoint: str, key: str) -> Gateway:
        item = Gateway(str(uuid7()), name, endpoint)
        await self.database.write(
            lambda session: session.insert(
                storage.Insert(
                    table=TABLE,
                    rows=(
                        {"id": item.id, "name": name, "endpoint": endpoint, "key": key},
                    ),
                )
            )
        )
        return item

    async def edit(self, gateway_id: str, name: str, endpoint: str, key: str) -> None:
        values = {"name": name, "endpoint": endpoint}
        if key:
            values["key"] = key
        await self.database.write(
            lambda session: session.update(
                storage.Update(
                    table=TABLE,
                    values={
                        name: storage.Value(value=value)
                        for name, value in values.items()
                    },
                    where=selection(gateway_id),
                )
            )
        )

    async def remove(self, gateway_id: str) -> None:
        await self.database.write(
            lambda session: session.delete(
                storage.Delete(
                    table=TABLE,
                    where=selection(gateway_id),
                )
            )
        )
