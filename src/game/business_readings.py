"""Validation for live business observations, separate from saved check-ins."""

from .businesses import BUSINESSES


def normalize_live_business_reading(
    business_id: str,
    stock_percent: int | None,
    supply_percent: int | None,
    value: int | None,
    *,
    manual: bool = False,
) -> tuple[int | None, int | None, int | None]:
    """Validate observed fields without converting unknowns or zeroes to estimates.

    Manual readings reject supplies for businesses without a supply meter. OCR
    readings discard those irrelevant supplies. At least one applicable field
    must remain observed. Invalid readings raise ValueError without side effects.
    """
    if type(business_id) is not str or business_id not in BUSINESSES:
        raise ValueError("Choose a known business")

    for name, observed, maximum in (
        ("Stock", stock_percent, 100),
        ("Supplies", supply_percent, 100),
        ("Value", value, 2**63 - 1),
    ):
        if observed is not None and (type(observed) is not int or not 0 <= observed <= maximum):
            raise ValueError(f"{name} must be a whole number from 0 to {maximum}, or unknown")

    if not BUSINESSES[business_id].uses_supplies and supply_percent is not None:
        if manual:
            raise ValueError("This business does not use supplies")
        supply_percent = None

    if stock_percent is None and supply_percent is None and value is None:
        raise ValueError("Enter at least one observed value")

    return stock_percent, supply_percent, value
