from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.web.auth import require_auth

router = APIRouter()

MEDIA_ROOT = Path("data/media").resolve()


@router.get("/media/{path:path}")
def serve_media(path: str, _: bool = Depends(require_auth)):
    file = (MEDIA_ROOT / path).resolve()
    if MEDIA_ROOT not in file.parents or not file.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(file)
