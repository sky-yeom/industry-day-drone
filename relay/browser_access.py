"""Permit browser access only from explicitly trusted dashboard origins."""

import re

from starlette.responses import JSONResponse


class BrowserAccessMiddleware:
    def __init__(self, app, *, origins, origin_regex):
        self.app = app
        self.origins = frozenset(origins)
        self.pattern = re.compile(origin_regex)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_origin = headers.get(b"origin")
        origin = raw_origin.decode("latin-1") if raw_origin is not None else None
        allowed = origin is None or origin in self.origins or self.pattern.fullmatch(origin) is not None
        if not allowed:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Dashboard origin is not allowed"}, status_code=403)(
                    scope, receive, send)
            return

        await self.app(scope, receive, send)
