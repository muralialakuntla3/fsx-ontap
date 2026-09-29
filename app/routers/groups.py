from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models import GroupCreate, GroupUpdate, MemberAdd, PrivilegesUpdate, VolumePermissionAttach
from app.ontap_client import AVAILABLE_PRIVILEGES, NTFS_PERMISSION_PRESETS, OntapApiError, OntapClient

router = APIRouter(prefix="/groups", tags=["groups"])


def client() -> OntapClient:
    return OntapClient(get_settings())


def handle_error(exc: OntapApiError):
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/meta/privileges")
async def list_available_privileges():
    return {"records": AVAILABLE_PRIVILEGES}


@router.get("/meta/permissions")
async def list_ntfs_permissions():
    return {
        "records": [
            {"value": p["value"], "label": p["label"]} for p in NTFS_PERMISSION_PRESETS
        ]
    }


@router.get("/meta/cifs-server")
async def get_cifs_server(svm: str = Query(...)):
    try:
        cifs = await client().get_cifs_server(svm)
        return {"name": cifs.get("name"), "svm": cifs.get("svm")}
    except OntapApiError as exc:
        handle_error(exc)


@router.get("")
async def list_groups(svm: str = Query(..., description="SVM name")):
    try:
        return {"records": await client().list_groups(svm)}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("", status_code=201)
async def create_group(svm: str, payload: GroupCreate):
    try:
        c = client()
        group = await c.create_group(svm, payload.name, payload.description)
        if payload.privileges:
            group["privileges"] = await c.set_privileges(svm, group["name"], payload.privileges)
        else:
            group["privileges"] = []
        if payload.volume_uuid and payload.permission:
            group["volume_permission"] = await c.attach_group_to_volume(
                svm, payload.volume_uuid, group["name"], payload.permission
            )
        return group
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/{sid}")
async def get_group(svm: str, sid: str):
    try:
        c = client()
        group = await c.get_group(svm, sid)
        group["members"] = await c.list_members(svm, sid)
        group["privileges"] = await c.get_privileges(svm, group["name"])
        try:
            cifs = await c.get_cifs_server(svm)
            group["cifs_server"] = cifs.get("name")
            group["local_account"] = c.local_group_account(cifs["name"], group["name"])
        except OntapApiError:
            group["cifs_server"] = None
            group["local_account"] = group["name"]
        group["attached_volumes"] = await c.group_attached_volumes(svm, group["name"])
        return group
    except OntapApiError as exc:
        handle_error(exc)


@router.patch("/{sid}")
async def update_group(svm: str, sid: str, payload: GroupUpdate):
    try:
        c = client()
        group = await c.update_group(svm, sid, payload.name, payload.description)
        if payload.privileges is not None:
            group["privileges"] = await c.set_privileges(svm, group["name"], payload.privileges)
        else:
            group["privileges"] = await c.get_privileges(svm, group["name"])
        return group
    except OntapApiError as exc:
        handle_error(exc)


@router.delete("/{sid}", status_code=204)
async def delete_group(svm: str, sid: str):
    try:
        await client().delete_group(svm, sid)
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/{sid}/members")
async def list_members(svm: str, sid: str):
    try:
        return {"records": [{"name": m} for m in await client().list_members(svm, sid)]}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("/{sid}/members", status_code=201)
async def add_member(svm: str, sid: str, payload: MemberAdd):
    try:
        await client().add_member(svm, sid, payload.name)
        return {"message": f"Member '{payload.name}' added"}
    except OntapApiError as exc:
        handle_error(exc)


@router.delete("/{sid}/members")
async def remove_member(svm: str, sid: str, name: str = Query(...)):
    try:
        await client().remove_member(svm, sid, name)
        return {"message": f"Member '{name}' removed"}
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/{sid}/privileges")
async def get_group_privileges(svm: str, sid: str):
    try:
        c = client()
        group = await c.get_group(svm, sid)
        privileges = await c.get_privileges(svm, group["name"])
        return {"name": group["name"], "privileges": privileges}
    except OntapApiError as exc:
        handle_error(exc)


@router.put("/{sid}/privileges")
async def update_group_privileges(svm: str, sid: str, payload: PrivilegesUpdate):
    try:
        c = client()
        group = await c.get_group(svm, sid)
        privileges = await c.set_privileges(svm, group["name"], payload.privileges)
        return {"name": group["name"], "privileges": privileges}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("/{sid}/volume-permissions", status_code=201)
async def attach_group_volume_permission(svm: str, sid: str, payload: VolumePermissionAttach):
    try:
        if not payload.volume_uuid:
            raise HTTPException(status_code=400, detail="volume_uuid is required")
        c = client()
        group = await c.get_group(svm, sid)
        return await c.attach_group_to_volume(
            svm, payload.volume_uuid, group["name"], payload.permission
        )
    except OntapApiError as exc:
        handle_error(exc)


@router.delete("/{sid}/volume-permissions")
async def detach_group_volume_permission(
    svm: str,
    sid: str,
    volume_uuid: str = Query(...),
    user_or_group: str | None = Query(None),
):
    try:
        c = client()
        group = await c.get_group(svm, sid)
        account = user_or_group
        if not account:
            cifs = await c.get_cifs_server(svm)
            account = c.local_group_account(cifs["name"], group["name"])
        await c.detach_group_from_volume(svm, volume_uuid, account)
        return {"message": f"Removed '{account}' from volume"}
    except OntapApiError as exc:
        handle_error(exc)
