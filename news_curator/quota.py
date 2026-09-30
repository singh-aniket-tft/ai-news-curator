"""Local Gemini request budget shared by interactive and scheduled runs."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from .storage import Store


def make_request_gate(store: Store, config: Mapping | None) -> Callable[[], None] | None:
    if not config:
        return None

    def gate() -> None:
        while True:
            wait = store.reserve_vlm_request(
                config["model"], config["requests_per_day"], config["requests_per_minute"]
            )
            if wait <= 0:
                return
            time.sleep(wait)

    return gate
