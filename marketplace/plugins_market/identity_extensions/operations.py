# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Safe operation logging at configured identity channel boundaries."""

from contextlib import nullcontext

import httpx
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from plugins_market.core.errors import BusinessError, normalize_http_exception_detail
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    bind_operation_actor,
    bind_operation_resource,
    complete_operation_result,
    get_operation_type,
    has_operation_completion_log,
    operation_context,
    operation_failure_result,
    operation_log_fields,
    safe_error_summary,
)
from starlette.exceptions import HTTPException

from .contracts import IdentityAdapter, IdentityError
from .registry import BUILTIN_PROVIDERS, provider_log_id

logger = get_logger(__name__)


def oauth_operation_context(operation_type: str, *, extension: bool):
    """Reuse the extension route scope; built-ins retain their existing scope."""
    if extension and get_operation_type() == operation_type:
        return nullcontext()
    return operation_context(operation_type=operation_type)


def _extension_provider(request: Request) -> str | None:
    provider = request.path_params.get("provider")
    if provider is None and request.scope["route"].name == "auth_me":
        provider = (request.headers.get("X-OAuth-Provider") or "").strip().lower()
    if not provider or provider in BUILTIN_PROVIDERS:
        return None
    return provider_log_id(provider)


def _internal_error() -> BusinessError:
    return BusinessError(
        code=500,
        status_code=500,
        error="internal_error",
        message="Identity authentication failed; please sign in again",
        error_code="SKILLHUB_INTERNAL_UNEXPECTED",
        error_class="internal",
    )


def _failure_details(
    exc: BusinessError | HTTPException | RequestValidationError,
) -> tuple[int, dict]:
    if isinstance(exc, RequestValidationError):
        return 422, {
            "error_code": "SKILLHUB_VALIDATION_FAILED",
            "error_class": "validation",
        }
    if isinstance(exc, BusinessError):
        return exc.status_code, exc.detail
    return exc.status_code, normalize_http_exception_detail(exc.status_code, exc.detail)


def _log_failure(
    provider: str, exc: BusinessError | HTTPException | RequestValidationError
) -> None:
    if has_operation_completion_log():
        return
    status_code, payload = _failure_details(exc)
    result = operation_failure_result(payload)
    logger.warning(
        "identity operation failed",
        **complete_operation_result(
            result=result.result,
            error_code=result.error_code,
            error_class=result.error_class,
            error_message=safe_error_summary(
                error_code=result.error_code,
                error_class=result.error_class,
            ),
            provider=provider,
            http_status=status_code,
        ),
    )


class ExtensionOAuthRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            provider = _extension_provider(request)
            if provider is None:
                return await original(request)
            operation_type = "oauth_me" if self.name == "auth_me" else self.name
            with operation_context(operation_type=operation_type):
                bind_operation_actor(actor_type="anonymous")
                bind_operation_resource(
                    resource_type="oauth_provider", resource_id=provider
                )
                try:
                    return await original(request)
                except (BusinessError, HTTPException, RequestValidationError) as exc:
                    _log_failure(provider, exc)
                    raise
                except Exception:  # noqa: BLE001 -- convert optional plugin/transaction faults safely
                    # Never let an arbitrary plugin/transaction exception reach traceback logging.
                    error = _internal_error()
                    _log_failure(provider, error)
                    raise error from None

        return handler


async def invoke_adapter(
    adapter: IdentityAdapter, provider: str, method: str, *args, **kwargs
):
    """Normalize plugin exceptions without logging credentials or upstream bodies."""
    try:
        return await getattr(adapter, method)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- plugin exceptions must not escape to traceback logging
        logger.warning(
            "identity adapter call failed",
            **operation_log_fields(
                stage="adapter_call",
                result="failure",
                provider=provider_log_id(provider),
                phase=method,
                error_type=type(exc).__name__,
            ),
        )
        if isinstance(exc, (IdentityError, httpx.HTTPError)):
            raise IdentityError("Identity adapter operation failed") from None
        raise _internal_error() from None
