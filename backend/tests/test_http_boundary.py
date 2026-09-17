"""HTTP 边界回归：实际分块限额和安全响应。"""

import asyncio

import httpx
from fastapi import FastAPI, Request

from app.gateway.middleware import install_middleware
from app.settings import Settings


def test_streamed_body_limit():
    async def scenario():
        app = FastAPI()
        install_middleware(app, Settings(max_body_bytes=8))
        seen = []

        @app.post("/api/v1/echo")
        async def echo(request: Request):
            body = await request.body()
            seen.append(body)
            return {"size": len(body)}

        async def chunks(*values):
            for value in values:
                yield value

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as client:
            for headers in ({}, {"content-length": "1"}):
                response = await client.post("/api/v1/echo", headers=headers, content=chunks(b"12345", b"6789"))
                assert response.status_code == 413
                assert response.json()["error"]["code"] == "payload_too_large"
                assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
                assert response.headers["x-content-type-options"] == "nosniff"
            assert seen == []
            response = await client.post("/api/v1/echo", content=chunks(b"1234", b"5678"))
            assert response.status_code == 200
            assert seen == [b"12345678"]

    asyncio.run(scenario())


def test_rejection_headers_and_request_ids():
    async def scenario():
        app = FastAPI()
        install_middleware(app, Settings())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as client:
            for incoming in ("trace\tforged", "trace\nforged", "x" * 65):
                response = await client.post(
                    "/api/v1/missing", headers={"x-request-id": incoming, "origin": "https://outside.example"}
                )
                assert response.status_code == 403
                returned = response.headers["x-request-id"]
                assert returned != incoming
                assert response.json()["error"]["request_id"] == returned
                assert response.headers["cache-control"] == "no-store"
                assert "Content-Security-Policy" in response.headers
            response = await client.post(
                "/api/v1/missing", headers={"content-length": "9" * 5000, "x-request-id": "trace-123.valid"}
            )
            assert response.status_code == 413
            assert response.headers["x-request-id"] == "trace-123.valid"
            response = await client.post("/api/missing", headers={"content-length": "99999"})
            assert response.json() == {"detail": "请求过长。"}

    asyncio.run(scenario())
