from __future__ import annotations

import asyncio
import sys
from typing import AsyncIterator, Protocol


class LineProducer(Protocol):
    async def lines(self) -> AsyncIterator[str]:
        """Yield raw log lines."""


class StdinProducer:
    async def lines(self) -> AsyncIterator[str]:
        """Asynchronously yield lines read from standard input.

        Yields:
            Raw lines read from `sys.stdin` until EOF.
        """

        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                break
            yield line


class JournalctlProducer:
    def __init__(self, unit: str, tail: int) -> None:
        """Produce lines by invoking `journalctl` for a given systemd unit.

        Args:
            unit: The systemd unit name to follow (without `.service`).
            tail: Number of historical lines to fetch before following.
        """

        self.unit = unit
        self.tail = tail

    async def lines(self) -> AsyncIterator[str]:
        """Yield lines from a `journalctl -f` subprocess for the configured unit.

        Yields:
            Decoded log lines produced by `journalctl` until the subprocess ends.
        """

        process = await asyncio.create_subprocess_exec(
            "journalctl",
            "-fu",
            self.unit,
            "--no-hostname",
            "-n",
            str(self.tail),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                yield line.decode(errors="replace")
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


class SystemdJournalProducer:
    def __init__(self, unit: str, tail: int) -> None:
        """Produce lines by reading the systemd journal via python-systemd.

        Args:
            unit: The systemd unit name to follow (without `.service`).
            tail: Number of historical lines to fetch before following.
        """

        self.unit = unit
        self.tail = tail

    async def lines(self) -> AsyncIterator[str]:
        """Yield lines from the systemd journal using python-systemd's Reader.

        Yields:
            The `MESSAGE` field of journal entries as strings as they arrive.
        """

        try:
            from systemd import journal
        except ImportError as exc:
            raise RuntimeError("python3-systemd is not installed") from exc

        reader = journal.Reader()
        reader.this_boot()
        reader.add_match(_SYSTEMD_UNIT=f"{self.unit}.service")
        reader.seek_tail()
        if self.tail > 0:
            reader.get_previous(skip=self.tail)

        while True:
            for entry in reader:
                message = entry.get("MESSAGE")
                if message:
                    yield str(message)

            await asyncio.to_thread(self._wait_for_journal, reader)

    @staticmethod
    def _wait_for_journal(reader: object) -> None:
        """Block until new journal events are available and process them.

        This helper is run in a thread to wait for the systemd journal reader
        to signal new events via its file descriptor. It uses `select.poll`
        and then calls `reader.process()` to advance the reader.

        Args:
            reader: The python-systemd journal Reader instance.
        """

        import select

        poller = select.poll()
        poller.register(reader, reader.get_events())  # type: ignore[attr-defined]
        timeout = reader.get_timeout()  # type: ignore[attr-defined]
        poll_timeout_ms = -1 if timeout is None else max(0, int(timeout / 1000))
        poller.poll(poll_timeout_ms)
        reader.process()  # type: ignore[attr-defined]


def choose_producer(source: str, unit: str, tail: int) -> LineProducer:
    """Return an appropriate `LineProducer` instance for a given source.

    Args:
        source: One of "stdin", "journalctl", "systemd", or "auto". "auto" will
            prefer the native systemd reader when available.
        unit: The systemd unit (without .service) used for journal-based producers.
        tail: Number of historical lines to request from the source.

    Returns:
        An object implementing the `LineProducer` protocol.
    """

    if source == "stdin":
        return StdinProducer()
    if source == "journalctl":
        return JournalctlProducer(unit, tail)
    if source == "systemd":
        return SystemdJournalProducer(unit, tail)
    if source == "auto":
        try:
            import systemd.journal  # noqa: F401

            return SystemdJournalProducer(unit, tail)
        except ImportError:
            return JournalctlProducer(unit, tail)
    raise ValueError(f"Unsupported source: {source}")
