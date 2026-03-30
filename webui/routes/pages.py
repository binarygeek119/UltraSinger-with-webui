"""Server-rendered HTML pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from webui import __version__
from webui.config import WHISPER_COMPUTE_TYPE_OPTIONS

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def page_home(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "home.html",
        {"title": "Home", "webui_version": __version__},
    )


@router.get("/jobs", response_class=HTMLResponse)
def page_jobs(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "jobs.html",
        {"title": "Jobs", "webui_version": __version__},
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def page_job_detail(request: Request, job_id: str) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "job_detail.html",
        {
            "title": f"Job {job_id}",
            "job_id": job_id,
            "webui_version": __version__,
        },
    )


@router.get("/downloads", response_class=HTMLResponse)
def page_downloads(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "downloads.html",
        {"title": "Downloads", "webui_version": __version__},
    )


@router.get("/exported-songs", response_class=HTMLResponse)
def page_exported_songs(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "exported_songs.html",
        {"title": "Exported Songs", "webui_version": __version__},
    )


@router.get("/lyrics-compare", response_class=HTMLResponse)
def page_lyrics_compare(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "lyrics_compare.html",
        {"title": "Lyrics compare", "webui_version": __version__},
    )


@router.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "settings.html",
        {
            "title": "Settings",
            "webui_version": __version__,
            "whisper_compute_type_options": WHISPER_COMPUTE_TYPE_OPTIONS,
        },
    )


@router.get("/about", response_class=HTMLResponse)
def page_about(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "about.html",
        {"title": "About", "webui_version": __version__},
    )
