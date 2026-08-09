"""CLI version endpoint (`GET /api/v1/cli/version`)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from ..config import settings
from ..schemas import CliVersionResponse

router = APIRouter()


@router.get("/api/v1/cli/version", response_model=CliVersionResponse)
async def cli_version(response: Response) -> CliVersionResponse:
    """Report the latest and minimum-supported CLI versions.

    Pure function of the ``cli_*`` settings; cacheable by clients and CDNs.
    """
    response.headers["Cache-Control"] = "public, max-age=300"
    return CliVersionResponse(
        latest=settings.cli_latest_version,
        min_supported=settings.cli_min_supported_version,
        released_at=settings.cli_released_at,
        release_url=settings.cli_release_url,
        install_command=settings.cli_install_command,
    )
