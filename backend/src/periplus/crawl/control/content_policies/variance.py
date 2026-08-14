"""Sparse per-crawl experiments for numeric content-policy settings."""

from __future__ import annotations

import math
import random
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Protocol

from .schemas import (
    ContentPolicyConfig,
    ContentPolicyVariance,
    ContentVarianceArm,
    ContentVarianceSetting,
)


class VarianceRandom(Protocol):
    def randint(self, a: int, b: int) -> int: ...


@dataclass(frozen=True)
class _SettingExperiment:
    setting: ContentVarianceSetting
    method: str
    field: str
    lower_factor: float | None = None
    higher_factor: float | None = None
    integer_step: int | None = None


_ARMS: tuple[ContentVarianceArm, ...] = ("lower", "baseline", "higher")
_SETTING_EXPERIMENTS = (
    _SettingExperiment(
        "wait_dynamic.maximum_wait_ms",
        "wait_dynamic",
        "maximum_wait_ms",
        lower_factor=0.75,
        higher_factor=1.25,
    ),
    _SettingExperiment(
        "wait_dynamic.stable_samples",
        "wait_dynamic",
        "stable_samples",
        integer_step=1,
    ),
    _SettingExperiment(
        "wait_fixed.duration_ms",
        "wait_fixed",
        "duration_ms",
        lower_factor=0.5,
        higher_factor=1.5,
    ),
    _SettingExperiment(
        "scroll.maximum_iterations",
        "scroll",
        "maximum_iterations",
        lower_factor=0.75,
        higher_factor=1.25,
    ),
    _SettingExperiment(
        "scroll.wait_ms",
        "scroll",
        "wait_ms",
        lower_factor=0.5,
        higher_factor=1.5,
    ),
    _SettingExperiment(
        "scroll.stable_bottom_samples",
        "scroll",
        "stable_bottom_samples",
        integer_step=1,
    ),
    _SettingExperiment(
        "expand.maximum_actions",
        "expand",
        "maximum_actions",
        lower_factor=0.75,
        higher_factor=1.25,
    ),
    _SettingExperiment(
        "expand.wait_ms",
        "expand",
        "wait_ms",
        lower_factor=0.5,
        higher_factor=1.5,
    ),
)
_BOUNDS: dict[ContentVarianceSetting, tuple[int, int]] = {
    "wait_dynamic.maximum_wait_ms": (1, 120_000),
    "wait_dynamic.stable_samples": (1, 100),
    "wait_fixed.duration_ms": (1, 120_000),
    "scroll.maximum_iterations": (1, 1_000),
    "scroll.wait_ms": (0, 30_000),
    "scroll.stable_bottom_samples": (1, 100),
    "expand.maximum_actions": (1, 1_000),
    "expand.wait_ms": (0, 30_000),
}


def _active_experiments(
    completion: MutableMapping[str, object],
) -> list[_SettingExperiment]:
    active = []
    for experiment in _SETTING_EXPERIMENTS:
        method = completion[experiment.method]
        if not isinstance(method, MutableMapping):
            raise TypeError(f"{experiment.method} must be a mapping")
        if method.get("enabled") is True:
            active.append(experiment)
    return active


def _effective_value(
    configured: int,
    *,
    experiment: _SettingExperiment,
    arm: ContentVarianceArm,
) -> int:
    if arm == "baseline":
        return configured
    if experiment.integer_step is not None:
        delta = experiment.integer_step if arm == "higher" else -experiment.integer_step
        candidate = configured + delta
    else:
        factor = (
            experiment.higher_factor if arm == "higher" else experiment.lower_factor
        )
        if factor is None:
            raise ValueError(f"{experiment.setting} has no factor for {arm}")
        candidate = math.floor(configured * factor + 0.5)
    minimum, maximum = _BOUNDS[experiment.setting]
    return min(maximum, max(minimum, candidate))


def vary_content_policy(
    content: ContentPolicyConfig,
    *,
    rng: VarianceRandom = random,
) -> tuple[ContentPolicyConfig, ContentPolicyVariance | None]:
    """Vary one active setting and return its frozen experiment assignment."""

    varied = content.model_dump(mode="python")
    completion = varied["completion"]
    if not isinstance(completion, MutableMapping):
        raise TypeError("completion must be a mapping")
    experiments = _active_experiments(completion)
    if not experiments:
        return content, None

    experiment = experiments[rng.randint(0, len(experiments) - 1)]
    arm = _ARMS[rng.randint(0, len(_ARMS) - 1)]
    method = completion[experiment.method]
    if not isinstance(method, MutableMapping):
        raise TypeError(f"{experiment.method} must be a mapping")
    configured = method[experiment.field]
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise TypeError(f"{experiment.setting} must be an integer")
    effective = _effective_value(
        configured,
        experiment=experiment,
        arm=arm,
    )
    method[experiment.field] = effective
    assignment = ContentPolicyVariance(
        setting=experiment.setting,
        arm=arm,
        configured_value=configured,
        effective_value=effective,
    )
    return ContentPolicyConfig.model_validate(varied), assignment
