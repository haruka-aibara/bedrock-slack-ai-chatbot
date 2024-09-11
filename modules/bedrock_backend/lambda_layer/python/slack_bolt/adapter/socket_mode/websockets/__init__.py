"""[`websockets`](https://pypi.org/project/websockets/) based implementation  / asyncio compatible"""

import os
from logging import Logger
from time import time
from typing import Optional

from slack_sdk.socket_mode.websockets import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web.async_client import AsyncWebClient

from slack_bolt import App
from slack_bolt.adapter.socket_mode.async_base_handler import AsyncBaseSocketModeHandler
from slack_bolt.adapter.socket_mode.async_internals import (
    send_async_response,
    run_async_bolt_app,
)
from slack_bolt.adapter.socket_mode.internals import run_bolt_app
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.response import BoltResponse


class SocketModeHandler(AsyncBaseSocketModeHandler):
    app: App
    app_token: str
    client: SocketModeClient

    def __init__(
        self,
        app: App,
        app_token: Optional[str] = None,
        logger: Optional[Logger] = None,
        web_client: Optional[AsyncWebClient] = None,
        ping_interval: float = 10,
    ):
        """Socket Mode adapter for Bolt apps.

        Please note that this adapter does not support proxy configuration
        as the underlying websockets module does not support proxy-wired connections.
        If you use proxy, consider using one of the other Socket Mode adapters.

        Args:
            app: The Bolt app
            app_token: App-level token starting with `xapp-`
            logger: Custom logger
            web_client: custom `slack_sdk.web.WebClient` instance
            ping_interval: The ping-pong internal (seconds)
        """
        self.app = app
        self.app_token = app_token or os.environ["SLACK_APP_TOKEN"]
        self.client = SocketModeClient(
            app_token=self.app_token,
            logger=logger if logger is not None else app.logger,
            web_client=web_client if web_client is not None else app.client,  # type: ignore[arg-type]
            ping_interval=ping_interval,
        )
        self.client.socket_mode_request_listeners.append(self.handle)  # type: ignore[arg-type]

    async def handle(self, client: SocketModeClient, req: SocketModeRequest) -> None:  # type: ignore[override]
        start = time()
        bolt_resp: BoltResponse = run_bolt_app(self.app, req)
        await send_async_response(client, req, bolt_resp, start)


class AsyncSocketModeHandler(AsyncBaseSocketModeHandler):
    app: AsyncApp
    app_token: str
    client: SocketModeClient

    def __init__(
        self,
        app: AsyncApp,
        app_token: Optional[str] = None,
        logger: Optional[Logger] = None,
        web_client: Optional[AsyncWebClient] = None,
        ping_interval: float = 10,
    ):
        self.app = app
        self.app_token = app_token or os.environ["SLACK_APP_TOKEN"]
        self.client = SocketModeClient(
            app_token=self.app_token,
            logger=logger if logger is not None else app.logger,
            web_client=web_client if web_client is not None else app.client,
            ping_interval=ping_interval,
        )
        self.client.socket_mode_request_listeners.append(self.handle)  # type: ignore[arg-type]

    async def handle(self, client: SocketModeClient, req: SocketModeRequest) -> None:  # type: ignore[override]
        start = time()
        bolt_resp: BoltResponse = await run_async_bolt_app(self.app, req)
        await send_async_response(client, req, bolt_resp, start)