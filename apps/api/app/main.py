import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from adapters.llm.errors import LlmProviderError
from apps.api.app.api.v1 import (
    actions,
    automation,
    browser,
    conversations,
    decisions,
    jobs,
    knowledge,
    profiles,
    recommendations,
    resumes,
    scheduling,
    strategies,
    system,
)
from apps.api.app.core.config import get_settings
from apps.api.app.services.errors import (
    DependencyUnavailableError,
    ResourceNotFoundError,
    VersionConflictError,
)

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def enforce_local_access(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    token = settings.api_access_token
    client_host = request.client.host if request.client else ""
    is_local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
    if request.url.path.startswith("/api/") and not is_local and token is None:
        return _error("LOCAL_ACCESS_DENIED", "API 仅允许本机访问", 403)
    if token is not None and request.url.path.startswith("/api/"):
        expected = token.get_secret_value()
        provided = request.headers.get("X-Local-Access-Token", "")
        if provided != expected:
            return _error("LOCAL_ACCESS_DENIED", "本地访问令牌无效", 401)
    return await call_next(request)

for router in (profiles.router, strategies.router, jobs.router, decisions.router,
               knowledge.router, resumes.router, conversations.router, browser.router,
               actions.router, automation.router, recommendations.router,
               scheduling.router, system.router):
    app.include_router(router, prefix="/api/v1")


def _error(code: str, message: str, status: int) -> JSONResponse:
    request_id = str(uuid.uuid4())
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                                                                 "details": {}, "request_id": request_id}})


@app.exception_handler(ResourceNotFoundError)
def not_found(_: Request, exc: ResourceNotFoundError) -> JSONResponse:
    return _error("RESOURCE_NOT_FOUND", str(exc), 404)


@app.exception_handler(VersionConflictError)
def version_conflict(_: Request, exc: VersionConflictError) -> JSONResponse:
    return _error("VERSION_CONFLICT", str(exc), 409)


@app.exception_handler(ValueError)
def invalid_request(_: Request, exc: ValueError) -> JSONResponse:
    return _error("INVALID_REQUEST", str(exc), 400)


@app.exception_handler(DependencyUnavailableError)
def dependency_unavailable(_: Request, exc: DependencyUnavailableError) -> JSONResponse:
    return _error("DEPENDENCY_UNAVAILABLE", str(exc), 503)


@app.exception_handler(LlmProviderError)
def llm_unavailable(_: Request, exc: LlmProviderError) -> JSONResponse:
    return _error(exc.code, str(exc), 503)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
