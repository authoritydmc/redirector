"""FastAPI router for QR code generation (/api/v1/qr and /qr/{pattern})."""

from __future__ import annotations

import asyncio
import base64
import io
import re

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from backend.core.errors import AppError

router = APIRouter(tags=["qr"])


class QRCodeResponse(BaseModel):
    pattern: str
    target_url: str
    qr_base64: str


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(pattern: str) -> str:
    """Header-safe download name: path input must never reach headers raw
    (quotes/newlines would split the header or 500 on validation)."""
    cleaned = _FILENAME_SAFE.sub("_", pattern.replace("/", "_")).strip("._")
    return f"{cleaned or 'shortcut'}_qr.png"


def _generate_qr_png_bytes(content: str) -> bytes:
    try:
        import qrcode
        img = qrcode.make(content)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        raise AppError(
            "QR generation failed",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"QR generation failed: {exc}",
            code="qr:generation-failed",
        ) from None


@router.get("/qr/{pattern:path}", summary="Generate QR code PNG image")
async def get_qr_image(pattern: str, request: Request) -> Response:
    """Return raw PNG binary for the shortcut QR code."""
    base_url = str(request.base_url).rstrip("/")
    target_url = f"{base_url}/{pattern.lstrip('/')}"
    # qrcode+PIL are sync CPU work — keep the event loop free.
    png_bytes = await asyncio.to_thread(_generate_qr_png_bytes, target_url)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{_safe_filename(pattern)}"'},
    )


@router.get("/api/v1/qr", response_model=QRCodeResponse, summary="Generate QR code as JSON base64")
async def get_qr_json(request: Request, pattern: str = Query(..., description="Shortcut pattern")) -> QRCodeResponse:
    """Return base64-encoded PNG and URL metadata."""
    base_url = str(request.base_url).rstrip("/")
    target_url = f"{base_url}/{pattern.lstrip('/')}"
    png_bytes = await asyncio.to_thread(_generate_qr_png_bytes, target_url)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    return QRCodeResponse(pattern=pattern, target_url=target_url, qr_base64=b64_str)
