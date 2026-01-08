"""Mission definitions for GTA Online."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MissionInfo:
    """Information about a known mission."""

    name: str
    category: str  # contact, heist, vip, etc.
    min_payout: int
    max_payout: int
    avg_duration_minutes: int
    difficulty: str  # easy, medium, hard
    solo_friendly: bool = True
    notes: str = ""


# Known contact missions with approximate payouts
CONTACT_MISSIONS = {
    "rooftop rumble": MissionInfo(
        name="Rooftop Rumble",
        category="contact",
        min_payout=13500,
        max_payout=18000,
        avg_duration_minutes=4,
        difficulty="easy",
        solo_friendly=True,
    ),
    "trash talk": MissionInfo(
        name="Trash Talk",
        category="contact",
        min_payout=11000,
        max_payout=16000,
        avg_duration_minutes=6,
        difficulty="medium",
        solo_friendly=True,
    ),
    "pier pressure": MissionInfo(
        name="Pier Pressure",
        category="contact",
        min_payout=10000,
        max_payout=15000,
        avg_duration_minutes=5,
        difficulty="easy",
        solo_friendly=True,
    ),
    "blow up": MissionInfo(
        name="Blow Up",
        category="contact",
        min_payout=9000,
        max_payout=14000,
        avg_duration_minutes=4,
        difficulty="easy",
        solo_friendly=True,
    ),
}

# VIP Work with cooldowns
VIP_WORK = {
    "headhunter": MissionInfo(
        name="Headhunter",
        category="vip",
        min_payout=20000,
        max_payout=25000,
        avg_duration_minutes=5,
        difficulty="easy",
        solo_friendly=True,
        notes="Kill 4 targets, best done with Oppressor/Buzzard",
    ),
    "sightseer": MissionInfo(
        name="Sightseer",
        category="vip",
        min_payout=20000,
        max_payout=25000,
        avg_duration_minutes=6,
        difficulty="easy",
        solo_friendly=True,
        notes="Collect 3 packages, best done with fast car/bike",
    ),
    "hostile takeover": MissionInfo(
        name="Hostile Takeover",
        category="vip",
        min_payout=15000,
        max_payout=20000,
        avg_duration_minutes=5,
        difficulty="medium",
        solo_friendly=True,
    ),
}

# Security Contracts
SECURITY_CONTRACTS = {
    "recover valuables": MissionInfo(
        name="Recover Valuables",
        category="security_contract",
        min_payout=31000,
        max_payout=70000,
        avg_duration_minutes=8,
        difficulty="medium",
        solo_friendly=True,
    ),
    "gang termination": MissionInfo(
        name="Gang Termination",
        category="security_contract",
        min_payout=31000,
        max_payout=70000,
        avg_duration_minutes=7,
        difficulty="medium",
        solo_friendly=True,
    ),
    "rescue operation": MissionInfo(
        name="Rescue Operation",
        category="security_contract",
        min_payout=31000,
        max_payout=70000,
        avg_duration_minutes=10,
        difficulty="medium",
        solo_friendly=True,
    ),
}


def get_mission_info(name: str) -> Optional[MissionInfo]:
    """Get information about a mission by name.

    Args:
        name: Mission name (case-insensitive)

    Returns:
        MissionInfo or None
    """
    name_lower = name.lower()

    # Check all mission dictionaries
    for missions in [CONTACT_MISSIONS, VIP_WORK, SECURITY_CONTRACTS]:
        if name_lower in missions:
            return missions[name_lower]

    return None


def get_estimated_payout(mission_name: str) -> int:
    """Get estimated average payout for a mission.

    Args:
        mission_name: Mission name

    Returns:
        Average payout or 0 if unknown
    """
    info = get_mission_info(mission_name)
    if info:
        return (info.min_payout + info.max_payout) // 2
    return 0
