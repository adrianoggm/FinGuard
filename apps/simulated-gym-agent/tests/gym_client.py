"""Test support: a client built the way the A2A gateway builds its own."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_text_part
from a2a.types import Message, Role, SendMessageRequest, Task, TaskState, TaskStatus
from a2a.utils import TransportProtocol
from fastapi import FastAPI
from google.protobuf.struct_pb2 import Struct

from finguard_simulated_gym.app import build_app
from finguard_simulated_gym.config import Settings

PUBLIC_URL = "http://127.0.0.1:9101"
TODAY = date(2026, 9, 30)  # tomorrow, Thursday 1 October, carries the demo anchor


def settings_for(style: str) -> Settings:
    return Settings(
        style=style,  # type: ignore[arg-type]
        a2a_url=f"http://gym-{style}:8000",
        public_url=PUBLIC_URL,
        api_key="k",
        bearer_token="b",
        today=TODAY,
    )


def auth_headers(style: str) -> dict[str, str]:
    return {"X-Gym-Key": "k"} if style == "norte" else {"Authorization": "Bearer b"}


@dataclass
class Gym:
    app: FastAPI
    style: str
    http: httpx.AsyncClient
    client: Any

    async def ask(
        self,
        *,
        text: str,
        skill: str,
        data: dict[str, Any] | None = None,
        message_id: str | None = None,
        context_id: str | None = None,
    ) -> Task:
        metadata = Struct()
        metadata.update({"finguardSkill": skill})
        parts = [new_text_part(text)] + ([new_data_part(data)] if data else [])
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=message_id or uuid4().hex,
                context_id=context_id or "",
                parts=parts,
                metadata=metadata,
            )
        )
        responses = [r async for r in self.client.send_message(request)]
        assert len(responses) == 1
        if responses[0].WhichOneof("payload") == "task":
            task: Task = responses[0].task
            return task
        return Task(
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=responses[0].message)
        )

    async def link(self, member: str, socio: str = "1234") -> httpx.Response:
        return await self.http.post("/vincular", data={"ref": member, "socio": socio})


@asynccontextmanager
async def open_gym(style: str) -> AsyncIterator[Gym]:
    app = build_app(settings_for(style))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"http://gym-{style}:8000",
        headers=auth_headers(style),
    ) as http:
        client = ClientFactory(
            ClientConfig(
                streaming=False,
                httpx_client=http,
                supported_protocol_bindings=[TransportProtocol.JSONRPC],
            )
        ).create(app.state.card)
        yield Gym(app, style, http, client)


def data_of(task: Task) -> dict[str, Any]:
    [artifact] = task.artifacts
    [part] = get_data_parts(list(artifact.parts))
    assert isinstance(part, dict)
    return part


def text_of(task: Task) -> str:
    [artifact] = task.artifacts
    [part] = get_text_parts(list(artifact.parts))
    return part
