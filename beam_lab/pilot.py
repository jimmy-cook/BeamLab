"""Frozen inputs matching the 2,000-model Excel pilot; loading never starts ETABS."""
from importlib.resources import files
import json

from .config import Config


def load_excel_pilot() -> Config:
    """Return a fresh editable study, independent of future sampler changes."""
    source = files("beam_lab").joinpath("data/ann_pilot_100x20.json")
    return Config.parse(json.loads(source.read_text(encoding="utf-8")))
