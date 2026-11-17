"""Live API service (Vercel Services, routed under /api). The command bar lands here in the slice; the admin
routes (`/api/admin/*`) are in `ft.api.refresh`, each behind `ft.api.admin_auth.require_admin`; the daily keep-alive
Vercel cron calls `/api/cron/keepalive` (`ft.api.cron`, behind `CRON_SECRET`); `/api/health` (`ft.api.health`)
is what the outside uptime monitor checks;
`/api/cmdbar/*` (`ft.api.cmdbar`) is the command bar, which answers only the paused state until it is judged (Spec 07 §3), and
`/api/admin/cmdbar/*` (`ft.api.cmdbar_admin`) is its admin page's list of example answers to check."""
from fastapi import FastAPI

from ft.api.cmdbar import router as cmdbar_router
from ft.api.cmdbar_admin import router as cmdbar_admin_router
from ft.api.cron import router as cron_router
from ft.api.health import router as health_router
from ft.api.labelling import router as labelling_router
from ft.api.refresh import router as admin_router

app = FastAPI(title="Finance Terminal API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin_router)
app.include_router(labelling_router)
app.include_router(cron_router)
app.include_router(health_router)
app.include_router(cmdbar_router)
app.include_router(cmdbar_admin_router)
