from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from .config import load_settings
from .mapping import load_mapping, map_symmetry_to_workday
from .symmetry_client import SymmetryClient
from .workday_client import WorkdayClient

console = Console()


@click.group()
def cli() -> None:
    """CLI to import local tax elections into Workday using Symmetry."""


async def resolve_elections_for_address(symmetry: SymmetryClient, address: dict, mapping_path: Path):
    taxes = await symmetry.get_local_taxes_for_address(
        street=address["street"],
        city=address["city"],
        state=address["state"],
        postal_code=address["postal_code"],
        country=address.get("country", "US"),
    )
    mapping = load_mapping(mapping_path)
    elections = map_symmetry_to_workday(taxes, mapping)
    return elections


@cli.command()
@click.option("--worker-id", required=True, help="Workday worker ID")
@click.option("--street", required=True)
@click.option("--city", required=True)
@click.option("--state", required=True)
@click.option("--postal-code", required=True)
@click.option("--country", default="US")
@click.option("--dry-run/--no-dry-run", default=True)
def single(worker_id: str, street: str, city: str, state: str, postal_code: str, country: str, dry_run: bool):
    """Resolve local taxes for an address and import elections for a single worker."""
    settings = load_settings()
    symmetry = SymmetryClient(settings.symmetry_base_url, settings.symmetry_api_key)

    async def run():
        elections = await resolve_elections_for_address(
            symmetry,
            {
                "street": street,
                "city": city,
                "state": state,
                "postal_code": postal_code,
                "country": country,
            },
            settings.mappings_path,
        )
        table = Table(title="Resolved Elections")
        table.add_column("Workday Code")
        table.add_column("Percent")
        table.add_column("Source")
        for e in elections:
            table.add_row(e.code, f"{(e.percentage or 0)*100:.3f}%", e.additional_fields.get("sourceJurisdiction", ""))
        console.print(table)

        if dry_run:
            console.print("[yellow]Dry run - not sending to Workday[/yellow]")
            return

        wd = WorkdayClient(settings.workday_hcm_url, settings.workday_username, settings.workday_password)
        payload = [
            {"code": e.code, "percentage": e.percentage, **e.additional_fields} for e in elections
        ]
        result = wd.put_local_tax_elections(worker_id, payload, dry_run=False)
        console.print("[green]Submitted elections to Workday[/green]")
        console.print(result)

    asyncio.run(run())


@cli.command()
@click.option("--csv-path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run/--no-dry-run", default=True)
@click.option("--concurrency", default=5, help="Concurrent Symmetry lookups")
def batch(csv_path: Path, dry_run: bool, concurrency: int):
    """Process a CSV with columns: worker_id, street, city, state, postal_code, [country]."""
    settings = load_settings()
    symmetry = SymmetryClient(settings.symmetry_base_url, settings.symmetry_api_key)

    async def run_batch():
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        sem = asyncio.Semaphore(concurrency)

        async def process_row(row):
            async with sem:
                elections = await resolve_elections_for_address(
                    symmetry,
                    {
                        "street": row["street"],
                        "city": row["city"],
                        "state": row["state"],
                        "postal_code": row["postal_code"],
                        "country": row.get("country", "US"),
                    },
                    settings.mappings_path,
                )
                return row["worker_id"], elections

        tasks = [process_row(r) for r in rows]
        results = await asyncio.gather(*tasks)

        wd = None
        if not dry_run:
            wd = WorkdayClient(settings.workday_hcm_url, settings.workday_username, settings.workday_password)

        for worker_id, elections in results:
            table = Table(title=f"Worker {worker_id}")
            table.add_column("Workday Code")
            table.add_column("Percent")
            table.add_column("Source")
            for e in elections:
                table.add_row(e.code, f"{(e.percentage or 0)*100:.3f}%", e.additional_fields.get("sourceJurisdiction", ""))
            console.print(table)

            if not dry_run and wd:
                payload = [
                    {"code": e.code, "percentage": e.percentage, **e.additional_fields} for e in elections
                ]
                wd.put_local_tax_elections(worker_id, payload, dry_run=False)

        if dry_run:
            console.print("[yellow]Dry run complete - no changes sent to Workday[/yellow]")
        else:
            console.print("[green]Submitted elections to Workday[/green]")

    asyncio.run(run_batch())


if __name__ == "__main__":
    cli()
