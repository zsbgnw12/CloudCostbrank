"""Probe: are router-level and endpoint-level dependencies AND or OR?"""
from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient


def dep_admin():
    raise HTTPException(403, "admin-required")


def dep_viewer():
    return  # always passes


router = APIRouter()


@router.get("/both")
def both_endpoint():
    return {"ok": True}


@router.get("/only-viewer", dependencies=[Depends(dep_viewer)])
def only_viewer_endpoint():
    """Endpoint adds a permissive dep — does it override the router-level admin dep?"""
    return {"ok": True}


@router.get("/plain")
def plain_endpoint():
    return {"ok": True}


app = FastAPI()
# Router-level: admin-required (always fails)
app.include_router(router, dependencies=[Depends(dep_admin)])

client = TestClient(app)
print("GET /both        =>", client.get("/both").status_code)
print("GET /only-viewer =>", client.get("/only-viewer").status_code)
print("GET /plain       =>", client.get("/plain").status_code)
