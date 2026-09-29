from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers.groups import router as groups_router
from app.routers.system import router as system_router
from app.routers.volumes import router as volumes_router

app = FastAPI(
    title="FSx ONTAP Local Group Manager",
    version="1.1.0",
    description="CRUD UI/API for CIFS local groups, privileges, and volumes on FSx for ONTAP."
)

app.include_router(system_router, prefix="/api")
app.include_router(groups_router, prefix="/api")
app.include_router(volumes_router, prefix="/api")

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse("app/static/index.html")


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"}
    )
