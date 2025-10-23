from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class SymmetryClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_seconds: float = 20.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._headers, params=params)
            response.raise_for_status()
            return response.json()

    async def get_local_taxes_for_address(
        self,
        *,
        street: str,
        city: str,
        state: str,
        postal_code: str,
        country: str = "US",
    ) -> List[Dict[str, Any]]:
        """
        Calls Symmetry to retrieve local jurisdictions for an address.

        Returns a list of tax objects that include identifiers and rates when available.
        """
        params = {
            "street": street,
            "city": city,
            "state": state,
            "postalCode": postal_code,
            "country": country,
        }
        # NOTE: The exact path may differ; adjust to your specific Symmetry subscription
        data = await self._get("v1/tax/localities", params=params)
        items = data.get("localTaxes") or data.get("items") or []
        return items
