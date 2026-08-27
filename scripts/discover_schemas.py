"""Dump the column names of ADMCMS tables, straight from the live database.

`CLAUDE.md` says to verify column names instead of guessing them, and ADMCMS
punishes guessing: `Quantity` is `ThisIinvoice`, `UnitPrice` is `SalesPrice`,
`CustSuppnr` on Salesinvoice is `Customer`. This is the shortest way to check
before writing a tool.

Usage:

    # against a local server started with AUTH_DISABLED=true
    python scripts/discover_schemas.py

    # against production, with a token from an authenticated MCP client
    python scripts/discover_schemas.py https://admcms.campai.nl/mcp --token "$TOKEN"

    # other tables than the default set
    python scripts/discover_schemas.py --tables Product CustSupplier
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_TABLES = [
    "SALESINVOICE",
    "SALESINVOICELINES",
    "SalesOrderHeader",
    "ProductWarehouse",
    "PurchaseOrder",
    "PurchaseOrderInvoice",
    "Product",
    "CustSupplier",
]


async def dump(url: str, tables: list[str], token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with (
        streamablehttp_client(url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        for table in tables:
            print(f"\n### {table}")
            result = await session.call_tool("get_schema", {"table_name": table})
            for item in result.content:
                if not hasattr(item, "text"):
                    continue
                parsed = json.loads(item.text)
                if parsed.get("error"):
                    print(f"  FOUT: {parsed['error']}")
                    continue
                for column in parsed.get("columns", []):
                    print(f"  {column['COLUMN_NAME']:40s} {column['DATA_TYPE']:15s}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://localhost:8000/mcp")
    parser.add_argument(
        "--token",
        default=os.environ.get("ADMCMS_MCP_TOKEN", ""),
        help="Bearer token; not needed against a local AUTH_DISABLED server.",
    )
    parser.add_argument("--tables", nargs="+", default=DEFAULT_TABLES)
    args = parser.parse_args()

    asyncio.run(dump(args.url, args.tables, args.token))


if __name__ == "__main__":
    main()
