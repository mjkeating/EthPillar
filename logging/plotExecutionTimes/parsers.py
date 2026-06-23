# Copyright (C) 2026  b0a7
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import re
from typing import Optional

from models import ProcessingPoint


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi_codes(value: str) -> str:
    """Remove ANSI escape sequences from a string.

    Args:
        value: The string potentially containing ANSI color/formatting codes.

    Returns:
        The input string with ANSI escape sequences removed.
    """

    return ANSI_ESCAPE_RE.sub("", value)


def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a regular expression with case-insensitive flag.

    Args:
        pattern: The regex pattern to compile.

    Returns:
        A compiled regular expression object with IGNORECASE set.
    """

    return re.compile(pattern, re.IGNORECASE)


class ExecutionLogParser:
    """Parse execution-client log lines into normalized plotting points.

    The parser supports multiple client log formats (geth, reth, besu, nethermind,
    ethrex, and a generic fallback). It extracts gas used and elapsed processing
    time and returns a `ProcessingPoint` when a complete measurement is found.
    """

    def __init__(self, client_name: str) -> None:
        """Initialize the parser for a specific client.

        Args:
            client_name: Name of the execution client (e.g. "geth", "reth").
        """

        self.client_name = (client_name or "unknown").lower()
        self.pattern = self._client_pattern(self.client_name)
        self.last_nethermind_elapsed_ms: Optional[float] = None
        self.nethermind_time_pattern = _compile(r"Processed\s+(\d+)\s+\|\s+([\d,.]+)\s*ms")
        self.nethermind_gas_pattern = _compile(r"\bBlock\b.*?([\d,.]+)\s*MGas(?!/s)")

    @staticmethod
    def _client_pattern(client_name: str) -> Optional[re.Pattern[str]]:
        """Return a compiled regex pattern appropriate for the named client.

        Args:
            client_name: Execution client name used to select a parsing pattern.

        Returns:
            A compiled regex or ``None`` for clients that require multi-line parsing.
        """

        if client_name == "geth":
            return _compile(r"Imported new potential chain segment.*mgas=([\d.]+).*elapsed=([\d.]+)(ms|s)")
        if client_name == "besu":
            return _compile(r"mwei bfee\|\s*([\d,]+)\s+\(.*?([\d.]+)s exec")
        if client_name == "reth":
            return _compile(r".*number=(\d+).*gas_used=([\d.]+)Mgas.*elapsed=([\d.]+)(ms|s)")
        if client_name == "ethrex":
            # Matches print_add_block_pipeline_logs header in ethrex blockchain.rs:
            # [METRIC] BLOCK {n} {hash} | {Ggas/s} | {total_ms} ms | {txs} txs | {Mgas} ({util}%)
            return _compile(
                r"\[METRIC\]\s+BLOCK\s+\d+\s+0x[0-9a-f]+\s*\|\s*[\d.]+\s+Ggas/s\s*\|\s*"
                r"([\d,.]+)\s+ms\s*\|\s*\d+\s+txs\s*\|\s*([\d,.]+)\s+Mgas\s*\(\d+%\)"
            )
        if client_name == "nethermind":
            return None
        return _compile(r".*gas_used=([\d.]+)Mgas.*elapsed=([\d.]+)(ms|s)")

    def parse_line(self, raw_line: str) -> Optional[ProcessingPoint]:
        """Parse a single raw log line and return a ProcessingPoint when available.

        Args:
            raw_line: The raw log line to parse (may contain ANSI codes).

        Returns:
            A `ProcessingPoint` if the line (or accumulated multi-line state) yields
            a measurement; otherwise ``None``.
        """

        line = strip_ansi_codes(raw_line).strip()
        if not line:
            return None

        if self.client_name == "nethermind":
            return self._parse_nethermind_line(line)
        return self._parse_single_line_client(line)

    def _parse_nethermind_line(self, line: str) -> Optional[ProcessingPoint]:
        """Parse lines produced by Nethermind which may span multiple lines.

        The method accumulates a time measurement on a time-containing line and then
        pairs it with a subsequent gas line to return a complete `ProcessingPoint`.

        Args:
            line: A cleaned (ANSI-free) log line.

        Returns:
            A `ProcessingPoint` when both time and gas have been observed, otherwise ``None``.
        """

        process_match = self.nethermind_time_pattern.search(line)
        if process_match:
            self.last_nethermind_elapsed_ms = float(process_match.group(2).replace(",", ""))
            inline_gas_match = self.nethermind_gas_pattern.search(line)
            if inline_gas_match:
                point = ProcessingPoint(
                    gas_used_mgas=float(inline_gas_match.group(1).replace(",", "")),
                    elapsed_time_ms=self.last_nethermind_elapsed_ms,
                )
                self.last_nethermind_elapsed_ms = None
                return point
            return None

        if self.last_nethermind_elapsed_ms is None:
            return None

        gas_match = self.nethermind_gas_pattern.search(line)
        if not gas_match:
            return None

        point = ProcessingPoint(
            gas_used_mgas=float(gas_match.group(1).replace(",", "")),
            elapsed_time_ms=self.last_nethermind_elapsed_ms,
        )
        self.last_nethermind_elapsed_ms = None
        return point

    def _parse_single_line_client(self, line: str) -> Optional[ProcessingPoint]:
        """Parse a single-line client log entry using the client-specific pattern.

        Args:
            line: A cleaned (ANSI-free) log line.

        Returns:
            A `ProcessingPoint` when the line matches the client's pattern, otherwise ``None``.
        """

        if self.pattern is None:
            return None

        match = self.pattern.search(line)
        if not match:
            return None

        if self.client_name == "reth":
            gas_used = float(match.group(2))
            elapsed_value = float(match.group(3))
            elapsed_unit = match.group(4).lower()
        elif self.client_name == "geth":
            gas_used = float(match.group(1))
            elapsed_value = float(match.group(2))
            elapsed_unit = match.group(3).lower()
        elif self.client_name == "besu":
            gas_used = float(match.group(1).replace(",", "")) / 1_000_000.0
            elapsed_value = float(match.group(2))
            elapsed_unit = "s"
        elif self.client_name == "ethrex":
            elapsed_time_ms = float(match.group(1).replace(",", ""))
            gas_used = float(match.group(2).replace(",", ""))
            return ProcessingPoint(gas_used_mgas=gas_used, elapsed_time_ms=elapsed_time_ms)
        else:
            gas_used = float(match.group(1))
            elapsed_value = float(match.group(2))
            elapsed_unit = match.group(3).lower()

        elapsed_time_ms = elapsed_value * 1000.0 if elapsed_unit == "s" else elapsed_value
        return ProcessingPoint(gas_used_mgas=gas_used, elapsed_time_ms=elapsed_time_ms)
