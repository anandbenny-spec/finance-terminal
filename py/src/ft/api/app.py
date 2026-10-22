"""Live API service (Vercel Services, routed under /api). The command bar and admin routes land here in the
slice; this stub proves the deployment shape."""
from fastapi import FastAPI

app = FastAPI(title="Finance Terminal API", docs_url=None, redoc_url=None)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
