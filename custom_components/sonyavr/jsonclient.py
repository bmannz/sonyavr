"""Async client for the Sony ES-series JSON control protocol (TCP port 33336).

Found on e.g. the STR-ZA2000ES. The receiver speaks plain JSON objects over a
TCP socket (no delimiter between objects):

    -> {"type":"get","feature":"main.power"}
    <- {"type":"result","feature":"main.power","value":"on"}

    -> {"type":"set","feature":"main.mute","value":"off"}
    <- {"type":"result","value":"ACK"}

    <- {"type":"notify","feature":"main.volumedb","value":-11.5}   (unsolicited)

A "get" for an unknown feature returns value "ERR"; for a feature that exists
but is not available right now (e.g. Zone 2 volume while Zone 2 is off) it
returns "NAK". Neither failure reply carries a "feature" field.

This module has no Home Assistant dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 33336
CONNECT_TIMEOUT = 10.0
REQUEST_TIMEOUT = 3.0
RECONNECT_MIN = 5.0
RECONNECT_MAX = 60.0
PERIODIC_INTERVAL = 30.0
MAX_BUFFER = 1_000_000

ACK = "ACK"
NAK = "NAK"
ERR = "ERR"

# Pseudo feature name passed to listeners when the connection state changes.
CONNECTION = "__connection__"

Listener = Callable[[set[str]], None]


class SonyJsonError(Exception):
    """Base error."""


class SonyJsonConnectionError(SonyJsonError):
    """Not connected, or the connection failed during a request."""


def _default_spawn(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task:
    return asyncio.get_running_loop().create_task(coro, name=name)


class SonyJsonClient:
    """Persistent connection with push notifications and a state cache."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        spawn: Callable[[Coroutine[Any, Any, Any], str], asyncio.Task] | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
        reconnect_min: float = RECONNECT_MIN,
        periodic_interval: float = PERIODIC_INTERVAL,
    ) -> None:
        self.host = host
        self.port = port
        self.state: dict[str, Any] = {}
        # Features that exist but replied NAK on the last get.
        self.unavailable: set[str] = set()
        # Features the receiver does not know (replied ERR).
        self.unsupported: set[str] = set()
        self.connected = False
        self.on_connect: Callable[[], Awaitable[None]] | None = None
        self.on_periodic: Callable[[], Awaitable[None]] | None = None

        self._spawn = spawn or _default_spawn
        self._request_timeout = request_timeout
        self._reconnect_min = reconnect_min
        self._periodic_interval = periodic_interval
        self._listeners: list[Listener] = []
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future | None = None
        self._buffer = ""
        self._run_task: asyncio.Task | None = None
        self._closing = False
        self._connected_event = asyncio.Event()

    # ------------------------------------------------------------------ public

    def add_listener(self, callback: Listener) -> Callable[[], None]:
        """Register a callback(changed_features); returns a remover."""
        self._listeners.append(callback)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(callback)

        return remove

    async def start(self) -> None:
        if self._run_task is None:
            self._closing = False
            self._run_task = self._spawn(self._run(), "sonyavr json client")

    async def stop(self) -> None:
        self._closing = True
        task, self._run_task = self._run_task, None
        self._drop_connection()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.connected = False
        self._connected_event.clear()

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def get(self, feature: str) -> Any:
        """Read a feature. Returns its value, or None if NAK/ERR."""
        msg = await self._request({"type": "get", "feature": feature})
        if msg.get("feature") == feature:
            value = msg.get("value")
            self.unsupported.discard(feature)
            if self._store(feature, value):
                self._notify({feature})
            return value
        code = msg.get("value")
        if code == NAK:
            self.unsupported.discard(feature)
            changed = feature in self.state or feature not in self.unavailable
            self.state.pop(feature, None)
            self.unavailable.add(feature)
            if changed:
                self._notify({feature})
        else:
            if code == ERR:
                self.unsupported.add(feature)
            _LOGGER.debug("get %s failed: %s", feature, msg)
        return None

    async def set(self, feature: str, value: Any) -> str:
        """Write a feature. Returns the reply code (ACK / NAK / ERR)."""
        msg = await self._request({"type": "set", "feature": feature, "value": value})
        code = msg.get("value")
        if code != ACK:
            _LOGGER.warning("Receiver rejected set %s=%r: %s", feature, value, code)
        return code if isinstance(code, str) else ERR

    # --------------------------------------------------------------- internals

    def _store(self, feature: str, value: Any) -> bool:
        changed = self.state.get(feature, _MISSING) != value or feature in self.unavailable
        self.state[feature] = value
        self.unavailable.discard(feature)
        return changed

    def _notify(self, changed: set[str]) -> None:
        for callback in list(self._listeners):
            try:
                callback(changed)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Listener error")

    def _handle_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "notify":
            feature = msg.get("feature")
            if isinstance(feature, str):
                if self._store(feature, msg.get("value")):
                    self._notify({feature})
        elif kind == "result":
            pending = self._pending
            if pending is not None and not pending.done():
                pending.set_result(msg)
            else:
                _LOGGER.debug("Unexpected result: %s", msg)
        else:
            _LOGGER.debug("Unhandled message: %s", msg)

    def _extract(self) -> list[str]:
        """Pull complete top-level JSON objects out of the buffer."""
        buf = self._buffer
        objects: list[str] = []
        depth = 0
        start = 0
        in_string = False
        escaped = False
        for i, ch in enumerate(buf):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"' and depth > 0:
                in_string = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0:
                    objects.append(buf[start : i + 1])
        self._buffer = buf[start:] if depth > 0 else ""
        if len(self._buffer) > MAX_BUFFER:
            self._buffer = ""
        return objects

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.connected or self._writer is None:
            raise SonyJsonConnectionError("Not connected")
        async with self._lock:
            writer = self._writer
            if not self.connected or writer is None:
                raise SonyJsonConnectionError("Not connected")
            future = asyncio.get_running_loop().create_future()
            self._pending = future
            try:
                writer.write(json.dumps(payload, separators=(",", ":")).encode())
                await writer.drain()
                return await asyncio.wait_for(future, self._request_timeout)
            except TimeoutError:
                _LOGGER.warning("No reply to %s; reconnecting", payload)
                self._drop_connection()
                raise SonyJsonConnectionError("Timed out") from None
            except OSError as err:
                self._drop_connection()
                raise SonyJsonConnectionError(str(err)) from err
            finally:
                self._pending = None

    def _drop_connection(self) -> None:
        # Report "not connected" straight away; the run loop finishes cleanup
        # (and notifies listeners) once the socket has actually closed.
        self.connected = False
        self._connected_event.clear()
        writer = self._writer
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                self._buffer += data.decode("utf-8", "replace")
                for raw in self._extract():
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        _LOGGER.debug("Bad JSON from receiver: %r", raw)
                        continue
                    if isinstance(msg, dict):
                        self._handle_message(msg)
        except (OSError, asyncio.IncompleteReadError):
            return

    async def _maintain(self) -> None:
        """Initial refresh, then a periodic heartbeat/refresh."""
        try:
            if self.on_connect is not None:
                await self.on_connect()
            while True:
                await asyncio.sleep(self._periodic_interval)
                # Doubles as a liveness check: a dead socket times out here.
                await self.get("main.power")
                if self.on_periodic is not None:
                    await self.on_periodic()
        except SonyJsonConnectionError:
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Maintenance task failed")

    async def _run(self) -> None:
        delay = self._reconnect_min
        while not self._closing:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
                )
            except (OSError, TimeoutError) as err:
                _LOGGER.debug("Cannot connect to %s:%s: %s", self.host, self.port, err)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)
                continue

            delay = self._reconnect_min
            _LOGGER.debug("Connected to %s:%s", self.host, self.port)
            self._writer = writer
            self._buffer = ""
            reader_task = asyncio.get_running_loop().create_task(self._read_loop(reader))
            self.connected = True
            self._connected_event.set()
            self._notify({CONNECTION})
            maintain_task = asyncio.get_running_loop().create_task(self._maintain())
            try:
                await reader_task
            finally:
                maintain_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await maintain_task
                self.connected = False
                self._connected_event.clear()
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()
                self._writer = None
                pending = self._pending
                if pending is not None and not pending.done():
                    pending.set_exception(SonyJsonConnectionError("Connection lost"))
                self._notify({CONNECTION})
            if not self._closing:
                _LOGGER.debug("Connection lost; reconnecting in %ss", delay)
                await asyncio.sleep(delay)


_MISSING = object()
