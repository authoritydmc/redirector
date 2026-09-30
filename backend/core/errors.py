"""Shared error envelope (RFC 7807 problem details).

EPIC-01: routes raise `AppError`; FastAPI handlers registered in
`backend/main.py` render it as `application/problem+json`.
Plain `HTTPException` keeps default FastAPI rendering for compat.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Domain error with an HTTP status and machine-readable code."""

    def __init__(
        self,
        title: str,
        status: int = 400,
        detail: str = "",
        code: str = "",
        *,
        suggestions: list[Any] | None = None,
        missing_params: list[str] | None = None,
    ) -> None:
        super().__init__(title)
        self.title = title
        self.status = status
        self.detail = detail
        self.code = code or f"error:{status}"
        self.suggestions = suggestions or []
        self.missing_params = missing_params or []

    def to_problem(self) -> dict[str, Any]:
        return {
            "type": "about:blank",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
            "suggestions": self.suggestions,
            "missing_params": self.missing_params,
        }


async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_problem(),
        media_type="application/problem+json",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
