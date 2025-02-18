"""Cost estimation from a local pricing table."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Price:
    input_per_1k: float
    output_per_1k: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            max(0, input_tokens) / 1000.0 * self.input_per_1k
            + max(0, output_tokens) / 1000.0 * self.output_per_1k
        )


class Pricing:
    """Model price lookup with an explicit fallback for unknown models."""

    def __init__(self, models: dict[str, Price], default: Price, as_of: str = "unknown") -> None:
        self.models = dict(models)
        self.default = default
        self.as_of = as_of

    @classmethod
    def load(cls, path: str | Path | None = None) -> Pricing:
        if path is None:
            text = resources.files("trace_to_eval.data").joinpath("pricing.json").read_text("utf-8")
            raw: dict[str, Any] = json.loads(text)
        else:
            raw = json.loads(Path(path).expanduser().read_text("utf-8"))
        default_raw = raw.get("default", {"input_per_1k": 0.005, "output_per_1k": 0.015})
        models = {
            str(name): Price(
                input_per_1k=float(value.get("input_per_1k", 0.0)),
                output_per_1k=float(value.get("output_per_1k", 0.0)),
            )
            for name, value in (raw.get("models") or {}).items()
        }
        return cls(
            models=models,
            default=Price(
                input_per_1k=float(default_raw["input_per_1k"]),
                output_per_1k=float(default_raw["output_per_1k"]),
            ),
            as_of=str(raw.get("as_of", "unknown")),
        )

    def price_for(self, model: str) -> Price:
        if model in self.models:
            return self.models[model]
        for name, price in self.models.items():
            if model.startswith(name):
                return price
        return self.default

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float:
        return self.price_for(model).cost(input_tokens, output_tokens)
