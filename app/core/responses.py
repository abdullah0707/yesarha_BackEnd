from typing import Any, Optional
from fastapi import HTTPException
from fastapi.responses import JSONResponse


# =====================================================
# STANDARD ERROR CODES
# (Dashboard translates these to AR/EN locally)
# =====================================================

class ErrorCodes:
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"

    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    DAILY_LIMIT_REACHED = "DAILY_LIMIT_REACHED"
    MONTHLY_LIMIT_REACHED = "MONTHLY_LIMIT_REACHED"
    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    PLAN_INACTIVE = "PLAN_INACTIVE"

    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    OLLAMA_UNREACHABLE = "OLLAMA_UNREACHABLE"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    EXECUTION_FAILED = "EXECUTION_FAILED"

    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_PROVIDER_ERROR = "PAYMENT_PROVIDER_ERROR"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"

    INTERNAL_ERROR = "INTERNAL_ERROR"


def success(data: Any = None, meta: Optional[dict] = None) -> dict:
    payload = {"status": "success", "data": data}
    if meta is not None:
        payload["meta"] = meta
    return payload


def paginated(items: list, page: int, limit: int, total: int) -> dict:
    total_pages = (total + limit - 1) // limit if limit > 0 else 1
    return success(
        data=items,
        meta={
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": max(total_pages, 1)
        }
    )


class AppError(HTTPException):
    """
    Raise this anywhere in the app to return the unified error envelope:
    { "status": "error", "code": "...", "message": "..." }
    """

    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        super().__init__(status_code=status_code, detail=message)


def error_response(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "code": code, "message": message}
    )
