"""Business definitions and data for GTA Online."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class BusinessCategory(Enum):
    """Categories of businesses."""

    MC = auto()  # Motorcycle Club businesses
    CEO = auto()  # CEO/Organization businesses
    PASSIVE = auto()  # Passive income businesses
    ACTIVE = auto()  # Require active work


@dataclass
class Business:
    """Definition of a GTA Online business."""

    id: str
    name: str
    category: BusinessCategory
    max_stock: int  # Maximum stock units
    max_value: int  # Maximum sell value (solo, local)
    max_value_bonus: int  # Maximum with bonus (far delivery)
    production_time: int  # Minutes to produce 1 unit (with supplies)
    full_production_time: int  # Minutes to fill from empty (approximate)
    supply_cost: int  # Cost to buy supplies
    supplies_per_full: int  # Supply purchases needed for full stock
    staff_upgrade_multiplier: float = 1.0  # Production boost with staff upgrade
    equipment_upgrade_multiplier: float = 1.0  # Production boost with equipment
    has_raid: bool = True  # Can be raided
    raid_timer: int = 0  # Minutes before raid possible (after selling)
    notes: str = ""


# MC Businesses (values with both upgrades)
COCAINE_LOCKUP = Business(
    id="cocaine",
    name="Cocaine Lockup",
    category=BusinessCategory.MC,
    max_stock=10,  # 10 bars
    max_value=420_000,
    max_value_bonus=546_000,
    production_time=30,  # 30 min per bar with upgrades
    full_production_time=300,  # 5 hours
    supply_cost=75_000,
    supplies_per_full=2,  # ~2.5 resupplies for full
    staff_upgrade_multiplier=1.25,
    equipment_upgrade_multiplier=1.4,
    raid_timer=240,
)

METH_LAB = Business(
    id="meth",
    name="Meth Lab",
    category=BusinessCategory.MC,
    max_stock=20,
    max_value=357_000,
    max_value_bonus=464_100,
    production_time=36,
    full_production_time=360,  # 6 hours
    supply_cost=75_000,
    supplies_per_full=2,
    staff_upgrade_multiplier=1.25,
    equipment_upgrade_multiplier=1.4,
    raid_timer=240,
)

CASH_FACTORY = Business(
    id="cash",
    name="Counterfeit Cash",
    category=BusinessCategory.MC,
    max_stock=40,
    max_value=294_000,
    max_value_bonus=382_200,
    production_time=24,
    full_production_time=240,  # 4 hours
    supply_cost=75_000,
    supplies_per_full=2,
    staff_upgrade_multiplier=1.25,
    equipment_upgrade_multiplier=1.4,
    raid_timer=240,
)

WEED_FARM = Business(
    id="weed",
    name="Weed Farm",
    category=BusinessCategory.MC,
    max_stock=80,
    max_value=252_000,
    max_value_bonus=327_600,
    production_time=24,
    full_production_time=320,
    supply_cost=75_000,
    supplies_per_full=2,
    staff_upgrade_multiplier=1.25,
    equipment_upgrade_multiplier=1.4,
    raid_timer=240,
)

DOCUMENT_FORGERY = Business(
    id="documents",
    name="Document Forgery",
    category=BusinessCategory.MC,
    max_stock=60,
    max_value=126_000,
    max_value_bonus=163_800,
    production_time=20,
    full_production_time=300,
    supply_cost=75_000,
    supplies_per_full=2,
    staff_upgrade_multiplier=1.25,
    equipment_upgrade_multiplier=1.4,
    raid_timer=240,
    notes="Lowest value MC business - often skipped",
)

# Bunker
BUNKER = Business(
    id="bunker",
    name="Bunker",
    category=BusinessCategory.CEO,
    max_stock=100,
    max_value=1_050_000,
    max_value_bonus=1_155_000,
    production_time=42,  # ~7 min per unit with upgrades
    full_production_time=700,  # ~11.5 hours
    supply_cost=75_000,
    supplies_per_full=5,
    staff_upgrade_multiplier=1.25,
    equipment_upgrade_multiplier=1.4,
    raid_timer=240,
)

# Nightclub (passive, accumulates from other businesses)
NIGHTCLUB = Business(
    id="nightclub",
    name="Nightclub Warehouse",
    category=BusinessCategory.PASSIVE,
    max_stock=360,  # Total across all goods
    max_value=1_690_000,  # Approximate max
    max_value_bonus=1_859_000,
    production_time=0,  # Varies by product
    full_production_time=3960,  # ~66 hours for everything
    supply_cost=0,  # No supply purchases
    supplies_per_full=0,
    has_raid=True,
    raid_timer=240,
    notes="Passive income from linked businesses",
)

# Agency (safe accumulates from contracts)
AGENCY = Business(
    id="agency",
    name="Agency",
    category=BusinessCategory.PASSIVE,
    max_stock=250_000,  # Safe capacity
    max_value=250_000,
    max_value_bonus=250_000,
    production_time=48,  # $500 every 48 min after enough contracts
    full_production_time=0,  # N/A
    supply_cost=0,
    supplies_per_full=0,
    has_raid=False,
    notes="Safe fills from completed Security Contracts",
)

# Acid Lab
ACID_LAB = Business(
    id="acid_lab",
    name="Acid Lab",
    category=BusinessCategory.MC,
    max_stock=160,
    max_value=325_000,
    max_value_bonus=422_500,
    production_time=24,
    full_production_time=384,
    supply_cost=60_000,
    supplies_per_full=2,
    has_raid=True,
    raid_timer=180,
)

# Vehicle Warehouse (not really stock-based but included)
VEHICLE_WAREHOUSE = Business(
    id="vehicle_warehouse",
    name="Vehicle Warehouse",
    category=BusinessCategory.ACTIVE,
    max_stock=40,  # Max stored vehicles
    max_value=100_000,  # Per top-range vehicle
    max_value_bonus=100_000,
    production_time=0,  # Source missions
    full_production_time=0,
    supply_cost=0,
    supplies_per_full=0,
    has_raid=False,
    notes="Source and sell vehicles - up to $80K profit per top range",
)

# Special Cargo
SPECIAL_CARGO = Business(
    id="special_cargo",
    name="Special Cargo Warehouse",
    category=BusinessCategory.ACTIVE,
    max_stock=111,  # Large warehouse
    max_value=2_220_000,
    max_value_bonus=2_220_000,  # No delivery bonus
    production_time=0,  # Source missions
    full_production_time=0,
    supply_cost=18_000,  # 3 crates average
    supplies_per_full=37,  # 37 x 3-crate missions
    has_raid=True,
    raid_timer=180,
    notes="High profit but very time consuming",
)

# All businesses dictionary
BUSINESSES: dict[str, Business] = {
    "cocaine": COCAINE_LOCKUP,
    "meth": METH_LAB,
    "cash": CASH_FACTORY,
    "weed": WEED_FARM,
    "documents": DOCUMENT_FORGERY,
    "bunker": BUNKER,
    "nightclub": NIGHTCLUB,
    "agency": AGENCY,
    "acid_lab": ACID_LAB,
    "vehicle_warehouse": VEHICLE_WAREHOUSE,
    "special_cargo": SPECIAL_CARGO,
}


def get_business(business_id: str) -> Optional[Business]:
    """Get a business by ID."""
    return BUSINESSES.get(business_id.lower())


def get_mc_businesses() -> list[Business]:
    """Get all MC businesses."""
    return [b for b in BUSINESSES.values() if b.category == BusinessCategory.MC]


def get_passive_businesses() -> list[Business]:
    """Get all passive income businesses."""
    return [b for b in BUSINESSES.values() if b.category == BusinessCategory.PASSIVE]


def calculate_value_per_hour(business: Business) -> float:
    """Calculate approximate $/hour for a business.

    Args:
        business: Business to calculate for

    Returns:
        Approximate dollars per hour
    """
    if business.full_production_time <= 0:
        return 0.0

    hours = business.full_production_time / 60
    return business.max_value / hours if hours > 0 else 0.0


def estimate_stock_value(business: Business, stock_percent: int) -> int:
    """Estimate current stock value.

    Args:
        business: Business type
        stock_percent: Stock level as percentage (0-100)

    Returns:
        Estimated value in dollars
    """
    return int(business.max_value * (stock_percent / 100))


def estimate_time_to_full(business: Business, current_stock_percent: int) -> int:
    """Estimate minutes until business is full.

    Args:
        business: Business type
        current_stock_percent: Current stock level (0-100)

    Returns:
        Estimated minutes to full
    """
    if business.full_production_time <= 0:
        return 0

    remaining_percent = 100 - current_stock_percent
    return int(business.full_production_time * (remaining_percent / 100))


def estimate_time_to_full_formatted(business: Business, current_stock_percent: int) -> str:
    """Get formatted time until business is full.

    Args:
        business: Business type
        current_stock_percent: Current stock level (0-100)

    Returns:
        Formatted time string (e.g., "2h 30m")
    """
    minutes = estimate_time_to_full(business, current_stock_percent)

    if minutes <= 0:
        return "Full"

    hours = minutes // 60
    remaining_mins = minutes % 60

    if hours > 0:
        return f"{hours}h {remaining_mins}m"
    return f"{remaining_mins}m"


def estimate_supplies_remaining(supply_percent: int, business: Business) -> int:
    """Estimate minutes until supplies run out.

    Args:
        supply_percent: Current supply level (0-100)
        business: Business type

    Returns:
        Estimated minutes until empty
    """
    if business.full_production_time <= 0 or business.supplies_per_full <= 0:
        return 0

    # Time for one bar of supplies (assuming ~20% per bar)
    time_per_supply_bar = business.full_production_time / business.supplies_per_full

    # Current supplies as fraction of one bar (100% = 5 bars worth for MC)
    bars_remaining = supply_percent / 20  # Each 20% is roughly one purchase worth
    return int(bars_remaining * time_per_supply_bar)


def get_optimal_sell_threshold(business: Business, solo: bool = True) -> int:
    """Get the optimal stock percentage to sell at.

    Args:
        business: Business type
        solo: Whether playing solo (affects vehicle count)

    Returns:
        Recommended stock percentage to sell at
    """
    # Solo players should sell at lower levels to guarantee one vehicle
    if solo:
        if business.category == BusinessCategory.MC:
            # MC businesses: 1 vehicle up to certain thresholds
            if business.id == "cocaine":
                return 50  # 2.5 bars = 1 vehicle
            elif business.id == "meth":
                return 50
            elif business.id == "cash":
                return 50
            else:
                return 50
        elif business.id == "bunker":
            return 25  # 25 units = 1 vehicle
        elif business.id == "acid_lab":
            return 50

    # With help, can sell full
    return 100


def get_sell_vehicle_count(business: Business, stock_percent: int) -> int:
    """Estimate number of sell vehicles needed.

    Args:
        business: Business type
        stock_percent: Current stock level

    Returns:
        Estimated vehicle count
    """
    if business.id == "bunker":
        # Bunker: 1 vehicle per 25 units (25%)
        return max(1, (stock_percent + 24) // 25)
    elif business.category == BusinessCategory.MC:
        # MC: Varies but roughly 1 vehicle per 25-50%
        return max(1, (stock_percent + 24) // 25)
    elif business.id == "nightclub":
        # Nightclub: Always 1 vehicle (Speedo/Mule/Pounder based on value)
        return 1
    elif business.id == "acid_lab":
        # Acid Lab: 1 vehicle up to certain point
        return max(1, (stock_percent + 49) // 50)

    return 1


@dataclass
class BusinessStatus:
    """Current status of a business with calculations."""

    business: Business
    stock_percent: int
    supply_percent: int
    last_updated_minutes: int = 0

    @property
    def estimated_value(self) -> int:
        """Get estimated current value."""
        return estimate_stock_value(self.business, self.stock_percent)

    @property
    def time_to_full(self) -> int:
        """Get minutes until full."""
        return estimate_time_to_full(self.business, self.stock_percent)

    @property
    def time_to_full_formatted(self) -> str:
        """Get formatted time to full."""
        return estimate_time_to_full_formatted(self.business, self.stock_percent)

    @property
    def supplies_remaining_minutes(self) -> int:
        """Get minutes until supplies empty."""
        return estimate_supplies_remaining(self.supply_percent, self.business)

    @property
    def vehicle_count(self) -> int:
        """Get estimated vehicle count for sell."""
        return get_sell_vehicle_count(self.business, self.stock_percent)

    @property
    def is_ready_to_sell(self) -> bool:
        """Check if ready for solo sell."""
        threshold = get_optimal_sell_threshold(self.business, solo=True)
        return self.stock_percent >= threshold

    @property
    def needs_supplies(self) -> bool:
        """Check if supplies are needed."""
        return self.supply_percent <= 20

    @property
    def status_text(self) -> str:
        """Get a short status text."""
        if self.stock_percent >= 100:
            return "FULL - Sell now!"
        elif self.is_ready_to_sell:
            return f"Ready ({self.vehicle_count} vehicle)"
        elif self.needs_supplies:
            return "Needs supplies!"
        else:
            return f"Full in {self.time_to_full_formatted}"
