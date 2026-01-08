"""Payout data for GTA Online activities."""

from typing import Dict, Optional


# Expected payouts for various activities
# Values are approximate and may vary based on in-game factors

ACTIVITY_PAYOUTS: Dict[str, Dict[str, int]] = {
    # VIP Work
    "vip_headhunter": {"min": 20000, "max": 25000, "avg": 22500},
    "vip_sightseer": {"min": 20000, "max": 25000, "avg": 22500},
    "vip_hostile_takeover": {"min": 15000, "max": 20000, "avg": 17500},
    "vip_executive_search": {"min": 20000, "max": 25000, "avg": 22500},

    # Payphone Hits
    "payphone_hit_bonus": {"min": 85000, "max": 85000, "avg": 85000},
    "payphone_hit_no_bonus": {"min": 15000, "max": 15000, "avg": 15000},

    # Security Contracts
    "security_contract_professional": {"min": 50000, "max": 70000, "avg": 60000},
    "security_contract_specialist": {"min": 40000, "max": 55000, "avg": 47500},
    "security_contract_standard": {"min": 31000, "max": 44000, "avg": 37500},

    # Auto Shop
    "auto_shop_delivery_s_class": {"min": 165000, "max": 180000, "avg": 172500},
    "auto_shop_delivery_a_class": {"min": 130000, "max": 145000, "avg": 137500},
    "auto_shop_exotic_export": {"min": 20000, "max": 20000, "avg": 20000},

    # Cayo Perico (solo, hard mode, primary only)
    "cayo_tequila": {"min": 990000, "max": 990000, "avg": 990000},
    "cayo_ruby_necklace": {"min": 1100000, "max": 1100000, "avg": 1100000},
    "cayo_bearer_bonds": {"min": 1210000, "max": 1210000, "avg": 1210000},
    "cayo_pink_diamond": {"min": 1430000, "max": 1430000, "avg": 1430000},
    "cayo_panther": {"min": 2090000, "max": 2090000, "avg": 2090000},

    # Business Sells (full stock, solo/local)
    "sell_cocaine": {"min": 420000, "max": 420000, "avg": 420000},
    "sell_meth": {"min": 357000, "max": 357000, "avg": 357000},
    "sell_cash": {"min": 294000, "max": 294000, "avg": 294000},
    "sell_weed": {"min": 252000, "max": 252000, "avg": 252000},
    "sell_documents": {"min": 126000, "max": 126000, "avg": 126000},
    "sell_bunker": {"min": 1050000, "max": 1050000, "avg": 1050000},
    "sell_nightclub": {"min": 1600000, "max": 1700000, "avg": 1650000},

    # Agency Safe
    "agency_safe_max": {"min": 250000, "max": 250000, "avg": 250000},
}


def get_payout(activity_id: str) -> Optional[Dict[str, int]]:
    """Get payout information for an activity.

    Args:
        activity_id: Activity identifier

    Returns:
        Dict with min, max, avg or None
    """
    return ACTIVITY_PAYOUTS.get(activity_id.lower())


def get_average_payout(activity_id: str) -> int:
    """Get average payout for an activity.

    Args:
        activity_id: Activity identifier

    Returns:
        Average payout or 0
    """
    payout = get_payout(activity_id)
    return payout["avg"] if payout else 0


def estimate_hourly_rate(activity_id: str, duration_minutes: float) -> float:
    """Estimate hourly rate for an activity.

    Args:
        activity_id: Activity identifier
        duration_minutes: How long the activity takes

    Returns:
        Estimated $/hour
    """
    avg = get_average_payout(activity_id)
    if avg and duration_minutes > 0:
        return (avg / duration_minutes) * 60
    return 0.0
