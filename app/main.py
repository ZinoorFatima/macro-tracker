"""Application entry point: configuration, routers, and the static SPA mount."""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from .db import init_db  # noqa: E402
from .routers import activities, days, meals, profile  # noqa: E402
from .validation import ValidationProblem  # noqa: E402

app = FastAPI(
    title="Macro Tracker",
    description="Macro and calorie tracking with Claude-powered meal analysis.",
    version="1.0.0",
)

init_db()

app.include_router(profile.router, prefix="/api")
app.include_router(meals.router, prefix="/api")
app.include_router(activities.router, prefix="/api")
app.include_router(days.router, prefix="/api")


@app.exception_handler(ValidationProblem)
def handle_validation_problem(request: Request, exc: ValidationProblem) -> JSONResponse:
    """Surface a domain rule violation as a 422 the frontend already renders.

    Pydantic turns these into 422s on its own when they come from a model
    validator; this covers the ones raised directly from a router.
    """
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    """Liveness plus whether the AI features are configured.

    The frontend calls this on load to decide whether to enable the chat panes,
    so a missing key degrades the app instead of breaking it.
    """
    return {"db": True, "api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """The page declares an inline SVG icon, but browsers probe this path anyway."""
    return Response(status_code=204)


class NoCacheStaticFiles(StaticFiles):
    """Serve the SPA without client-side caching.

    The app ships unversioned asset filenames (`/js/app.js`, not
    `/js/app.8f3c.js`), so a cached copy survives a redeploy and the user gets a
    new `index.html` wired to stale scripts. Revalidating every request costs
    nothing on a handful of small local files and removes that whole class of
    "works after a hard refresh" bug.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/", NoCacheStaticFiles(directory=ROOT / "static", html=True), name="static")
