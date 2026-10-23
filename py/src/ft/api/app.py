"""Live API service (Vercel Services, routed under /api). The command bar lands here in the slice; the admin
routes (`/api/admin/*`) are in `ft.api.refresh`, each behind `ft.api.admin_auth.require_admin`."""
from fastapi import FastAPI

from ft.api.refresh import router as admin_router

app = FastAPI(title="Finance Terminal API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin_router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
