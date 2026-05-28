"""Kōan — Thompson Sampling multi-armed bandit for project selection.

Each project gets a Beta distribution parameterized by (alpha, beta):
- alpha: accumulated successes + 1 (prior = 1)
- beta: accumulated failures + 1 (prior = 1)

The uniform (1, 1) prior means new projects start at 50% estimated win
rate and are fully eligible for exploration. After ~50 outcomes, the
distribution meaningfully separates high-performers from low-performers.

Persistence: .bandit-state.json in the instance directory (atomic write).
"""

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple

from app.utils import atomic_write

_BANDIT_FILE = ".bandit-state.json"


@dataclass
class BanditState:
    """Per-project Beta distribution parameters for Thompson Sampling."""

    # Maps project name -> (alpha, beta); both values are always > 0.
    params: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def get(self, project: str) -> Tuple[float, float]:
        """Return (alpha, beta) for a project, defaulting to uniform prior."""
        return self.params.get(project, (1.0, 1.0))


def load_bandit_state(instance_dir: str) -> BanditState:
    """Load bandit state from disk.

    Returns a fresh BanditState if the file is missing or malformed.
    Never raises — graceful fallback is required so a bad state file
    does not crash the agent loop.
    """
    path = Path(instance_dir) / _BANDIT_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        params: Dict[str, Tuple[float, float]] = {}
        for name, values in raw.items():
            if (
                isinstance(values, (list, tuple))
                and len(values) == 2
                and all(isinstance(v, (int, float)) and v > 0 for v in values)
            ):
                params[name] = (float(values[0]), float(values[1]))
        return BanditState(params=params)
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError):
        return BanditState()


def save_bandit_state(state: BanditState, instance_dir: str) -> None:
    """Persist bandit state to disk using an atomic write."""
    path = Path(instance_dir) / _BANDIT_FILE
    data = {name: list(ab) for name, ab in state.params.items()}
    atomic_write(path, json.dumps(data, indent=2))


def thompson_sample(state: BanditState, project: str) -> float:
    """Draw a sample from the project's Beta distribution.

    The sample represents a plausible success rate for this project.
    Projects with more successes produce higher samples on average,
    while uncertain projects (few observations) produce noisier samples,
    naturally balancing exploitation and exploration.

    Returns a value in [0, 1].
    """
    alpha, beta = state.get(project)
    return random.betavariate(alpha, beta)


def update_bandit(state: BanditState, project: str, success: bool) -> None:
    """Update Beta parameters in-place after observing an outcome.

    success=True  → increment alpha (productive session)
    success=False → increment beta  (empty or blocked session)
    """
    alpha, beta = state.get(project)
    if success:
        state.params[project] = (alpha + 1.0, beta)
    else:
        state.params[project] = (alpha, beta + 1.0)
