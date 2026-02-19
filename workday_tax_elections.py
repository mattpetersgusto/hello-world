"""
End-to-end workflow for updating Workday local tax elections.

Workflow:
1) Pull worker address rows from a Workday Custom Report JSON endpoint.
2) Geocode each worker address with Symmetry Payroll Point API to obtain
   local tax code and jurisdiction details.
3) Submit Workday Payroll SOAP updates via Maintain_Employee_Tax_Elections.
4) Print a run summary and write failed_workers.json for follow-up.

All credentials and environment-specific configuration are read from a .env file.
No credentials are hardcoded.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from requests import Response
from zeep import Client
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

ERROR_LOG_FILE = "geocode_errors.log"
FAILED_WORKERS_FILE = "failed_workers.json"
REQUEST_TIMEOUT_SECONDS = 30


def configure_logging() -> logging.Logger:
    """Configure one logger that writes to stdout and geocode_errors.log."""
    logger = logging.getLogger("workday_tax_elections")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(ERROR_LOG_FILE)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_required_env(name: str) -> str:
    """Return required env value or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_address(worker_row: Dict[str, Any]) -> str:
    """Build a human-readable full address string."""
    street = str(worker_row.get("Street_Address", "")).strip()
    city = str(worker_row.get("City", "")).strip()
    state = str(worker_row.get("State", "")).strip()
    zip_code = str(worker_row.get("Zip", "")).strip()
    return f"{street}, {city}, {state} {zip_code}".strip()


def fetch_workday_report_rows(
    report_url: str, username: str, password: str, session: requests.Session
) -> List[Dict[str, Any]]:
    """Step 1: Fetch Workday Custom Report rows from Report_Entry."""
    response: Response = session.get(
        report_url,
        auth=(username, password),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    payload = response.json()
    rows = payload.get("Report_Entry", [])
    if isinstance(rows, dict):
        return [rows]
    if not isinstance(rows, list):
        return []
    return rows


def fetch_csv_work_rows(csv_path: str) -> List[Dict[str, Any]]:
    """Read worker rows from CSV, mapping Work_* address fields to expected keys."""
    worker_rows: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            work_street_1 = str(
                row.get("Work_Street_Address") or row.get("Street_Address") or ""
            ).strip()
            work_street_2 = str(
                row.get("Work_Street_Address_2") or row.get("Street_Address_2") or ""
            ).strip()
            if work_street_2:
                work_street_1 = f"{work_street_1} {work_street_2}".strip()

            worker_rows.append(
                {
                    "Worker_ID": str(
                        row.get("Worker_ID") or row.get("Employee_ID") or ""
                    ).strip(),
                    "Street_Address": work_street_1,
                    "City": str(row.get("Work_City") or row.get("City") or "").strip(),
                    "State": str(row.get("Work_State") or row.get("State") or "").strip(),
                    "Zip": str(row.get("Work_Zip") or row.get("Zip") or "").strip(),
                    "Filing_Status": str(row.get("Filing_Status") or "").strip(),
                }
            )
    return worker_rows


def log_geocode_failure(
    logger: logging.Logger, worker_id: str, address: str, reason: str
) -> None:
    """Log geocode failures in the required structured format."""
    logger.error(
        "GEOCODE_FAIL | Worker: %s | Address: %s | Reason: %s",
        worker_id,
        address,
        reason,
    )


def call_symmetry_for_local_tax(
    worker_row: Dict[str, Any],
    symmetry_api_key: str,
    symmetry_calculate_url: str,
    session: requests.Session,
    logger: logging.Logger,
) -> Optional[Tuple[str, str]]:
    """Step 2: Call Symmetry and return (local_tax_code, jurisdiction_name)."""
    worker_id = str(worker_row.get("Worker_ID", "")).strip()
    address = build_address(worker_row)

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": symmetry_api_key,
    }
    body = {
        "street_line_1": str(worker_row.get("Street_Address", "")).strip(),
        "city": str(worker_row.get("City", "")).strip(),
        "state": str(worker_row.get("State", "")).strip(),
        "zip_code": str(worker_row.get("Zip", "")).strip(),
    }

    try:
        response = session.post(
            symmetry_calculate_url,
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        local_taxes = ((payload.get("taxes") or {}).get("local") or [])
        if not isinstance(local_taxes, list) or not local_taxes:
            log_geocode_failure(
                logger,
                worker_id,
                address,
                "No taxes.local entries returned",
            )
            return None

        first_local_tax = local_taxes[0] or {}
        local_tax_code = str(first_local_tax.get("tax_code", "")).strip()
        jurisdiction_name = str(first_local_tax.get("jurisdiction_name", "")).strip()

        if not local_tax_code or not jurisdiction_name:
            log_geocode_failure(
                logger,
                worker_id,
                address,
                "Missing tax_code or jurisdiction_name in taxes.local[0]",
            )
            return None

        return local_tax_code, jurisdiction_name

    except requests.RequestException as exc:
        log_geocode_failure(logger, worker_id, address, str(exc))
        return None
    except ValueError as exc:
        log_geocode_failure(logger, worker_id, address, f"Invalid JSON response: {exc}")
        return None


def build_soap_client(wsdl_url: str, username: str, password: str) -> Client:
    """Create and return one reusable zeep client with WS-Security token."""
    transport = Transport(timeout=REQUEST_TIMEOUT_SECONDS)
    wsse = UsernameToken(username, password)
    return Client(wsdl=wsdl_url, wsse=wsse, transport=transport)


def update_workday_tax_election(
    soap_client: Client,
    worker_row: Dict[str, Any],
    local_tax_code: str,
    jurisdiction_name: str,
    effective_date: str,
) -> None:
    """Step 3: Call Maintain_Employee_Tax_Elections for one worker."""
    worker_id = str(worker_row.get("Worker_ID", "")).strip()
    filing_status = str(worker_row.get("Filing_Status", "")).strip() or "Single"

    # Build a straightforward request payload from the required business values.
    request_payload = {
        "Employee_ID": worker_id,
        "Tax_Code": local_tax_code,
        "Jurisdiction_Name": jurisdiction_name,
        "Effective_Date": effective_date,
        "Filing_Status": filing_status,
        "Withholding_Allowances": 0,
    }

    soap_client.service.Maintain_Employee_Tax_Elections(**request_payload)


def parse_args() -> argparse.Namespace:
    """Parse CLI options for choosing worker source and SOAP behavior."""
    parser = argparse.ArgumentParser(
        description="Update Workday tax elections using Workday report or CSV input."
    )
    parser.add_argument(
        "--csv-path",
        help=(
            "Optional path to a CSV file. If provided, rows are read from CSV "
            "instead of WORKDAY_REPORT_URL. Work_* address columns are preferred."
        ),
    )
    parser.add_argument(
        "--skip-soap",
        action="store_true",
        help="Run Symmetry geocoding only and skip Workday SOAP updates.",
    )
    return parser.parse_args()


def run() -> None:
    """Run the complete workflow and output summary artifacts."""
    # Load .env first so all API credentials/URLs come from environment variables.
    load_dotenv()
    args = parse_args()
    logger = configure_logging()

    # Read shared Symmetry config needed for every worker.
    symmetry_api_key = get_required_env("SYMMETRY_API_KEY")
    symmetry_calculate_url = get_required_env("SYMMETRY_CALCULATE_URL")

    # Keep one HTTP session for efficient TCP reuse across requests.
    session = requests.Session()

    # Step 1: choose worker source (CSV vs Workday report endpoint).
    if args.csv_path:
        worker_rows = fetch_csv_work_rows(args.csv_path)
        logger.info("Loaded %s worker rows from CSV: %s", len(worker_rows), args.csv_path)
    else:
        workday_report_url = get_required_env("WORKDAY_REPORT_URL")
        workday_username = get_required_env("WORKDAY_USERNAME")
        workday_password = get_required_env("WORKDAY_PASSWORD")
        worker_rows = fetch_workday_report_rows(
            report_url=workday_report_url,
            username=workday_username,
            password=workday_password,
            session=session,
        )
        logger.info(
            "Loaded %s worker rows from Workday custom report", len(worker_rows)
        )

    # Create one reusable SOAP client unless the run is explicitly geocode-only.
    soap_client: Optional[Client] = None
    tax_effective_date = os.getenv("TAX_EFFECTIVE_DATE", "2025-01-01")
    if not args.skip_soap:
        workday_soap_wsdl = get_required_env("WORKDAY_SOAP_WSDL")
        workday_username = get_required_env("WORKDAY_USERNAME")
        workday_password = get_required_env("WORKDAY_PASSWORD")
        soap_client = build_soap_client(
            wsdl_url=workday_soap_wsdl,
            username=workday_username,
            password=workday_password,
        )

    geocode_failures: List[Dict[str, str]] = []
    soap_failures: List[str] = []
    succeeded = 0
    geocode_successes: List[Dict[str, str]] = []

    for worker_row in worker_rows:
        worker_id = str(worker_row.get("Worker_ID", "")).strip()
        address = build_address(worker_row)

        # Guard against empty Work_* address data before external API calls.
        if not all(
            [
                str(worker_row.get("Street_Address", "")).strip(),
                str(worker_row.get("City", "")).strip(),
                str(worker_row.get("State", "")).strip(),
                str(worker_row.get("Zip", "")).strip(),
            ]
        ):
            log_geocode_failure(
                logger,
                worker_id,
                address,
                "Missing one or more required address fields",
            )
            geocode_failures.append({"worker_id": worker_id, "address": address})
            continue

        # Step 2: lookup local tax details from Symmetry; skip on any lookup issue.
        local_tax_data = call_symmetry_for_local_tax(
            worker_row=worker_row,
            symmetry_api_key=symmetry_api_key,
            symmetry_calculate_url=symmetry_calculate_url,
            session=session,
            logger=logger,
        )
        if not local_tax_data:
            geocode_failures.append({"worker_id": worker_id, "address": address})
            continue

        local_tax_code, jurisdiction_name = local_tax_data
        geocode_successes.append(
            {
                "worker_id": worker_id,
                "address": address,
                "local_tax_code": local_tax_code,
                "jurisdiction_name": jurisdiction_name,
            }
        )

        if args.skip_soap:
            succeeded += 1
            continue

        # Step 3: submit tax election update to Workday; keep processing on failure.
        try:
            if soap_client is None:
                raise RuntimeError("SOAP client was not initialized")
            update_workday_tax_election(
                soap_client=soap_client,
                worker_row=worker_row,
                local_tax_code=local_tax_code,
                jurisdiction_name=jurisdiction_name,
                effective_date=tax_effective_date,
            )
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - required to continue overall run
            logger.error("SOAP_FAIL | Worker: %s | Reason: %s", worker_id, exc)
            soap_failures.append(worker_id)

    # Step 4: persist failed worker details for retry/research.
    with open(FAILED_WORKERS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {
                "geocode_failures": geocode_failures,
                "soap_failures": soap_failures,
            },
            file,
            indent=2,
        )

    # Persist successful Symmetry geocodes so CSV-only runs have a concrete output.
    with open("symmetry_results.json", "w", encoding="utf-8") as file:
        json.dump(geocode_successes, file, indent=2)

    # Print required completion summary to console (and log file).
    logger.info("==================================================")
    logger.info("RUN COMPLETE")
    logger.info("  ✅ Succeeded:         %s", succeeded)
    logger.info(
        "  ❌ Geocode failures:  %s  (see geocode_errors.log)",
        len(geocode_failures),
    )
    logger.info("  ❌ SOAP failures:     %s", len(soap_failures))
    logger.info("==================================================")


if __name__ == "__main__":
    run()
