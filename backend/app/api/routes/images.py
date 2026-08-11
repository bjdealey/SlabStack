"""Card image upload, retrieval and management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import CardDep, DbSession, ImageDep
from app.api.errors import ApiError, NotFoundError
from app.enums import ImageSide
from app.schemas.card import CardImageOut, CardImageUpdate
from app.services import images_service
from app.services.images_service import ImageValidationError

router = APIRouter(tags=["images"])


@router.post(
    "/cards/{card_id}/images",
    response_model=list[CardImageOut],
    status_code=status.HTTP_201_CREATED,
    summary="Upload one or more images for a card",
)
async def upload_images(
    db: DbSession,
    card: CardDep,
    files: Annotated[list[UploadFile], File(description="JPEG, PNG or WebP.")],
    side: Annotated[str, Form()] = ImageSide.FRONT.value,
    caption: Annotated[str | None, Form()] = None,
) -> list[CardImageOut]:
    if side not in ImageSide.values():
        raise ApiError("invalid_side", f"side must be one of {ImageSide.values()}")

    stored = []
    for upload in files:
        content = await upload.read()
        try:
            image = images_service.store_image(
                db,
                card,
                content=content,
                original_filename=upload.filename,
                side=side,
                caption=caption,
            )
        except ImageValidationError as exc:
            raise ApiError(
                "invalid_image", str(exc), details={"filename": upload.filename}
            ) from exc
        stored.append(CardImageOut.from_model(image))
    return stored


@router.get("/cards/{card_id}/images", response_model=list[CardImageOut], summary="List card images")
def list_images(card: CardDep) -> list[CardImageOut]:
    return [CardImageOut.from_model(image) for image in card.images]


@router.get("/images/{image_id}/file", summary="Download the full-size image")
def get_file(image: ImageDep) -> FileResponse:
    path = images_service.absolute_path(image.file_path)
    if not path.exists():
        raise NotFoundError("Image file", image.id)
    return FileResponse(path, media_type=image.mime_type)


@router.get("/images/{image_id}/thumbnail", summary="Download the thumbnail")
def get_thumbnail(image: ImageDep) -> FileResponse:
    if not image.thumbnail_path:
        raise NotFoundError("Thumbnail", image.id)
    path = images_service.absolute_path(image.thumbnail_path)
    if not path.exists():
        raise NotFoundError("Thumbnail file", image.id)
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/images/{image_id}", response_model=CardImageOut, summary="Update image metadata")
def update_image(db: DbSession, image: ImageDep, payload: CardImageUpdate) -> CardImageOut:
    changes = payload.model_dump(exclude_unset=True)
    make_primary = changes.pop("is_primary", None)
    for field, value in changes.items():
        setattr(image, field, value)
    db.flush()
    if make_primary:
        images_service.set_primary(db, image)
    return CardImageOut.from_model(image)


@router.delete(
    "/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an image"
)
def delete_image(db: DbSession, image: ImageDep) -> Response:
    images_service.delete_image(db, image)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
