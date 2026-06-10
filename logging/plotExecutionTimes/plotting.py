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

import asyncio
import math
import os
import platform
import shutil
import time
from collections import deque
from typing import Deque, Iterable, Optional, Sequence

from models import REDRAW_REQUESTED, MachineInfo, ProcessingPoint

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    Console = Group = Live = Panel = Text = None  # type: ignore[assignment]
    RICH_AVAILABLE = False


DEFAULT_Y_MAX_MS = 1000.0


def calculate_tier_percentages(values: Iterable[float]) -> tuple[float, float, float]:
    """Calculate the percentage of values falling into green/yellow/red tiers.

    Args:
        values: An iterable of elapsed times in milliseconds.

    Returns:
        A 3-tuple with percentages for (green, yellow, red) tiers.
    """

    values_list = list(values)
    if not values_list:
        return 0.0, 0.0, 0.0

    green = sum(1 for value in values_list if value < 300)
    yellow = sum(1 for value in values_list if 300 <= value <= 750)
    red = sum(1 for value in values_list if value > 750)
    total = len(values_list)
    return green / total * 100, yellow / total * 100, red / total * 100


def point_style(elapsed_time_ms: float) -> str:
    """Return a style name for a point based on its elapsed time.

    Args:
        elapsed_time_ms: Elapsed time in milliseconds.

    Returns:
        A string style name ("green", "yellow", or "red").
    """

    if elapsed_time_ms < 300:
        return "green"
    if elapsed_time_ms <= 750:
        return "yellow"
    return "red"


class PlotState:
    def __init__(self, max_points: int, machine_info: MachineInfo) -> None:
        """Hold the current plot state and recent points.

        Args:
            max_points: Maximum number of points to retain in the sliding window.
            machine_info: `MachineInfo` describing the host and client version.
        """

        self.points: Deque[ProcessingPoint] = deque(maxlen=max_points)
        self.machine_info = machine_info
        self.updated_at = time.monotonic()

    def add(self, point: ProcessingPoint) -> None:
        """Append a new `ProcessingPoint` to the state and update timestamp.

        Args:
            point: A `ProcessingPoint` to add to the plot history.
        """

        self.points.append(point)
        self.updated_at = time.monotonic()

    def update_client_version(self, client_version: str) -> None:
        """Update the stored client version in `machine_info`.

        Args:
            client_version: New client version string to set.
        """

        if client_version == self.machine_info.client_version:
            return
        self.machine_info = MachineInfo(
            client_version=client_version,
            cpu_model=self.machine_info.cpu_model,
            storage_model=self.machine_info.storage_model,
            installed_ram=self.machine_info.installed_ram,
        )
        self.updated_at = time.monotonic()


class PlotRenderer:
    def __init__(self, y_max_ms: float = DEFAULT_Y_MAX_MS) -> None:
        """Create a plot renderer.

        Args:
            y_max_ms: Maximum Y axis value in milliseconds to display (clamping threshold).
        """

        self.y_max_ms = y_max_ms

    def render(self, state: PlotState, width: int, height: int) -> object:
        """Render the current `PlotState` into either a Rich Panel or plain text.

        Args:
            state: Current `PlotState` containing points and machine info.
            width: Width in characters for the plot area.
            height: Height in characters for the plot area.

        Returns:
            A Rich renderable when Rich is available, otherwise a string.
        """

        plot_text = self.create_plot_text(state.points, width, height)
        machine_info = state.machine_info
        if RICH_AVAILABLE:
            return Panel(
                Group(
                    Text(f"Client: {machine_info.client_version}", style="bold"),
                    Text(f"CPU: {machine_info.cpu_model}"),
                    Text(f"Storage: {machine_info.storage_model}"),
                    Text(f"RAM: {machine_info.installed_ram}"),
                    plot_text,
                ),
                title="Execution Block Processing Times",
                subtitle="Ctrl+C to stop",
                border_style="cyan",
            )
        return "\n".join(
            [
                f"Client: {machine_info.client_version}",
                f"CPU: {machine_info.cpu_model}",
                f"Storage: {machine_info.storage_model}",
                f"RAM: {machine_info.installed_ram}",
                str(plot_text),
            ]
        )

    def create_plot_text(self, points: Sequence[ProcessingPoint], width: int, height: int) -> object:
        """Create the textual representation of the scatter plot grid.

        Args:
            points: Sequence of `ProcessingPoint` objects to render.
            width: Desired plot width in characters.
            height: Desired plot height in characters.

        Returns:
            A Rich Text object when Rich is available, otherwise a plain string.
        """

        width = max(24, width)
        height = max(10, height)
        x_min = 0.0
        x_max = self._x_axis_max(points)
        grid: list[list[tuple[str, Optional[str]]]] = [[(" ", None) for _ in range(width)] for _ in range(height)]

        newest_index = len(points) - 1
        for index, point in enumerate(points):
            y_value = min(point.elapsed_time_ms, self.y_max_ms)
            if not (x_min <= point.gas_used_mgas <= x_max):
                continue
            x_idx = int((point.gas_used_mgas - x_min) / (x_max - x_min) * (width - 1))
            y_idx = height - 1 - int((y_value / self.y_max_ms) * (height - 1))
            char = "#" if point.elapsed_time_ms > self.y_max_ms else "*"
            style = None if index == newest_index else point_style(point.elapsed_time_ms)
            if char == "#":
                style = "red"
            grid[y_idx][x_idx] = (char, style)

        self._place_label(grid, 0, 0, f"{int(self.y_max_ms)} ms")
        self._place_label(grid, self._y_row(1000, height), 0, "1000 ms")
        self._place_label(grid, self._y_row(500, height), 0, "500 ms")
        self._place_label(grid, height - 1, 0, "0")
        self._place_label(grid, height - 1, max(0, width - len(f"{x_max:.0f} MGas")), f"{x_max:.0f} MGas")

        green, yellow, red = calculate_tier_percentages(point.elapsed_time_ms for point in points)
        latest_mgas = points[-1].gas_used_mgas if points else 0.0
        latest_ms = points[-1].elapsed_time_ms if points else 0.0

        footer = [
            f"X-Axis: Gas Used | Range: 0 to {x_max:.2f} MGas",
            f"Y-Axis: Elapsed Time | Range: 0 to {self.y_max_ms:.0f} ms",
            f"Tiers: < 300ms: {green:.1f}% | 300ms - 750ms: {yellow:.1f}% | > 750ms: {red:.1f}%",
            "'#' = Clamped at Max Time",
        ]
        summary_line = f"Plot updated with {len(points)} points - Latest: {latest_mgas:.2f} MGas, {latest_ms:.1f}ms"

        if RICH_AVAILABLE:
            text = Text()
            text.append("-" * (width + 2) + "\n")
            for row in grid:
                text.append("|")
                for char, style in row:
                    text.append(char, style=style)
                text.append("|\n")
            text.append("-" * (width + 2) + "\n")
            for line in footer:
                if line.startswith("Tiers:"):
                    text.append("Tiers: ")
                    text.append(f"< 300ms: {green:.1f}%", style="green")
                    text.append(" | ")
                    text.append(f"300ms - 750ms: {yellow:.1f}%", style="yellow")
                    text.append(" | ")
                    text.append(f"> 750ms: {red:.1f}%", style="red")
                    text.append("\n")
                elif line.startswith("'#'"):
                    text.append("'")
                    text.append("#", style="red")
                    text.append("' = Clamped at Max Time\n")
                else:
                    text.append(line + "\n")
            text.append(summary_line + "\n")
            return text

        # --- Plain Text Fallback ---
        lines = ["-" * (width + 2)]
        # ANSI color codes for plain-text fallback
        ansi = {"green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m"}
        reset = "\033[0m"

        for row in grid:
            rendered = "|"
            for char, style in row:
                if style and style in ansi:
                    rendered += f"{ansi[style]}{char}{reset}"
                else:
                    rendered += char
            rendered += "|"
            lines.append(rendered)
        lines.append("-" * (width + 2))
        # draw colors for footer (color the '#' marker and tier percentages)
        colored_footer: list[str] = []
        for line in footer:
            if line.startswith("Tiers:"):
                parts = line.split("|")
                if len(parts) >= 3:
                    g = parts[0].split(":", 1)[1].strip()
                    y = parts[1].strip()
                    r = parts[2].strip()
                    colored_footer.append(
                        f"Tiers: {ansi['green']}{g}{reset} | {ansi['yellow']}{y}{reset} | {ansi['red']}{r}{reset}"
                    )
                else:
                    colored_footer.append(line)
            else:
                colored_footer.append(line.replace("#", f"{ansi['red']}#{reset}"))

        lines.extend(colored_footer)
        lines.append(summary_line)
        return "\n".join(lines)

    def _x_axis_max(self, points: Sequence[ProcessingPoint]) -> float:
        """Compute a sensible maximum value for the X axis (gas used).

        Args:
            points: Sequence of `ProcessingPoint` objects.

        Returns:
            A float representing the maximum MGas value to show on the X axis.
        """

        if not points:
            return 50.0
        candidate = max(50.0, max(point.gas_used_mgas for point in points) * 1.1)
        return max(1.0, math.ceil(candidate / 5.0) * 5.0)

    def _y_row(self, y_value: float, height: int) -> int:
        """Convert a Y-axis value in ms to a row index in the grid.

        Args:
            y_value: Y-axis value in milliseconds.
            height: Height of the grid in rows.

        Returns:
            Row index corresponding to the Y value.
        """

        if y_value <= 0 or y_value >= self.y_max_ms:
            return 0
        return height - 1 - int((y_value / self.y_max_ms) * (height - 1))

    @staticmethod
    def _place_label(grid: list[list[tuple[str, Optional[str]]]], row: int, col: int, label: str) -> None:
        """Place a horizontal label string into the grid at the specified position.

        Args:
            grid: The grid to modify.
            row: Row index to place the label.
            col: Column index to start the label.
            label: Label text to place into the grid.
        """

        if row < 0 or row >= len(grid):
            return
        for offset, char in enumerate(label):
            target = col + offset
            if 0 <= target < len(grid[row]):
                grid[row][target] = (char, None)


def compute_plot_dimensions() -> tuple[int, int]:
    """Compute terminal-based plot width and height with sensible bounds.

    Returns:
        A tuple of (width, height) to use for plotting.
    """

    terminal = shutil.get_terminal_size(fallback=(100, 34))
    width = int(os.environ.get("PLOT_WIDTH", max(24, terminal.columns - 6)))
    height = int(os.environ.get("PLOT_HEIGHT", max(10, terminal.lines - 14)))
    return max(24, min(width, max(24, terminal.columns - 6))), max(10, height)


async def consume_points(
    queue: asyncio.Queue[object],
    state: PlotState,
    renderer: PlotRenderer,
    refresh_per_second: int,
) -> None:
    """Consume items from an async queue and render updates to the terminal.

    The consumer listens for `ProcessingPoint` instances and special sentinel values
    such as `None` to stop or `REDRAW_REQUESTED` to force a redraw.

    Args:
        queue: Async queue yielding either `ProcessingPoint`, `REDRAW_REQUESTED`, or ``None``.
        state: `PlotState` instance to update with new points.
        renderer: `PlotRenderer` used to render the state.
        refresh_per_second: Refresh rate for Rich live rendering (when available).
    """

    async def _collect_batch() -> tuple[list[ProcessingPoint], bool]:
        """Wait for at least one queue item then drain immediately-available items.

        Returns (batch, saw_none). If `saw_none` is True the caller should stop.
        """

        first_item = await queue.get()
        if first_item is None:
            return [], True

        batch: list[ProcessingPoint] = []
        if first_item is not REDRAW_REQUESTED:
            batch.append(first_item)  # type: ignore[arg-type]

        while True:
            try:
                queued = queue.get_nowait()
            except asyncio.QueueEmpty:
                return batch, False
            if queued is None:
                # Re-queue the sentinel so the outer loop can observe it.
                await queue.put(None)
                return batch, True
            if queued is REDRAW_REQUESTED:
                continue
            batch.append(queued)  # type: ignore[arg-type]

    if RICH_AVAILABLE:
        console = Console()
        width, height = compute_plot_dimensions()
        with Live(
            renderer.render(state, width, height),
            console=console,
            refresh_per_second=refresh_per_second,
            screen=False,
            auto_refresh=False,
        ) as live:
            while True:
                batch, saw_none = await _collect_batch()
                if not batch and saw_none:
                    break  # producer finished (None sentinel); stop consumer without rendering
                for p in batch:
                    state.add(p)

                width, height = compute_plot_dimensions()
                live.update(renderer.render(state, width, height), refresh=True)
                if saw_none:
                    break  # producer finished during draining; rendered final batch, now stop
    else:
        while True:
            batch, saw_none = await _collect_batch()
            if not batch and saw_none:
                break  # producer finished (None sentinel); stop consumer without rendering
            for p in batch:
                state.add(p)

            width, height = compute_plot_dimensions()
            os.system("cls" if platform.system() == "Windows" else "clear")
            print(renderer.render(state, width, height))
            if saw_none:
                break  # producer finished during draining; rendered final batch, now stop
