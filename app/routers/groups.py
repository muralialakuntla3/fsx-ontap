from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models import GroupCreate, GroupUpdate, MemberAdd
from app.ontap_client import OntapApiError, OntapClient

router = APIRouter(prefix="/groups", tags=["groups"])


def client() -> OntapClient:
    return OntapClient(get_settings())


def handle_error(exc: OntapApiError):
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("")
async def list_groups(svm: str = Query(..., description="SVM name")):
    try:
        return {"records": await client().list_groups(svm)}
    except OntapApiError as exc:
        handle_error(exc)


@router.post("", status_code=201)
async def create_group(svm: str, payload: GroupCreate):
    try:
        return await client().create_group(svm, payload.name, payload.description)
    except OntapApiError as exc:
        handle_error(exc)


@router.get("/{sid}")
async def get_group(svm: str, sid: str):
    try:
        group = await client().get_group(svm, sid)
        group["members"] = await client().list_members(svm, sid)
        return group
    except OntapApiError as exc:
        handle_error(exc)


@router.patch("/{sid}")
async def update_group(svm: str, sid: str, payload: GroupUpdate):
    try:
        return await client().update_group(svm, sid, payload.name, payload.description)
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
