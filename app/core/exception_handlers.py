from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError

from app.core.responses import error_response, ErrorCodes
from app.core.responses import AppError


def register_exception_handlers(app: FastAPI):

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return error_response(
            ErrorCodes.VALIDATION_ERROR,
            str(exc.errors()),
            422
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return error_response(
            ErrorCodes.NOT_FOUND if exc.status_code == 404 else ErrorCodes.INTERNAL_ERROR,
            str(exc.detail),
            exc.status_code
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        return error_response(
            ErrorCodes.INTERNAL_ERROR,
            f"Unexpected error: {str(exc)}",
            500
        )
