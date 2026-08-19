"""Streaming passthrough of OpenAI-style requests to an engine container.

Shared by the /v1 model router (sessions) and /api/engines/chat (PWA chat).
AirLLM can take minutes-to-hours per reply (PLAN §6.2) — no read timeout.
"""

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

CHAT_TIMEOUT = httpx.Timeout(connect=10, read=None, write=30, pool=10)


async def proxy_openai_request(
    base_url: str, path: str, body: bytes
) -> Response | StreamingResponse:
    url = f"{base_url}/{path.lstrip('/')}"
    client = httpx.AsyncClient(timeout=CHAT_TIMEOUT)
    try:
        upstream_request = client.build_request(
            "POST", url, content=body, headers={"content-type": "application/json"}
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"engine unreachable: {exc}") from exc

    content_type = upstream.headers.get("content-type", "application/json")
    if "text/event-stream" in content_type:
        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(), status_code=upstream.status_code, media_type="text/event-stream"
        )

    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
        await client.aclose()
    return Response(content, status_code=upstream.status_code, media_type=content_type)
