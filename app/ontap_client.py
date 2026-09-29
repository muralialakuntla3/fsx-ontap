from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings


class OntapApiError(Exception):
    def __init__(self, status_code: int, message: str, payload: Any = None):
        self.status_code = status_code
        self.message = message
        self.payload = payload
        super().__init__(message)


class OntapClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.base_url,
            auth=(self.settings.ontap_username, self.settings.ontap_password),
            verify=self.settings.ontap_verify_ssl,
            timeout=self.settings.ontap_timeout,
            headers={"Accept": "application/json"},
        )

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                error = data.get("error", data)
                if isinstance(error, dict):
                    return error.get("message") or str(error)
            return str(data)
        except Exception:
            return response.text or f"HTTP {response.status_code}"

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise OntapApiError(502, f"Could not connect to ONTAP: {exc}") from exc

        if response.status_code >= 400:
            raise OntapApiError(
                response.status_code,
                self._error_message(response),
                response.text,
            )

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}

    async def list_svms(self) -> list[dict]:
        data = await self._request(
            "GET",
            "/svm/svms",
            params={"fields": "name,uuid", "max_records": 1000},
        )
        return data.get("records", [])

    async def resolve_svm(self, svm_name: str) -> dict:
        data = await self._request(
            "GET",
            "/svm/svms",
            params={"name": svm_name, "fields": "name,uuid"},
        )
        records = data.get("records", [])
        if not records:
            raise OntapApiError(404, f"SVM '{svm_name}' was not found")
        return records[0]

    async def list_groups(self, svm_name: str) -> list[dict]:
        svm = await self.resolve_svm(svm_name)
        data = await self._request(
            "GET",
            "/protocols/cifs/local-groups",
            params={
                "svm.uuid": svm["uuid"],
                "fields": "name,description,sid,svm",
                "max_records": 1000,
            },
        )
        records = data.get("records", [])
        for record in records:
            record.setdefault("svm", svm)
        return records

    async def get_group(self, svm_name: str, sid: str) -> dict:
        svm = await self.resolve_svm(svm_name)
        path = f"/protocols/cifs/local-groups/{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}"
        data = await self._request(
            "GET",
            path,
            params={"fields": "name,description,sid,svm"},
        )
        data.setdefault("svm", svm)
        return data

    async def create_group(self, svm_name: str, name: str, description: str | None) -> dict:
        svm = await self.resolve_svm(svm_name)
        body = {
            "name": name,
            "svm": {"uuid": svm["uuid"]},
        }
        if description is not None:
            body["description"] = description

        data = await self._request("POST", "/protocols/cifs/local-groups", json=body)

        # ONTAP may return 201 without a complete resource. Resolve by name.
        if isinstance(data, dict) and data.get("sid"):
            return data
        groups = await self.list_groups(svm_name)
        matches = [g for g in groups if g.get("name", "").lower() == name.lower()]
        if not matches:
            raise OntapApiError(500, "Group was created but could not be retrieved")
        return matches[0]

    async def update_group(
        self,
        svm_name: str,
        sid: str,
        name: str | None,
        description: str | None,
    ) -> dict:
        svm = await self.resolve_svm(svm_name)
        path = f"/protocols/cifs/local-groups/{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}"
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description

        if not body:
            return await self.get_group(svm_name, sid)

        await self._request("PATCH", path, json=body)
        return await self.get_group(svm_name, sid)

    async def delete_group(self, svm_name: str, sid: str) -> None:
        svm = await self.resolve_svm(svm_name)
        path = f"/protocols/cifs/local-groups/{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}"
        await self._request("DELETE", path)

    async def list_members(self, svm_name: str, sid: str) -> list[str]:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/local-groups/"
            f"{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}/members"
        )
        data = await self._request(
            "GET",
            path,
            params={"max_records": 1000},
        )
        return [r["name"] for r in data.get("records", []) if r.get("name")]

    async def add_member(self, svm_name: str, sid: str, member: str) -> None:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/local-groups/"
            f"{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}/members"
        )
        await self._request(
            "POST",
            path,
            json={
                "name": member,
                "svm": {"uuid": svm["uuid"]},
                "local_cifs_group": {"sid": sid},
            },
        )

    async def remove_member(self, svm_name: str, sid: str, member: str) -> None:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/local-groups/"
            f"{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}/members/"
            f"{quote(member, safe='')}"
        )
        await self._request("DELETE", path)
