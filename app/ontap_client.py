from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings

AVAILABLE_PRIVILEGES = [
    {
        "value": "SeTcbPrivilege",
        "label": "Act as part of the operating system (SeTcbPrivilege)",
    },
    {
        "value": "SeBackupPrivilege",
        "label": "Back up files and directories (SeBackupPrivilege)",
    },
    {
        "value": "SeRestorePrivilege",
        "label": "Restore files and directories (SeRestorePrivilege)",
    },
    {
        "value": "SeTakeOwnershipPrivilege",
        "label": "Take ownership of files/objects (SeTakeOwnershipPrivilege)",
    },
    {
        "value": "SeSecurityPrivilege",
        "label": "Manage security log (SeSecurityPrivilege)",
    },
    {
        "value": "SeChangeNotifyPrivilege",
        "label": "Bypass traverse checking (SeChangeNotifyPrivilege)",
    },
]

SHARE_PERMISSIONS = [
    {"value": "full_control", "label": "Full control"},
    {"value": "change", "label": "Change"},
    {"value": "read", "label": "Read"},
    {"value": "no_access", "label": "No access"},
]


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
        # Path already includes svm.uuid and sid — body must only contain the member name.
        path = (
            f"/protocols/cifs/local-groups/"
            f"{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}/members"
        )
        await self._request("POST", path, json={"name": member})

    async def remove_member(self, svm_name: str, sid: str, member: str) -> None:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/local-groups/"
            f"{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}/members/"
            f"{quote(member, safe='')}"
        )
        await self._request("DELETE", path)

    # ---- Privileges ----

    async def get_privileges(self, svm_name: str, name: str) -> list[str]:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/users-and-groups/privileges/"
            f"{quote(svm['uuid'], safe='')}/{quote(name, safe='')}"
        )
        try:
            data = await self._request(
                "GET",
                path,
                params={"fields": "name,privileges,svm"},
            )
        except OntapApiError as exc:
            if exc.status_code == 404:
                return []
            raise
        if isinstance(data, dict):
            return list(data.get("privileges") or [])
        return []

    async def set_privileges(self, svm_name: str, name: str, privileges: list[str]) -> list[str]:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/users-and-groups/privileges/"
            f"{quote(svm['uuid'], safe='')}/{quote(name, safe='')}"
        )
        body = {"privileges": privileges}
        # PATCH replaces the privilege set. If no record exists yet, POST creates it.
        try:
            await self._request("PATCH", path, json=body)
        except OntapApiError as exc:
            if exc.status_code == 404:
                if not privileges:
                    return []
                await self._request("POST", path, json=body)
            else:
                raise
        return await self.get_privileges(svm_name, name)

    # ---- Aggregates / Volumes ----

    async def list_aggregates(self) -> list[dict]:
        data = await self._request(
            "GET",
            "/storage/aggregates",
            params={"fields": "name,uuid,state,space.available,space.size", "max_records": 1000},
        )
        return data.get("records", [])

    async def list_volumes(self, svm_name: str) -> list[dict]:
        svm = await self.resolve_svm(svm_name)
        data = await self._request(
            "GET",
            "/storage/volumes",
            params={
                "svm.uuid": svm["uuid"],
                "fields": (
                    "name,uuid,size,state,type,style,comment,"
                    "aggregates,nas.path,nas.security_style,svm,space"
                ),
                "max_records": 1000,
            },
        )
        records = data.get("records", [])
        for record in records:
            record.setdefault("svm", svm)
        return records

    async def get_volume(self, uuid: str) -> dict:
        data = await self._request(
            "GET",
            f"/storage/volumes/{quote(uuid, safe='')}",
            params={
                "fields": (
                    "name,uuid,size,state,type,style,comment,"
                    "aggregates,nas.path,nas.security_style,svm,space"
                )
            },
        )
        return data

    async def create_volume(
        self,
        svm_name: str,
        name: str,
        size: str,
        aggregate: str,
        comment: str | None = None,
        junction_path: str | None = None,
        security_style: str | None = None,
    ) -> dict:
        svm = await self.resolve_svm(svm_name)
        body: dict[str, Any] = {
            "name": name,
            "svm": {"uuid": svm["uuid"]},
            "aggregates": [{"name": aggregate}],
            "size": size,
        }
        if comment:
            body["comment"] = comment
        nas: dict[str, Any] = {}
        if junction_path:
            nas["path"] = junction_path
        if security_style:
            nas["security_style"] = security_style
        if nas:
            body["nas"] = nas

        data = await self._request("POST", "/storage/volumes", json=body)
        # Volume create is often async; try to resolve by name afterwards.
        try:
            volumes = await self.list_volumes(svm_name)
            matches = [v for v in volumes if v.get("name") == name]
            if matches:
                return matches[0]
        except OntapApiError:
            pass
        return data or {"name": name, "svm": svm}

    async def update_volume(
        self,
        uuid: str,
        size: str | None = None,
        comment: str | None = None,
        state: str | None = None,
        junction_path: str | None = None,
        security_style: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if size is not None:
            body["size"] = size
        if comment is not None:
            body["comment"] = comment
        if state is not None:
            body["state"] = state
        nas: dict[str, Any] = {}
        if junction_path is not None:
            nas["path"] = junction_path
        if security_style is not None:
            nas["security_style"] = security_style
        if nas:
            body["nas"] = nas
        if body:
            await self._request("PATCH", f"/storage/volumes/{quote(uuid, safe='')}", json=body)
        return await self.get_volume(uuid)

    async def delete_volume(self, uuid: str) -> None:
        await self._request("DELETE", f"/storage/volumes/{quote(uuid, safe='')}")

    # ---- CIFS shares / ACLs (group <-> volume linkage) ----

    async def list_shares(self, svm_name: str) -> list[dict]:
        svm = await self.resolve_svm(svm_name)
        data = await self._request(
            "GET",
            "/protocols/cifs/shares",
            params={
                "svm.uuid": svm["uuid"],
                "fields": "name,path,volume,svm,comment",
                "max_records": 1000,
            },
        )
        records = data.get("records", [])
        for record in records:
            record.setdefault("svm", svm)
        return records

    async def list_share_acls(self, svm_name: str, share: str) -> list[dict]:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/shares/"
            f"{quote(svm['uuid'], safe='')}/{quote(share, safe='')}/acls"
        )
        data = await self._request(
            "GET",
            path,
            params={"fields": "user_or_group,permission,type,sid", "max_records": 1000},
        )
        return data.get("records", [])

    async def add_share_acl(
        self,
        svm_name: str,
        share: str,
        user_or_group: str,
        permission: str,
        acl_type: str = "windows",
    ) -> None:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/shares/"
            f"{quote(svm['uuid'], safe='')}/{quote(share, safe='')}/acls"
        )
        await self._request(
            "POST",
            path,
            json={
                "user_or_group": user_or_group,
                "permission": permission,
                "type": acl_type,
            },
        )

    async def update_share_acl(
        self,
        svm_name: str,
        share: str,
        user_or_group: str,
        permission: str,
        acl_type: str = "windows",
    ) -> None:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/shares/"
            f"{quote(svm['uuid'], safe='')}/{quote(share, safe='')}/acls/"
            f"{quote(user_or_group, safe='')}/{quote(acl_type, safe='')}"
        )
        await self._request("PATCH", path, json={"permission": permission})

    async def remove_share_acl(
        self,
        svm_name: str,
        share: str,
        user_or_group: str,
        acl_type: str = "windows",
    ) -> None:
        svm = await self.resolve_svm(svm_name)
        path = (
            f"/protocols/cifs/shares/"
            f"{quote(svm['uuid'], safe='')}/{quote(share, safe='')}/acls/"
            f"{quote(user_or_group, safe='')}/{quote(acl_type, safe='')}"
        )
        await self._request("DELETE", path)

    async def volume_attached_groups(self, svm_name: str, volume_name: str) -> list[dict]:
        """Groups (and users) attached to a volume via CIFS share ACLs."""
        shares = await self.list_shares(svm_name)
        attached: list[dict] = []
        for share in shares:
            vol = share.get("volume") or {}
            share_vol_name = vol.get("name") if isinstance(vol, dict) else None
            if share_vol_name != volume_name:
                continue
            try:
                acls = await self.list_share_acls(svm_name, share["name"])
            except OntapApiError:
                continue
            for acl in acls:
                attached.append(
                    {
                        "share": share.get("name"),
                        "path": share.get("path"),
                        "user_or_group": acl.get("user_or_group"),
                        "permission": acl.get("permission"),
                        "type": acl.get("type"),
                        "sid": acl.get("sid"),
                    }
                )
        return attached

    async def group_attached_volumes(self, svm_name: str, group_name: str, sid: str | None = None) -> list[dict]:
        """Volumes/shares where this group appears in a share ACL."""
        shares = await self.list_shares(svm_name)
        group_names = {group_name.lower()}
        # Local groups are often referenced as GROUP or CIFS_SERVER\GROUP
        if "\\" in group_name:
            group_names.add(group_name.split("\\", 1)[-1].lower())
        else:
            group_names.add(group_name.lower())

        attached: list[dict] = []
        for share in shares:
            try:
                acls = await self.list_share_acls(svm_name, share["name"])
            except OntapApiError:
                continue
            for acl in acls:
                name = (acl.get("user_or_group") or "").lower()
                short = name.split("\\", 1)[-1] if "\\" in name else name
                acl_sid = acl.get("sid")
                match = name in group_names or short in group_names
                if sid and acl_sid and acl_sid == sid:
                    match = True
                if not match:
                    continue
                vol = share.get("volume") or {}
                attached.append(
                    {
                        "share": share.get("name"),
                        "path": share.get("path"),
                        "volume": vol.get("name") if isinstance(vol, dict) else None,
                        "volume_uuid": vol.get("uuid") if isinstance(vol, dict) else None,
                        "permission": acl.get("permission"),
                        "user_or_group": acl.get("user_or_group"),
                        "type": acl.get("type"),
                    }
                )
        return attached
