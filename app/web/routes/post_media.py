"""Медиа у поста: добавить своё, удалить, переставить, перекачать пропавшее.

Отдельный модуль, потому что к списку постов и к генерации это отношения не
имеет: здесь всё про файлы на диске и порядок их в альбоме.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models import ActionLog, MediaItem, MediaOrigin, RawPost
from app.services import media_restore, media_storage
from app.web.auth import require_auth
from app.web.routes.common import back_to_post

router = APIRouter()


@router.post("/posts/{post_id}/restore-media")
async def restore_post_media(post_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    """Перекачивает пропавшее медиа. Страница поста дёргает это сама при открытии."""
    post = db.scalar(
        select(RawPost)
        .options(joinedload(RawPost.source), selectinload(RawPost.media_items))
        .where(RawPost.id == post_id)
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return await media_restore.restore(db, post)


def _media_post(db: Session, post_id: int) -> RawPost | None:
    return db.scalar(
        select(RawPost).options(selectinload(RawPost.media_items)).where(RawPost.id == post_id)
    )


def _discard(saved: list[dict]) -> None:
    for item in saved:
        Path(item["path"]).unlink(missing_ok=True)


def _sync_media_counters(db: Session, post: RawPost) -> None:
    """has_media и media_count — не украшение: по ним решают, что публиковать."""
    count = db.scalar(
        select(func.count()).select_from(MediaItem).where(MediaItem.raw_post_id == post.id)
    ) or 0
    post.media_count = count
    post.has_media = count > 0


@router.post("/posts/{post_id}/media")
async def add_post_media(
    post_id: int,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """Своё фото или видео вместо того, что пришло из канала.

    Файл ложится на диск и живёт до ближайшего перезапуска: восстановить его
    неоткуда, и панель об этом честно скажет.
    """
    post = _media_post(db, post_id)
    if not post:
        return RedirectResponse(url="/posts", status_code=302)

    chosen = [upload for upload in files if upload.filename]
    if not chosen:
        return back_to_post(post_id, media_error="Файлы не выбраны.")

    limit = media_storage.MAX_ITEMS_PER_POST
    room = limit - len(post.media_items)
    if room <= 0:
        return back_to_post(post_id, media_error=f"В посте уже {limit} файлов — больше Telegram в альбом не соберёт.")
    if len(chosen) > room:
        return back_to_post(post_id, media_error=f"Влезет ещё {room}: в альбоме Telegram не больше {limit} файлов.")

    storage = media_storage.MediaStorageService()
    saved: list[dict] = []
    try:
        for upload in chosen:
            saved.append(await storage.save_upload(upload, post.source_id, post.id, db))
    except media_storage.UploadRejected as exc:
        # Всё или ничего: половина загруженного альбома хуже, чем понятный отказ.
        _discard(saved)
        return back_to_post(post_id, media_error=str(exc))
    except Exception:
        # Кончилось место, сорвался диск — файлов в базе быть не должно, а
        # недописанным огрызкам на диске тем более.
        _discard(saved)
        raise

    last = max((item.sort_order for item in post.media_items), default=-1)
    for offset, item in enumerate(saved, start=1):
        db.add(MediaItem(
            raw_post_id=post.id,
            # Сообщения в Telegram у такого файла нет, и восстановление его не ищет.
            telegram_message_id=0,
            media_type=item["media_type"],
            file_path=item["path"],
            file_size=item["file_size"],
            mime_type=item["mime_type"],
            sort_order=last + offset,
            origin=MediaOrigin.MANUAL.value,
        ))
    db.flush()
    _sync_media_counters(db, post)
    db.add(ActionLog(
        action="media_added",
        entity_type="RawPost",
        entity_id=str(post.id),
        message=f"Добавлено файлов вручную: {len(saved)}",
    ))
    db.commit()
    return back_to_post(post_id)


@router.post("/posts/{post_id}/media/{item_id}/delete")
def delete_post_media(post_id: int, item_id: int, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    post = _media_post(db, post_id)
    item = db.get(MediaItem, item_id)
    if not post or not item or item.raw_post_id != post_id:
        return back_to_post(post_id)

    Path(item.file_path).unlink(missing_ok=True)
    db.delete(item)
    db.flush()
    _sync_media_counters(db, post)
    db.add(ActionLog(
        action="media_deleted",
        entity_type="RawPost",
        entity_id=str(post.id),
        message=f"Удалён файл #{item_id}",
    ))
    db.commit()
    return back_to_post(post_id)


@router.post("/posts/{post_id}/media/{item_id}/move")
def move_post_media(
    post_id: int,
    item_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """Порядок файлов = порядок в альбоме, а первый ещё и несёт подпись."""
    post = _media_post(db, post_id)
    if not post:
        return back_to_post(post_id)

    if direction not in ("up", "down"):
        return back_to_post(post_id)

    items = sorted(post.media_items, key=lambda m: (m.sort_order, m.id))
    current = next((index for index, m in enumerate(items) if m.id == item_id), None)
    if current is None:
        return back_to_post(post_id)

    neighbour = current - 1 if direction == "up" else current + 1
    if 0 <= neighbour < len(items):
        items[current], items[neighbour] = items[neighbour], items[current]
    # Перенумеровываем целиком: часть постов приехала с одинаковым sort_order,
    # и обмен значениями на них не работал бы.
    for order, item in enumerate(items):
        item.sort_order = order
    db.commit()
    return back_to_post(post_id)
