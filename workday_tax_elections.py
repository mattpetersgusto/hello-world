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


def run() -> None:
    """Run the complete workflow and output summary artifacts."""
    # Load .env first so all API credentials/URLs come from environment variables.
    load_dotenv()
    logger = configure_logging()

    # Read required config values once at startup for fail-fast validation.
    workday_report_url = get_required_env("WORKDAY_REPORT_URL")
    workday_username = get_required_env("WORKDAY_USERNAME")
    workday_password = get_required_env("WORKDAY_PASSWORD")
    workday_soap_wsdl = get_required_env("WORKDAY_SOAP_WSDL")
    symmetry_api_key = get_required_env("SYMMETRY_API_KEY")
    symmetry_calculate_url = get_required_env("SYMMETRY_CALCULATE_URL")
    tax_effective_date = os.getenv("TAX_EFFECTIVE_DATE", "2025-01-01")

    # Keep one HTTP session for efficient TCP reuse across requests.
    session = requests.Session()

    # Step 1: pull all worker rows from the Workday custom report.
    worker_rows = fetch_workday_report_rows(
        report_url=workday_report_url,
        username=workday_username,
        password=workday_password,
        session=session,
    )

    # Create one reusable SOAP client (required) for all Workday updates.
    soap_client = build_soap_client(
        wsdl_url=workday_soap_wsdl,
        username=workday_username,
        password=workday_password,
    )

    geocode_failures: List[Dict[str, str]] = []
    soap_failures: List[str] = []
    succeeded = 0

    for worker_row in worker_rows:
        worker_id = str(worker_row.get("Worker_ID", "")).strip()
        address = build_address(worker_row)

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

        # Step 3: submit tax election update to Workday; keep processing on failure.
        try:
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
