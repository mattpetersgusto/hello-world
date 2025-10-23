from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from zeep import Client
from zeep.transports import Transport
from requests import Session
from requests.auth import HTTPBasicAuth


class WorkdayClient:
    def __init__(self, hcm_url: str, username: str, password: str, *, timeout_seconds: float = 30.0):
        session = Session()
        session.auth = HTTPBasicAuth(username, password)
        transport = Transport(session=session, timeout=timeout_seconds)
        self._client = Client(hcm_url, transport=transport)

    def put_local_tax_elections(
        self,
        worker_id: str,
        elections: Iterable[Dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> Any:
        """
        Submit local tax elections for a worker.

        Note: The exact Workday service and structure depends on your tenant's version
        and enabled services. Adjust types/names accordingly.
        """
        # This is a placeholder; adapt according to your WSDL's types
        # Example structure; replace with the correct binding and request
        service = self._client.service
        payload = {
            "Worker_Reference": {"ID": [{"type": "Employee_ID", "value": worker_id}]},
            "Local_Tax_Elections_Data": [
                {
                    "Local_Tax_Authority_ID": e["code"],
                    "Withholding_Rate": e.get("percentage"),
                }
                for e in elections
            ],
            "Run_As_Test": dry_run,
        }
        # Replace 'Put_Worker_Tax_Elections' with your actual operation name
        return service.Put_Worker_Tax_Elections(**payload)
