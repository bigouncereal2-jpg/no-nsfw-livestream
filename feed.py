import asyncio
from http import HTTPStatus
from pathlib import Path
from threading import Event

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from pipeline import PORT, STREAM_URL


class FeedServer:
    def __init__(self, gate):
        self.gate = gate
        self.ready = Event()
        self.error = None
        self.html = (Path(__file__).resolve().parent / "source.html").read_text(encoding="utf-8")

    def run(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self.error = str(exc)
            self.ready.clear()
            self.gate.blackout("OBS source disconnected.")

    async def _serve(self):
        async with serve(
            self._client,
            "127.0.0.1",
            PORT,
            compression=None,
            max_size=32,
            max_queue=2,
            close_timeout=0.15,
            process_request=self._request,
        ):
            self.ready.set()
            await asyncio.Future()

    def _request(self, connection, request):
        if request.headers.get_all("Host") != [f"127.0.0.1:{PORT}"]:
            return connection.respond(HTTPStatus.FORBIDDEN, "Local OBS address required.\n")
        if request.path == "/feed":
            if request.headers.get_all("Origin") != [STREAM_URL.rstrip("/")]:
                return connection.respond(HTTPStatus.FORBIDDEN, "Local OBS page required.\n")
            return None
        if request.path != "/":
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found.\n")

        response = connection.respond(HTTPStatus.OK, self.html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            f"connect-src ws://127.0.0.1:{PORT}; frame-ancestors 'none'"
        )
        return response

    async def _client(self, socket):
        read = asyncio.create_task(socket.recv())
        last_sequence = 0
        showing_black = False
        awaiting_ack = False
        try:
            while True:
                if read.done():
                    if read.result() != "ready":
                        return
                    awaiting_ack = False
                    read = asyncio.create_task(socket.recv())

                _, _, frame, _, _ = self.gate.snapshot()
                if frame is None and not showing_black:
                    await asyncio.wait_for(socket.send("black"), timeout=0.15)
                    showing_black = True
                if frame is not None and not awaiting_ack and frame.sequence != last_sequence:
                    await asyncio.wait_for(socket.send(frame.jpeg), timeout=0.15)
                    last_sequence = frame.sequence
                    awaiting_ack = True
                    showing_black = False
                await asyncio.sleep(0.005)
        except (ConnectionClosed, TimeoutError):
            return
        finally:
            read.cancel()
            await asyncio.gather(read, return_exceptions=True)
