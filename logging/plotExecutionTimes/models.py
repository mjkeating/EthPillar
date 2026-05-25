from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingPoint:
    """A single measured processing data point.

    Attributes:
        gas_used_mgas: Gas used for the block in MGas.
        elapsed_time_ms: Elapsed processing time in milliseconds.
    """

    gas_used_mgas: float
    elapsed_time_ms: float


@dataclass(frozen=True)
class MachineInfo:
    """Information describing the machine and execution client.

    Attributes:
        client_version: Human readable execution client and version (e.g. "geth:1.10").
        cpu_model: CPU model string detected from the host.
        storage_model: Storage device description (largest SSD detected, or a fallback).
        installed_ram: Installed RAM as a human readable string.
    """

    client_version: str
    cpu_model: str
    storage_model: str
    installed_ram: str


class RedrawRequested:
    """Marker class used to request a redraw of the plot.

    Instances of this class are placed onto the event queue to signal
    that the rendering should be refreshed without adding a data point.
    """


REDRAW_REQUESTED = RedrawRequested()
