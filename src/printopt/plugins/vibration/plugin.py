"""Vibration analysis plugin."""

from __future__ import annotations

from printopt.core.plugin import Plugin


class VibrationPlugin(Plugin):
    name = "vibration"

    def __init__(self) -> None:
        super().__init__()
        self.results: dict = {}

    async def on_start(self) -> None:
        pass

    async def on_stop(self) -> None:
        pass

    def get_dashboard_data(self) -> dict:
        return {"results": self.results}
