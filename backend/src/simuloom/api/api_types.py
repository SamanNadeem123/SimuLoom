"""Small shared types for the API layer."""

from __future__ import annotations

from typing import NamedTuple

from simuloom.db.models import Experiment


class SubmitResult(NamedTuple):
    experiment: Experiment
    replayed: bool
