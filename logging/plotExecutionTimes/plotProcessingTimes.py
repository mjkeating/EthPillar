#!/usr/bin/env python3
"""Glue code for the real-time execution processing time plotter."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from models import REDRAW_REQUESTED, ProcessingPoint
from parsers import ExecutionLogParser
from plotting import PlotRenderer, PlotState, consume_points
from producers import LineProducer, choose_producer
from system_info import build_machine_info, detect_client_info


DEFAULT_MAX_POINTS = 320


class ParserState:
    def __init__(self, client_name: str) -> None:
        """Hold parser-related state and the active `ExecutionLogParser`.

        Args:
            client_name: Initial execution client name to configure the parser for.
        """

        self.client_name = (client_name or "unknown").lower()
        self.parser = ExecutionLogParser(self.client_name)

    def parse_line(self, line: str) -> Optional[ProcessingPoint]:
        """Parse a raw line via the current `ExecutionLogParser`.

        Args:
            line: Raw log line to parse.

        Returns:
            A `ProcessingPoint` when parsing yields a measurement, otherwise ``None``.
        """

        return self.parser.parse_line(line)

    def update_client(self, client_name: str) -> None:
        """Switch the parser to a different client implementation.

        Args:
            client_name: New client name to switch to.
        """

        normalized = (client_name or "unknown").lower()
        if normalized == self.client_name:
            return
        self.client_name = normalized
        self.parser = ExecutionLogParser(normalized)


async def produce_points(
    producer: LineProducer,
    parser_state: ParserState,
    queue: asyncio.Queue[object],
) -> None:
    """Read raw lines from a `LineProducer`, parse them, and enqueue points.

    Args:
        producer: Source of raw log lines.
        parser_state: ParserState used to convert lines into `ProcessingPoint`.
        queue: Async queue where `ProcessingPoint` objects (and ``None`` sentinel) are placed.
    """

    async for line in producer.lines():
        point = parser_state.parse_line(line)
        if point is not None:
            await queue.put(point)
    await queue.put(None)


async def refresh_client_info(
    endpoint: str,
    parser_state: ParserState,
    plot_state: PlotState,
    queue: asyncio.Queue[object],
    interval_seconds: int,
    auto_client: bool,
) -> None:
    """Periodically probe the client and update `PlotState`/parser when needed.

    Args:
        endpoint: JSON-RPC endpoint to query for client info.
        parser_state: ParserState to update when client changes and `auto_client` is True.
        plot_state: PlotState used to track current machine/client version.
        queue: Queue where `REDRAW_REQUESTED` sentinel is placed when the client version changes.
        interval_seconds: How many seconds to wait between probes.
        auto_client: If True, the parser will be switched automatically to detected client.
    """

    while True:
        await asyncio.sleep(interval_seconds)
        client_name, client_version = await asyncio.to_thread(detect_client_info, endpoint)
        previous_client_name = parser_state.client_name
        if auto_client:
            parser_state.update_client(client_name)
        detected_client_name = (client_name or "unknown").lower()
        detected_unknown_version = client_version == "Client:Unknown" or client_version.endswith(":Unknown")
        current_known_version = not plot_state.machine_info.client_version.endswith(":Unknown")
        same_client = detected_client_name == previous_client_name
        if detected_unknown_version and current_known_version and same_client:
            continue
        before = plot_state.machine_info.client_version
        plot_state.update_client_version(client_version)
        if plot_state.machine_info.client_version != before:
            await queue.put(REDRAW_REQUESTED)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot execution client block processing times.")
    parser.add_argument("--source", choices=("auto", "systemd", "journalctl", "stdin"), default="auto")
    parser.add_argument("--unit", default="execution", help="systemd unit name without .service")
    parser.add_argument("--client", default="auto", help="Execution client name, or auto")
    parser.add_argument("--el-rpc-endpoint", default=os.environ.get("EL_RPC_ENDPOINT", "http://127.0.0.1:8545"))
    parser.add_argument("--max-points", type=int, default=DEFAULT_MAX_POINTS)
    parser.add_argument("--tail", type=int, default=0, help="Historical journal lines to scan before following")
    parser.add_argument("--refresh-per-second", type=int, default=8)
    parser.add_argument("--client-refresh-seconds", type=int, default=30)
    parser.add_argument("--self-test", action="store_true", help="Parse sample client logs and exit")
    """Parse command line arguments for the plotting tool.

    Args:
        argv: Optional list of argument strings (defaults to sys.argv when None).

    Returns:
        The populated `argparse.Namespace`.
    """

    return parser.parse_args(argv)


def run_self_test() -> int:
    """Run a quick sanity check parsing representative log lines.

    Returns:
        Exit code 0 on success, 1 on failure.
    """
    samples = {
        "geth": "INFO Imported new potential chain segment number=1 mgas=20.42 elapsed=125.573305ms",
        "reth": "INFO number=23493228 gas_used=8.50Mgas elapsed=59.916758ms",
        "besu": "INFO | 20,237,520 (100.0%) gas; 1.2 mwei bfee| 20,237,520 (0.154s exec)",
        "nethermind": "Processed 123 | 345.6 ms Block 123 17.42 MGas",
    }

    for client, line in samples.items():
        point = ExecutionLogParser(client).parse_line(line)
        if point is None:
            print(f"Self-test failed for {client}", file=sys.stderr)
            return 1
    print("Self-test passed.")
    return 0


async def async_main(args: argparse.Namespace) -> None:
    """Main async entrypoint creating tasks for producing and consuming plot points.

    Args:
        args: Parsed command line arguments namespace.
    """

    client_name, client_version = detect_client_info(args.el_rpc_endpoint)
    auto_client = args.client == "auto"
    if not auto_client:
        client_name = args.client
        client_version = f"{args.client}:manual"

    parser_state = ParserState(client_name)
    producer = choose_producer(args.source, args.unit, args.tail)
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=1000)
    plot_state = PlotState(max_points=args.max_points, machine_info=build_machine_info(client_version))
    renderer = PlotRenderer()

    core_tasks = {
        asyncio.create_task(produce_points(producer, parser_state, queue)),
        asyncio.create_task(consume_points(queue, plot_state, renderer, args.refresh_per_second)),
    }
    background_tasks = set()
    if args.client_refresh_seconds > 0:
        background_tasks.add(
            asyncio.create_task(
                refresh_client_info(
                    args.el_rpc_endpoint,
                    parser_state,
                    plot_state,
                    queue,
                    args.client_refresh_seconds,
                    auto_client,
                )
            )
        )

    done, pending = await asyncio.wait(core_tasks, return_when=asyncio.FIRST_EXCEPTION)
    try:
        for task in done:
            task.result()
    finally:
        for task in pending:
            task.cancel()
        for task in background_tasks:
            task.cancel()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Synchronous CLI entrypoint.

    Args:
        argv: Optional list of argument strings (defaults to sys.argv when None).

    Returns:
        Process exit code.
    """

    args = parse_args(argv)
    if args.self_test:
        return run_self_test()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nStopping.")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
