from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models import VolumeCreate, VolumePermissionAttach, VolumeUpdate
from app.ontap_client import NTFS_PERMISSION_PRESETS, OntapApiError, OntapClient

router = APIRouter(prefix="/volumes", tags=["volumes"])


def client() -> OntapClient:
    return OntapClient(get_settings())


def handle_error(exc: OntapApiError):
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/meta/permissions")
async def list_ntfs_permissions():
    return {
        "records": [
            {"value": p["value"], "label": p["label"]} for p in NTFS_PERMISSION_PRESETS
        ]
    }


@router.get("/meta/aggregates")
async def list_aggregates(svm: str | None = Query(None)):
    try:
        c = client()
        if svm:
            records = await c.list_aggregates_for_svm(svm)
        else:
            try:
                records = await c.list_aggregates()
            except OntapApiError:
                records = []
        return {"records": records}
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
        volume["attached_groups"] = await c.volume_attached_groups(svm, volume)
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
        return {"records": await c.volume_attached_groups(svm, volume)}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("/{uuid}/permissions", status_code=201)
async def attach_group_permission(uuid: str, svm: str, payload: VolumePermissionAttach):
    try:
        c = client()
        group_name = payload.group_name
        if not group_name:
            raise HTTPException(status_code=400, detail="group_name is required")
        return await c.attach_group_to_volume(svm, uuid, group_name, payload.permission)
    except OntapApiError as exc:
        handle_error(exc)


@router.delete("/{uuid}/permissions")
async def remove_group_permission(
    uuid: str,
    svm: str,
    user_or_group: str = Query(...),
):
    try:
        await client().detach_group_from_volume(svm, uuid, user_or_group)
        return {"message": f"Removed '{user_or_group}' from volume"}
    except OntapApiError as exc:
        handle_error(exc)
