import os
from typing import Optional
from urllib.parse import quote

import httpx


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
        resp = self._client.get(f"/node/show/{quote(node, safe='')}.json")
        resp.raise_for_status()
        return resp.json()

    def fetch_config(self, node: str, group: Optional[str] = None) -> str:
        """Return the current running configuration for a node as plain text."""
        encoded_node = quote(node, safe="")
        url = f"/node/fetch/{encoded_node}"
        if group:
            url = f"/node/fetch/{quote(group, safe='')}/{encoded_node}"
        resp = self._client.get(url, headers={"Accept": "text/plain"})
        resp.raise_for_status()
        return resp.text

    def get_versions(self, node: str, group: Optional[str] = None) -> list:
        """Return version history (commits) for a node.

        Oxidized Web expects the node, including its optional group, in the
        ``node_full`` query parameter of ``/node/version.json``.
        """
        node_full = f"{group}/{node}" if group else node
        resp = self._client.get(
            "/node/version.json",
            params={"node_full": node_full},
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_version(self, node: str, oid: str, group: Optional[str] = None) -> str:
        """Return the configuration at a specific git commit OID as plain text."""
        encoded_node = quote(node, safe="")
        url = f"/node/fetch/{encoded_node}"
        if group:
            url = f"/node/fetch/{quote(group, safe='')}/{encoded_node}"
        resp = self._client.get(url, params={"oid": oid}, headers={"Accept": "text/plain"})
        resp.raise_for_status()
        return resp.text

    def close(self):
        self._client.close()
