"""Meta action-type → unified-metric mapping.

Meta reports conversions as a list of {action_type, value}. These sets define
which action types roll into each unified column. conversions = purchases + leads
by default; conversion_value comes from purchase action_values.
"""

PURCHASE_TYPES = {
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_conversion.purchase",
}

LEAD_TYPES = {
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "onsite_conversion.lead_grouped",
}

VIDEO_VIEW_TYPES = {"video_view"}


def sum_actions(items: list | None, types: set[str]) -> float:
    return sum(float(i.get("value", 0)) for i in (items or []) if i.get("action_type") in types)
