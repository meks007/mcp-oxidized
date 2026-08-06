import os
import httpx
from typing import Optional


class OxidizedClient:
    """HTTP client for the Oxidized REST API with Basic Auth support."""

    def __init__(self):
        self.base_url = os.environ["OXIDIZED_URL"].rstrip("/")
        user = os.environ.get("OXIDIZED_USER", "")
        password = os.environ.get("OXIDIZED_PASS", "")
        auth = (user, password) if user else None
        verify_ssl = os.environ.get("OXIDIZED_VERIFY_SSL", "true").lower() not in ("false", "0", "no")
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=auth,
            verify=verify_ssl,
            timeout=30,
            headers={"Accept": "application/json"},
        )

    def get_nodes(self) -> list:
        """Return list of all nodes."""
        resp = self._client.get("/nodes.json")
        resp.raise_for_status()
        return resp.json()

    def get_node(self, node: str) -> dict:
        """Return details for a single node."""
        resp = self._client.get(f"/node/show/{node}.json")
        resp.raise_for_status()
        return resp.json()

    def fetch_config(self, node: str, group: Optional[str] = None) -> str:
        """Return the current running configuration for a node as plain text."""
        url = f"/node/fetch/{node}"
        if group:
            url = f"/node/fetch/{group}/{node}"
        resp = self._client.get(url, headers={"Accept": "text/plain"})
        resp.raise_for_status()
        return resp.text

    def get_versions(self, node: str, group: Optional[str] = None) -> list:
        """Return version history (commits) for a node."""
        url = f"/node/version/{node}.json"
        if group:
            url = f"/node/version/{group}/{node}.json"
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    def fetch_version(self, node: str, oid: str, group: Optional[str] = None) -> str:
        """Return the configuration at a specific git commit OID as plain text."""
        url = f"/node/fetch/{node}"
        if group:
            url = f"/node/fetch/{group}/{node}"
        resp = self._client.get(url, params={"oid": oid}, headers={"Accept": "text/plain"})
        resp.raise_for_status()
        return resp.text

    def close(self):
        self._client.close()
