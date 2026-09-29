from __future__ import annotations

import asyncio
import time
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

# NTFS path permission presets from FSx_Permissions.docx (Project RW / Project RO).
# Note: do not include "synchronize" — some FSx/ONTAP builds reject it.
NTFS_PERMISSION_PRESETS = [
    {
        "value": "modify",
        "label": "Modify (Read/Write)",
        "advanced_rights": {
            "read_data": True,
            "read_attr": True,
            "read_ea": True,
            "read_perm": True,
            "execute_file": True,
            "write_data": True,
            "append_data": True,
            "write_attr": True,
            "write_ea": True,
            "delete": True,
            "delete_child": True,
        },
    },
    {
        "value": "read_and_execute",
        "label": "Read and Execute (Read-Only)",
        "advanced_rights": {
            "read_data": True,
            "read_attr": True,
            "read_ea": True,
            "read_perm": True,
            "execute_file": True,
        },
    },
    {
        "value": "full_control",
        "label": "Full control",
        "advanced_rights": {"full_control": True},
    },
]

APPLY_TO_ALL = {
    "this_folder": True,
    "sub_folders": True,
    "files": True,
}


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

    async def wait_job(self, job_uuid: str, timeout: float = 90.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            data = await self._request("GET", f"/cluster/jobs/{quote(job_uuid, safe='')}")
            state = (data or {}).get("state")
            if state in ("success", "failure", "error"):
                if state != "success":
                    msg = (data or {}).get("message") or f"ONTAP job {job_uuid} ended with {state}"
                    raise OntapApiError(500, msg, data)
                return data or {}
            if time.monotonic() >= deadline:
                raise OntapApiError(504, f"Timed out waiting for ONTAP job {job_uuid}")
            await asyncio.sleep(1.0)

    async def _request_job(self, method: str, path: str, **kwargs) -> Any:
        data = await self._request(method, path, **kwargs)
        if isinstance(data, dict):
            job = data.get("job") or {}
            job_uuid = job.get("uuid")
            if job_uuid:
                await self.wait_job(job_uuid)
        return data

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

    async def get_cifs_server(self, svm_name: str) -> dict:
        """Return CIFS/SMB server for the SVM (NetBIOS name used as local domain)."""
        if self.settings.cifs_local_domain:
            return {"name": self.settings.cifs_local_domain, "svm": {"name": svm_name}}
        svm = await self.resolve_svm(svm_name)
        data = await self._request(
            "GET",
            "/protocols/cifs/services",
            params={
                "svm.uuid": svm["uuid"],
                "fields": "name,svm,ad_domain,enabled",
                "max_records": 10,
            },
        )
        records = data.get("records", [])
        if not records:
            raise OntapApiError(
                404,
                f"No CIFS/SMB server found for SVM '{svm_name}'. "
                "Set CIFS_LOCAL_DOMAIN in .env or create a CIFS server.",
            )
        return records[0]

    @staticmethod
    def short_name(name: str) -> str:
        return (name or "").split("\\")[-1].strip().lower()

    @staticmethod
    def local_group_account(cifs_server: str, group_name: str) -> str:
        """Build DOMAIN\\group as required by file-security ACL user field."""
        short = (group_name or "").split("\\")[-1].strip()
        domain = (cifs_server or "").split("\\")[0].strip()
        if not short:
            raise OntapApiError(400, "Group name is required")
        if not domain:
            raise OntapApiError(400, "CIFS server / local domain is required")
        return f"{domain}\\{short}"

    @staticmethod
    def encode_fs_path(path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return quote(path, safe="")

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

    async def find_group_by_name(self, svm_name: str, name: str) -> dict | None:
        wanted = self.short_name(name)
        # Prefer server-side name filter first, then fall back to full list match.
        svm = await self.resolve_svm(svm_name)
        try:
            data = await self._request(
                "GET",
                "/protocols/cifs/local-groups",
                params={
                    "svm.uuid": svm["uuid"],
                    "name": name,
                    "fields": "name,description,sid,svm",
                    "max_records": 100,
                },
            )
            records = data.get("records", [])
            for record in records:
                if self.short_name(record.get("name", "")) == wanted:
                    record.setdefault("svm", svm)
                    return record
        except OntapApiError:
            pass

        groups = await self.list_groups(svm_name)
        matches = [g for g in groups if self.short_name(g.get("name", "")) == wanted]
        return matches[0] if matches else None

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

        data = await self._request(
            "POST",
            "/protocols/cifs/local-groups",
            params={"return_records": "true"},
            json=body,
        )

        if isinstance(data, dict):
            if data.get("sid"):
                return data
            records = data.get("records") or []
            if records and records[0].get("sid"):
                return records[0]

        found = await self.find_group_by_name(svm_name, name)
        if not found:
            # Brief delay — ONTAP sometimes indexes the new group slightly later.
            await asyncio.sleep(1.0)
            found = await self.find_group_by_name(svm_name, name)
        if not found:
            raise OntapApiError(500, "Group was created but could not be retrieved")
        return found

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
        try:
            await self._request("POST", path, json={"name": member})
        except OntapApiError as exc:
            # Already a member — treat as success so UI can refresh cleanly.
            msg = (exc.message or "").lower()
            if exc.status_code in (409, 400) and ("duplicate" in msg or "already" in msg):
                return
            raise

    async def remove_member(self, svm_name: str, sid: str, member: str) -> None:
        svm = await self.resolve_svm(svm_name)
        # Preserve DOMAIN\user exactly in the path.
        path = (
            f"/protocols/cifs/local-groups/"
            f"{quote(svm['uuid'], safe='')}/{quote(sid, safe='')}/members/"
            f"{quote(member, safe='')}"
        )
        await self._request("DELETE", path)

    # ---- Privileges (optional Se* privileges) ----

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

    async def list_aggregates_for_svm(self, svm_name: str) -> list[dict]:
        """
        FSx often hides cluster aggregates from SVM-scoped accounts.
        Prefer aggregates discovered from existing volumes on the SVM.
        """
        found: dict[str, dict] = {}
        try:
            for agg in await self.list_aggregates():
                name = agg.get("name")
                if name:
                    found[name] = agg
        except OntapApiError:
            pass

        try:
            for volume in await self.list_volumes(svm_name):
                for agg in volume.get("aggregates") or []:
                    name = agg.get("name") if isinstance(agg, dict) else None
                    if name and name not in found:
                        found[name] = {"name": name, "uuid": agg.get("uuid")}
        except OntapApiError:
            pass

        return sorted(found.values(), key=lambda a: a.get("name") or "")

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

        data = await self._request_job("POST", "/storage/volumes", json=body)
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
            await self._request_job("PATCH", f"/storage/volumes/{quote(uuid, safe='')}", json=body)
        return await self.get_volume(uuid)

    async def delete_volume(self, uuid: str) -> None:
        await self._request_job("DELETE", f"/storage/volumes/{quote(uuid, safe='')}")

    # ---- File-security (NTFS) permissions on volume junction paths ----

    def _preset(self, permission: str) -> dict:
        for preset in NTFS_PERMISSION_PRESETS:
            if preset["value"] == permission:
                return preset
        raise OntapApiError(
            400,
            f"Unknown permission '{permission}'. Use: "
            + ", ".join(p["value"] for p in NTFS_PERMISSION_PRESETS),
        )

    async def get_path_permissions(self, svm_name: str, path: str) -> dict:
        svm = await self.resolve_svm(svm_name)
        api_path = (
            f"/protocols/file-security/permissions/"
            f"{quote(svm['uuid'], safe='')}/{self.encode_fs_path(path)}"
        )
        try:
            return await self._request(
                "GET",
                api_path,
                params={"lookup_names": "true"},
            ) or {}
        except OntapApiError as exc:
            if exc.status_code == 404:
                return {"acls": [], "path": path}
            raise

    async def add_path_acl(
        self,
        svm_name: str,
        path: str,
        user: str,
        permission: str,
    ) -> Any:
        svm = await self.resolve_svm(svm_name)
        preset = self._preset(permission)
        api_path = (
            f"/protocols/file-security/permissions/"
            f"{quote(svm['uuid'], safe='')}/{self.encode_fs_path(path)}/acl"
        )
        body: dict[str, Any] = {
            "access": "access_allow",
            "access_control": "file_directory",
            "user": user,
            "advanced_rights": preset["advanced_rights"],
            "apply_to": APPLY_TO_ALL,
            "propagation_mode": "propagate",
        }
        return await self._request_job(
            "POST",
            api_path,
            params={"return_timeout": 0},
            json=body,
        )

    @staticmethod
    def _is_open_access_principal(name: str | None) -> bool:
        if not name:
            return False
        short = name.split("\\")[-1].strip().lower()
        return short in {
            "everyone",
            "users",  # BUILTIN\Users
            "authenticated users",
        } or name.strip().upper() in {"S-1-1-0", "S-1-5-11", "S-1-5-32-545"}

    @staticmethod
    def _find_ace(acls: list[dict], user: str) -> dict | None:
        wanted = (user or "").strip().lower()
        wanted_short = wanted.split("\\")[-1]
        for ace in acls:
            name = (ace.get("user") or "").strip()
            if not name:
                continue
            name_l = name.lower()
            if name_l == wanted or name_l.split("\\")[-1] == wanted_short:
                return ace
        return None

    @staticmethod
    def _ace_delete_body(ace: dict) -> dict:
        """
        ONTAP requires the DELETE body to match the existing ACE.
        Copy fields from GET, drop user + synchronize (known Everyone delete issue).
        """
        body: dict[str, Any] = {
            "access": ace.get("access") or "access_allow",
            "propagation_mode": "propagate",
        }
        access_control = ace.get("access_control") or "file_directory"
        body["access_control"] = str(access_control).replace("-", "_")

        apply_to = ace.get("apply_to")
        if isinstance(apply_to, dict) and apply_to:
            body["apply_to"] = {k: bool(v) for k, v in apply_to.items() if v is not None}
        else:
            body["apply_to"] = dict(APPLY_TO_ALL)

        # Prefer rights shorthand when present; otherwise mirror advanced_rights.
        if ace.get("rights"):
            body["rights"] = ace["rights"]
        else:
            adv = ace.get("advanced_rights") or {}
            cleaned = {
                k: True
                for k, v in adv.items()
                if v is True and k != "synchronize"
            }
            if cleaned:
                body["advanced_rights"] = cleaned
        return body

    async def remove_path_acl(self, svm_name: str, path: str, user: str) -> Any:
        svm = await self.resolve_svm(svm_name)
        perms = await self.get_path_permissions(svm_name, path)
        acls = perms.get("acls") or []
        ace = self._find_ace(acls, user)
        if not ace:
            raise OntapApiError(
                404,
                f"ACL entry for '{user}' was not found on path '{path}'",
            )

        actual_user = ace.get("user") or user
        api_path = (
            f"/protocols/file-security/permissions/"
            f"{quote(svm['uuid'], safe='')}/{self.encode_fs_path(path)}/acl/"
            f"{quote(actual_user, safe='')}"
        )
        body = self._ace_delete_body(ace)

        try:
            return await self._request_job(
                "DELETE",
                api_path,
                params={"return_timeout": 0},
                json=body,
            )
        except OntapApiError as first_exc:
            # Retry with minimal body (access + apply_to only) — some builds are picky.
            minimal = {
                "access": body["access"],
                "access_control": body["access_control"],
                "apply_to": body["apply_to"],
                "propagation_mode": "propagate",
            }
            try:
                return await self._request_job(
                    "DELETE",
                    api_path,
                    params={"return_timeout": 0},
                    json=minimal,
                )
            except OntapApiError:
                raise first_exc

    async def remove_open_access_acls(self, svm_name: str, path: str) -> list[str]:
        """Remove Everyone / BUILTIN\\Users / Authenticated Users ACEs when present."""
        perms = await self.get_path_permissions(svm_name, path)
        removed: list[str] = []
        for ace in list(perms.get("acls") or []):
            user = ace.get("user")
            if not self._is_open_access_principal(user):
                continue
            try:
                await self.remove_path_acl(svm_name, path, user)
                removed.append(user)
            except OntapApiError:
                # Continue removing other open-access principals.
                continue
        return removed

    @staticmethod
    def _summarize_ace(ace: dict) -> str:
        if ace.get("rights"):
            return str(ace["rights"])
        adv = ace.get("advanced_rights") or {}
        if adv.get("full_control"):
            return "full_control"
        write_like = any(
            adv.get(k)
            for k in ("write_data", "append_data", "delete", "delete_child", "write_attr", "write_ea")
        )
        read_like = any(
            adv.get(k) for k in ("read_data", "read_attr", "execute_file", "read_perm")
        )
        if write_like and read_like:
            return "modify"
        if read_like:
            return "read_and_execute"
        return "custom"

    @staticmethod
    def _names_match(account: str, candidates: set[str]) -> bool:
        account_l = (account or "").lower()
        short = account_l.split("\\")[-1]
        return account_l in candidates or short in candidates

    async def volume_attached_groups(self, svm_name: str, volume: dict) -> list[dict]:
        path = ((volume.get("nas") or {}).get("path")) or None
        if not path:
            return []
        perms = await self.get_path_permissions(svm_name, path)
        attached: list[dict] = []
        for ace in perms.get("acls") or []:
            user = ace.get("user")
            attached.append(
                {
                    "user_or_group": user,
                    "access": ace.get("access"),
                    "permission": self._summarize_ace(ace),
                    "path": path,
                    "volume": volume.get("name"),
                    "volume_uuid": volume.get("uuid"),
                    "apply_to": ace.get("apply_to"),
                    "open_access": self._is_open_access_principal(user),
                }
            )
        return attached

    async def group_attached_volumes(self, svm_name: str, group_name: str) -> list[dict]:
        cifs = await self.get_cifs_server(svm_name)
        account = self.local_group_account(cifs["name"], group_name)
        short = group_name.split("\\")[-1]
        candidates = {account.lower(), short.lower(), group_name.lower()}

        attached: list[dict] = []
        for volume in await self.list_volumes(svm_name):
            path = (volume.get("nas") or {}).get("path")
            if not path:
                continue
            try:
                perms = await self.get_path_permissions(svm_name, path)
            except OntapApiError:
                continue
            for ace in perms.get("acls") or []:
                user = ace.get("user") or ""
                if not self._names_match(user, candidates):
                    continue
                attached.append(
                    {
                        "volume": volume.get("name"),
                        "volume_uuid": volume.get("uuid"),
                        "path": path,
                        "user_or_group": user,
                        "permission": self._summarize_ace(ace),
                        "access": ace.get("access"),
                    }
                )
        return attached

    async def attach_group_to_volume(
        self,
        svm_name: str,
        volume_uuid: str,
        group_name: str,
        permission: str,
        remove_open_access: bool = True,
    ) -> dict:
        volume = await self.get_volume(volume_uuid)
        path = (volume.get("nas") or {}).get("path")
        if not path:
            raise OntapApiError(
                400,
                f"Volume '{volume.get('name')}' has no junction path. "
                "Set a junction path before attaching NTFS permissions.",
            )
        cifs = await self.get_cifs_server(svm_name)
        account = self.local_group_account(cifs["name"], group_name)
        await self.add_path_acl(svm_name, path, account, permission)
        removed_open: list[str] = []
        if remove_open_access:
            removed_open = await self.remove_open_access_acls(svm_name, path)
        return {
            "volume": volume.get("name"),
            "volume_uuid": volume_uuid,
            "path": path,
            "user_or_group": account,
            "permission": permission,
            "cifs_server": cifs.get("name"),
            "removed_open_access": removed_open,
        }

    async def detach_group_from_volume(
        self,
        svm_name: str,
        volume_uuid: str,
        user_or_group: str,
    ) -> None:
        volume = await self.get_volume(volume_uuid)
        path = (volume.get("nas") or {}).get("path")
        if not path:
            raise OntapApiError(400, "Volume has no junction path")
        await self.remove_path_acl(svm_name, path, user_or_group)
