from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.ontap_client import OntapApiError, OntapClient

router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "ontap_host": settings.ontap_host,
        "default_svm": settings.default_svm,
    }


@router.get("/svms")
async def svms():
    try:
        return {"records": await OntapClient(get_settings()).list_svms()}
    except OntapApiError as exc:
        raise HTTPException(exc.status_code, detail=exc.message)
