from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models import ShareAclCreate, ShareAclUpdate, VolumeCreate, VolumeUpdate
from app.ontap_client import SHARE_PERMISSIONS, OntapApiError, OntapClient

router = APIRouter(prefix="/volumes", tags=["volumes"])


def client() -> OntapClient:
    return OntapClient(get_settings())


def handle_error(exc: OntapApiError):
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/meta/permissions")
async def list_share_permissions():
    return {"records": SHARE_PERMISSIONS}


@router.get("/meta/aggregates")
async def list_aggregates():
    try:
        return {"records": await client().list_aggregates()}
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/meta/shares")
async def list_shares(svm: str = Query(..., description="SVM name")):
    try:
        return {"records": await client().list_shares(svm)}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("/meta/shares/{share}/acls", status_code=201)
async def create_share_acl(share: str, svm: str, payload: ShareAclCreate):
    try:
        await client().add_share_acl(
            svm, share, payload.user_or_group, payload.permission, payload.type
        )
        return {"message": f"Attached '{payload.user_or_group}' to share '{share}'"}
    except OntapApiError as exc:
        handle_error(exc)


@router.get("")
async def list_volumes(svm: str = Query(..., description="SVM name")):
    try:
        return {"records": await client().list_volumes(svm)}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("", status_code=201)
async def create_volume(svm: str, payload: VolumeCreate):
    try:
        return await client().create_volume(
            svm,
            payload.name,
            payload.size,
            payload.aggregate,
            payload.comment,
            payload.junction_path,
            payload.security_style,
        )
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/{uuid}")
async def get_volume(uuid: str, svm: str = Query(..., description="SVM name")):
    try:
        c = client()
        volume = await c.get_volume(uuid)
        volume["attached_groups"] = await c.volume_attached_groups(svm, volume["name"])
        volume["shares"] = [
            s for s in await c.list_shares(svm)
            if (s.get("volume") or {}).get("uuid") == uuid
            or (s.get("volume") or {}).get("name") == volume.get("name")
        ]
        return volume
    except OntapApiError as exc:
        handle_error(exc)


@router.patch("/{uuid}")
async def update_volume(uuid: str, payload: VolumeUpdate):
    try:
        return await client().update_volume(
            uuid,
            payload.size,
            payload.comment,
            payload.state,
            payload.junction_path,
            payload.security_style,
        )
    except OntapApiError as exc:
        handle_error(exc)


@router.delete("/{uuid}", status_code=204)
async def delete_volume(uuid: str):
    try:
        await client().delete_volume(uuid)
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/{uuid}/groups")
async def list_volume_groups(uuid: str, svm: str = Query(...)):
    try:
        c = client()
        volume = await c.get_volume(uuid)
        return {"records": await c.volume_attached_groups(svm, volume["name"])}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("/{uuid}/acls", status_code=201)
async def attach_group_to_volume(
    uuid: str,
    svm: str,
    payload: ShareAclCreate,
    share: str = Query(..., description="CIFS share name on this volume"),
):
    try:
        c = client()
        volume = await c.get_volume(uuid)
        shares = await c.list_shares(svm)
        match = next(
            (
                s for s in shares
                if s.get("name") == share
                and (
                    (s.get("volume") or {}).get("uuid") == uuid
                    or (s.get("volume") or {}).get("name") == volume.get("name")
                )
            ),
            None,
        )
        if not match:
            raise HTTPException(
                status_code=404,
                detail=f"Share '{share}' was not found on volume '{volume.get('name')}'",
            )
        await c.add_share_acl(svm, share, payload.user_or_group, payload.permission, payload.type)
        return {"message": f"Attached '{payload.user_or_group}' to share '{share}'"}
    except OntapApiError as exc:
        handle_error(exc)


@router.patch("/{uuid}/acls")
async def update_volume_acl(
    uuid: str,
    svm: str,
    payload: ShareAclUpdate,
    share: str = Query(...),
    user_or_group: str = Query(...),
    acl_type: str = Query("windows"),
):
    try:
        await client().update_share_acl(svm, share, user_or_group, payload.permission, acl_type)
        return {"message": f"Updated ACL for '{user_or_group}' on share '{share}'"}
    except OntapApiError as exc:
        handle_error(exc)


@router.delete("/{uuid}/acls")
async def remove_volume_acl(
    uuid: str,
    svm: str,
    share: str = Query(...),
    user_or_group: str = Query(...),
    acl_type: str = Query("windows"),
):
    try:
        await client().remove_share_acl(svm, share, user_or_group, acl_type)
        return {"message": f"Removed ACL for '{user_or_group}' from share '{share}'"}
    except OntapApiError as exc:
        handle_error(exc)
