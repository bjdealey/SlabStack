"""Card image storage.

Files live on disk under ``data/media/cards/<card_id>/`` and the database keeps
only metadata plus a *relative* path. Relative matters: the user can move or
back up their whole data directory and nothing breaks.

Uploads are validated by decoding them with Pillow rather than by trusting the
declared content type, then re-saved. Anything that is not a real image fails
before it reaches disk.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.enums import ImageSide
from app.models import Card, CardImage

EXTENSION_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class ImageValidationError(ValueError):
    pass


def _card_dir(card_id: str) -> Path:
    path = settings.media_dir / "cards" / card_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def absolute_path(relative: str) -> Path:
    return settings.media_dir / relative


def store_image(
    db: Session,
    card: Card,
    *,
    content: bytes,
    original_filename: str | None,
    side: str = ImageSide.FRONT.value,
    caption: str | None = None,
) -> CardImage:
    if not content:
        raise ImageValidationError("The uploaded file is empty.")
    if len(content) > settings.max_image_bytes:
        limit_mb = settings.max_image_bytes / (1024 * 1024)
        raise ImageValidationError(f"Image is larger than the {limit_mb:.0f} MB limit.")

    try:
        with Image.open(io.BytesIO(content)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageValidationError("That file is not a readable image.") from exc

    image_format = (image.format or "JPEG").upper()
    if MIME_BY_FORMAT.get(image_format) not in settings.allowed_image_types:
        allowed = ", ".join(sorted(settings.allowed_image_types))
        raise ImageValidationError(f"Unsupported image type {image_format}. Allowed: {allowed}.")

    record = CardImage(
        card_id=card.id,
        side=side,
        file_path="",
        mime_type=MIME_BY_FORMAT[image_format],
        original_filename=(original_filename or "")[:255] or None,
        caption=caption,
        width=image.width,
        height=image.height,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    db.add(record)
    db.flush()  # assigns the id used in the filename

    directory = _card_dir(card.id)
    extension = EXTENSION_BY_FORMAT.get(image_format, ".jpg")
    full_path = directory / f"{record.id}{extension}"
    full_path.write_bytes(content)
    record.file_path = str(full_path.relative_to(settings.media_dir))

    thumbnail = image.copy()
    thumbnail.thumbnail((settings.thumbnail_max_px, settings.thumbnail_max_px))
    if thumbnail.mode not in ("RGB", "L"):
        thumbnail = thumbnail.convert("RGB")
    thumbnail_path = directory / f"{record.id}_thumb.jpg"
    thumbnail.save(thumbnail_path, format="JPEG", quality=85, optimize=True)
    record.thumbnail_path = str(thumbnail_path.relative_to(settings.media_dir))

    existing = db.scalars(
        select(CardImage).where(CardImage.card_id == card.id, CardImage.side == side)
    ).all()
    record.sort_order = max((img.sort_order for img in existing if img.id != record.id), default=-1) + 1
    if not any(img.is_primary for img in existing if img.id != record.id):
        record.is_primary = True

    db.flush()
    return record


def set_primary(db: Session, image: CardImage) -> CardImage:
    siblings = db.scalars(
        select(CardImage).where(CardImage.card_id == image.card_id, CardImage.side == image.side)
    ).all()
    for sibling in siblings:
        sibling.is_primary = sibling.id == image.id
    db.flush()
    return image


def delete_files(image: CardImage) -> None:
    """Remove an image's files from disk, leaving the row to the caller."""
    for relative in (image.file_path, image.thumbnail_path):
        if not relative:
            continue
        absolute_path(relative).unlink(missing_ok=True)


def delete_card_media(card: Card) -> None:
    """Delete every file belonging to a card, plus its now-empty directory.

    Rows are left to the ``card_images`` cascade — deleting them here as well
    would have the ORM try to delete them twice.
    """
    for image in card.images:
        delete_files(image)
    directory = settings.media_dir / "cards" / card.id
    if directory.exists() and not any(directory.iterdir()):
        directory.rmdir()


def delete_image(db: Session, image: CardImage) -> None:
    was_primary = image.is_primary
    card_id, side = image.card_id, image.side
    delete_files(image)
    db.delete(image)
    db.flush()

    if was_primary:
        replacement = db.scalars(
            select(CardImage)
            .where(CardImage.card_id == card_id, CardImage.side == side)
            .order_by(CardImage.sort_order)
        ).first()
        if replacement is not None:
            replacement.is_primary = True
            db.flush()


def primary_image(card: Card, side: str = ImageSide.FRONT.value) -> CardImage | None:
    candidates = [img for img in card.images if img.side == side]
    if not candidates:
        return None
    for image in candidates:
        if image.is_primary:
            return image
    return sorted(candidates, key=lambda i: i.sort_order)[0]
