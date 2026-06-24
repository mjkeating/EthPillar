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

    Each client logs block processing differently. The Y-axis is always elapsed
    milliseconds and the X-axis is gas used in MGas, but the log field semantics
    vary — see ``CLIENT_LOG_TIMING`` for what each parser actually measures.

    Supported clients: geth, reth, besu, nethermind, erigon, ethrex, and a
    generic ``gas_used``/``elapsed`` fallback for unknown clients.
    """

    # What each client's parsed timing field represents (for apples-to-oranges awareness).
    CLIENT_LOG_TIMING = {
        "geth": "Block import elapsed (execution + validation) from chain segment log",
        "reth": "Block execution elapsed from structured log fields",
        "besu": "Block exec time (modern) or total import time (legacy Imported line)",
        "nethermind": "Block processing time from Processed line (paired with MGas line)",
        "erigon": "EVM execution time from head updated/validated (gas derived from mgas/s × time)",
        "ethrex": "Full add-block pipeline time (validate + exec + merkle + store)",
    }

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
        self.besu_modern_pattern = _compile(
            r"Imported\s+#(?:[\d,]+).*?([\d,]+)\s*\(\s*[\d.]+\s*\%\)\s*gas\s+used\s*\|\s*([\d,.]+)\s*ms\s+exec"
        )
        self.besu_legacy_pattern = _compile(
            r"Imported\s+#(?:[\d,]+).*?([\d,]+)\s*\(\s*[\d.]+\s*\%\)\s*gas\s*/.*?in\s+([\d,.]+)\s*s\b"
        )
        self.erigon_head_pattern = _compile(
            r"head (?:updated|validated).*?execution=([\d,.]+)(ms|s).*?mgas/s=([\d,.]+)"
        )
        self.erigon_metric_pattern = _compile(
            r"\[METRIC\]\s+BLOCK EXECUTION THROUGHPUT\s+\(\d+\):\s+[\d.]+\s+Ggas/s\s+"
            r"TIME SPENT:\s+([\d,.]+)\s+ms\.\s+Gas Used:\s+([\d,.]+)"
        )

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
            return None
        if client_name == "erigon":
            return None
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
        if self.client_name == "besu":
            return self._parse_besu_line(line)
        if self.client_name == "erigon":
            return self._parse_erigon_line(line)
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

    @staticmethod
    def _elapsed_ms(value: float, unit: str) -> float:
        return value * 1000.0 if unit.lower() == "s" else value

    def _parse_besu_line(self, line: str) -> Optional[ProcessingPoint]:
        """Parse Besu EngineNewPayload import summary lines.

        Modern format (Besu 26.x+):
            Imported #N (...) | ... | 34,833,694 (58.1%) gas used | 671.1ms exec | ...
        Legacy format:
            Imported #N / ... / 18,250,609 (60.8%) gas / (...) in 1.507s. Peers: N
        """

        match = self.besu_modern_pattern.search(line)
        if match:
            gas_used_mgas = float(match.group(1).replace(",", "")) / 1_000_000.0
            elapsed_time_ms = float(match.group(2).replace(",", ""))
            return ProcessingPoint(gas_used_mgas=gas_used_mgas, elapsed_time_ms=elapsed_time_ms)

        match = self.besu_legacy_pattern.search(line)
        if not match:
            return None

        gas_used_mgas = float(match.group(1).replace(",", "")) / 1_000_000.0
        elapsed_time_ms = float(match.group(2).replace(",", "")) * 1000.0
        return ProcessingPoint(gas_used_mgas=gas_used_mgas, elapsed_time_ms=elapsed_time_ms)

    def _parse_erigon_line(self, line: str) -> Optional[ProcessingPoint]:
        """Parse Erigon per-block timing logs.

        Primary (Engine API / fork-validator path, current Erigon releases):
            head validated ... execution=98ms mgas/s=600.56 avg mgas/s=389.11 ...
        Canonical commit path (batch FCU):
            head updated ... execution=932.476215ms mgas/s=16.23 average mgas/s=18.07
        Gas is derived: mgas/s × execution_seconds (not logged directly).

        Fallback (older metric summary):
            [METRIC] BLOCK EXECUTION THROUGHPUT (N): X Ggas/s TIME SPENT: Y ms. Gas Used: Z ...
        """

        match = self.erigon_head_pattern.search(line)
        if match:
            elapsed_value = float(match.group(1).replace(",", ""))
            elapsed_time_ms = self._elapsed_ms(elapsed_value, match.group(2))
            mgas_per_second = float(match.group(3).replace(",", ""))
            gas_used_mgas = mgas_per_second * (elapsed_time_ms / 1000.0)
            return ProcessingPoint(gas_used_mgas=gas_used_mgas, elapsed_time_ms=elapsed_time_ms)

        match = self.erigon_metric_pattern.search(line)
        if not match:
            return None

        elapsed_time_ms = float(match.group(1).replace(",", ""))
        gas_used_mgas = float(match.group(2).replace(",", "")) * 1000.0
        return ProcessingPoint(gas_used_mgas=gas_used_mgas, elapsed_time_ms=elapsed_time_ms)

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
        elif self.client_name == "ethrex":
            elapsed_time_ms = float(match.group(1).replace(",", ""))
            gas_used = float(match.group(2).replace(",", ""))
            return ProcessingPoint(gas_used_mgas=gas_used, elapsed_time_ms=elapsed_time_ms)
        else:
            gas_used = float(match.group(1))
            elapsed_value = float(match.group(2))
            elapsed_unit = match.group(3).lower()

        elapsed_time_ms = self._elapsed_ms(elapsed_value, elapsed_unit)
        return ProcessingPoint(gas_used_mgas=gas_used, elapsed_time_ms=elapsed_time_ms)
