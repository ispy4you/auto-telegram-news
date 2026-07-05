from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.web.auth import require_auth

router = APIRouter()


@router.get("/media/{path:path}")
def serve_media(path: str, _: bool = Depends(require_auth)):
    file = Path("data/media") / path
    if not file.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(file)
