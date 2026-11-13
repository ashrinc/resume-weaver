"""POST /api/tailor: run the tailoring pipeline (see app.tailor.pipeline)."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.errors import InternalPipelineError, TailorDeadlineError
from app.api.schemas import ErrorResponse
from app.llm.errors import GeminiError
from app.tailor.models import TailorRequest, TailorResult
from app.tailor.pipeline import PipelineInputError, TailorPipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tailor"])


def get_pipeline(request: Request) -> TailorPipeline:
    return request.app.state.tailor_pipeline


@router.post(
    "/tailor",
    response_model=TailorResult,
    responses={
        400: {"model": ErrorResponse, "description": "Input cannot be tailored"},
        422: {"model": ErrorResponse, "description": "Empty or missing field"},
        500: {"model": ErrorResponse, "description": "Unexpected or configuration error"},
        502: {"model": ErrorResponse, "description": "Gemini timeout, rate limit or bad response"},
        504: {"model": ErrorResponse, "description": "Overall deadline exceeded (gemini_deadline)"},
    },
)
async def tailor(
    request: Request,
    body: TailorRequest,
    pipeline: Annotated[TailorPipeline, Depends(get_pipeline)],
) -> TailorResult:
    deadline: float = request.app.state.tailor_deadline_seconds
    try:
        # One overall limit (TAILOR_DEADLINE_SECONDS), including LLM retries and
        # backoff. On expiry the run is cancelled, so nothing is cached.
        return await asyncio.wait_for(
            pipeline.run(body.latex_source, body.job_description), timeout=deadline
        )
    except TimeoutError:
        # GeminiError does not subclass TimeoutError, so this is only the deadline.
        logger.warning("tailor: deadline of %gs exceeded; run cancelled", deadline)
        raise TailorDeadlineError(deadline) from None
    except (PipelineInputError, GeminiError):
        raise  # mapped to 400 / 5xx by app.api.errors
    except Exception as exc:
        # Log the type and stack frames only: exception messages can contain
        # fragments of the user's resume or job description.
        frames = "".join(traceback.format_tb(exc.__traceback__))
        logger.error("tailor: unexpected %s\n%s", type(exc).__name__, frames)
        raise InternalPipelineError() from None
