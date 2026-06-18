from fastapi import APIRouter, HTTPException, status

from app.modules.parser.schemas import ParsedMetadata, ParseUrlRequest
from app.modules.parser.service import ParserError, parse_url


router = APIRouter()


@router.post("/parse-url", response_model=ParsedMetadata)
async def parse_url_endpoint(payload: ParseUrlRequest) -> ParsedMetadata:
    try:
        return await parse_url(str(payload.url))
    except ParserError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
