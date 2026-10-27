"""Meta action-type → unified-metric mapping.

Meta reports conversions as a list of {action_type, value}, and one conversion
shows up under several overlapping action types at once: `omni_purchase` (every
channel), its alias `purchase`, and the per-channel
`offsite_conversion.fb_pixel_purchase` (website) / `onsite_conversion.purchase`
(on Meta). Summing them counts each purchase two or three times, so every metric
reads one *aggregate* type (the first present, in priority order) and only adds
up the disjoint per-channel *parts* when no aggregate is reported.

conversions = purchases + leads by default; conversion_value comes from purchase
action_values.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionKind:
    aggregates: tuple[str, ...]  # same total under different names; first present wins
    parts: tuple[str, ...] = ()  # disjoint per-channel counts; summed only as a fallback


PURCHASE = ActionKind(
    aggregates=("omni_purchase", "purchase"),
    parts=("offsite_conversion.fb_pixel_purchase", "onsite_conversion.purchase"),
)

LEAD = ActionKind(
    aggregates=("lead",),
    parts=("offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"),
)

VIDEO_VIEW = ActionKind(aggregates=("video_view",))


def action_total(items: list | None, kind: ActionKind) -> float:
    by_type: dict[str, float] = {}
    for i in items or []:
        t = i.get("action_type")
        by_type[t] = by_type.get(t, 0.0) + float(i.get("value", 0))
    for t in kind.aggregates:
        if t in by_type:
            return by_type[t]
    return sum(by_type.get(t, 0.0) for t in kind.parts)
