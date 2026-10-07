"""SQLAlchemy ORM models for the receipt price intelligence application."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import get_engine
from app.logging_config import get_logger

logger = get_logger("models")


def generate_uuid():
    """Generate a UUID string for primary keys."""
    return str(uuid.uuid4())


# Base classes - using declarative base pattern
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    """Application users."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True)
    display_name = Column(String(255))
    timezone = Column(String(50), nullable=False, default="UTC")
    currency_code = Column(String(3))
    role = Column(String(50), nullable=False, default="USER")
    # "Let the Household Assistant answer for me" (app/tools.py; How the app sees you)
    assistant_ok = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    receipt_drafts = relationship("ReceiptDraft", back_populates="user")
    receipts = relationship("Receipt", back_populates="user")
    common_items = relationship("CommonItem", back_populates="user")
    recommendations = relationship("Recommendation", back_populates="user")


class Home(Base):
    """A household. Receipts, items, and price history belong to a home.

    Every signed-in Home Assistant user can view and add data in any home; only
    administrators create, rename, or delete homes.
    """

    __tablename__ = "homes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    # Where trips start and end (used by the trip planner)
    address = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    created_by_user_id = Column(String(36), ForeignKey("users.id"))
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class Alert(Base):
    """Something worth telling the household about (a price target reached, a big saving, ...)."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("home_id", "dedupe_key", name="uq_alert_home_key"),)

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(30), nullable=False)  # PRICE_TARGET | NEW_LOW | SAVING | PRICE_DROP | RESTOCK
    dedupe_key = Column(String(255), nullable=False)  # the same event is only ever raised once
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    data = Column(Text)  # JSON
    dismissed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class PriceTarget(Base):
    """A price the household wants to be told about: below a number, or a new low."""

    __tablename__ = "price_targets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False)
    common_item_id = Column(String(36), ForeignKey("common_items.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String(20), nullable=False, default="BELOW")  # BELOW | NEW_LOW
    target_price = Column(Float)  # per ``unit`` (BELOW only)
    unit = Column(String(20))
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    last_triggered_at = Column(DateTime)


class Budget(Base):
    """A monthly spending limit for a home: for one category, or for everything when ``category`` is empty."""

    __tablename__ = "budgets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(100))  # NULL = the whole month's spending
    monthly_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class LineCorrection(Base):
    """A fix a person made to how a store's receipt line was read, applied to future receipts from that store."""

    __tablename__ = "line_corrections"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id", ondelete="CASCADE"), nullable=False)
    original_key = Column(String(255), nullable=False)  # normalized text as it was read
    corrected_text = Column(String(500), nullable=False)
    times = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, server_default=func.current_timestamp())


class WebHours(Base):
    """Opening hours for a store location found on the web, waiting for a person to accept them."""

    __tablename__ = "web_hours_suggestions"

    store_location_id = Column(String(36), ForeignKey("store_locations.id", ondelete="CASCADE"), primary_key=True)
    hours = Column(Text, nullable=False)  # JSON, same format as store_locations.opening_hours
    source_url = Column(String(1000))
    confidence = Column(Float)
    note = Column(String(200))
    status = Column(String(20), nullable=False, default="SUGGESTED")  # SUGGESTED | APPLIED | DISMISSED
    fetched_at = Column(DateTime, nullable=False)


class WebDeal(Base):
    """A promotion for one of your items found on the web (a store's weekly ad, a sale page)."""

    __tablename__ = "web_deals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id", ondelete="CASCADE"), nullable=False)
    common_item_id = Column(String(36), ForeignKey("common_items.id", ondelete="CASCADE"), nullable=False)
    product = Column(String(200), nullable=False)  # as the source names it
    price = Column(Float, nullable=False)
    unit = Column(String(10), nullable=False, default="each")
    valid_from = Column(String(10))
    valid_to = Column(String(10))
    conditions = Column(String(200))
    source_url = Column(String(1000))
    source_title = Column(String(300))
    # DEAL: a promotion (weekly ad, sale page). PRICE: the current shelf price on the store's website.
    kind = Column(String(10), nullable=False, default="DEAL")
    # How the price was read: "structured" (the page's product data), "text" (a price pattern in the
    # page text) or "model" (the model read the page, only when neither of the others found it)
    method = Column(String(12))
    fetched_at = Column(DateTime, nullable=False)


class AppSetting(Base):
    """Settings kept in the database as JSON by key: the App settings (``app.app_settings``, Admin → App settings) and
    what the Notifications page saved (key ``notifications``, which takes precedence over the notification App
    settings)."""

    __tablename__ = "app_settings"

    key = Column(String(80), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_by = Column(String(255))   # who changed an App setting (app.app_settings)


class OnlinePriceIgnore(Base):
    """An online price a home chose not to use: this item at this store chain uses its receipt price instead,
    everywhere (trip planner, shopping list, Home Assistant), and price checks skip it."""

    __tablename__ = "online_price_ignores"
    __table_args__ = (UniqueConstraint("home_id", "common_item_id", "store_chain_id", name="uq_online_price_ignore"),)

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True)
    common_item_id = Column(String(36), ForeignKey("common_items.id", ondelete="CASCADE"), nullable=False)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class StoreDiscount(Base):
    """A discount a home gets at a store chain: a store card or membership reward (see ``app/services/discounts.py``).

    ``percent`` off prices in scope, and for fuel optionally ``cents_per_unit`` off per gallon (or litre).
    ``applies_to``: ALL, FUEL (fuel only) or NON_FUEL (everything but fuel).
    """

    __tablename__ = "store_discounts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    percent = Column(Float, nullable=False, default=0.0)
    cents_per_unit = Column(Float, nullable=False, default=0.0)
    applies_to = Column(String(10), nullable=False, default="ALL")
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ShoppingListItem(Base):
    """An item on a home's shopping list (see ``app/services/shoplist.py``).

    Linked to a known item (``common_item_id``) when it is one you have bought before, so the list can say where it is
    cheapest; otherwise just ``text``. ``ha_uid`` is the matching item of the Home Assistant to-do list it syncs with.
    """

    __tablename__ = "shopping_list_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True)
    common_item_id = Column(String(36), ForeignKey("common_items.id", ondelete="SET NULL"))
    text = Column(String(200), nullable=False)
    qty = Column(Float, nullable=False, default=1.0)
    checked = Column(Integer, nullable=False, default=0)
    ha_uid = Column(String(100))
    ha_status = Column(String(20))  # the to-do item's status at the last sync: tells which side changed since
    added_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    checked_at = Column(DateTime)


class NearbyStore(Base):
    """A grocery store near a home found on OpenStreetMap (see ``app/services/nearby.py``).

    ``status``: NEW (found, nothing decided), ADDED (added as one of the home's stores, see
    ``store_location_id``), KNOWN (a store the home already buys from, ``matched_location_id``) or HIDDEN.
    """

    __tablename__ = "nearby_stores"
    __table_args__ = (UniqueConstraint("home_id", "osm_id", name="uq_nearby_stores_home_osm"),)

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False)
    osm_id = Column(String(40), nullable=False)  # "node/123", "way/456"
    name = Column(String(200), nullable=False)
    brand = Column(String(200))
    shop = Column(String(40))
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    distance_km = Column(Float)
    street = Column(String(255))
    city = Column(String(100))
    state = Column(String(100))
    postal_code = Column(String(20))
    raw_address = Column(Text)
    opening_hours_raw = Column(String(255))  # as written on OpenStreetMap
    opening_hours = Column(Text)  # JSON in the app's format, when it could be read
    website = Column(String(500))
    store_chain_id = Column(String(36), ForeignKey("store_chains.id", ondelete="SET NULL"))
    matched_location_id = Column(String(36), ForeignKey("store_locations.id", ondelete="SET NULL"))
    store_location_id = Column(String(36), ForeignKey("store_locations.id", ondelete="SET NULL"))
    status = Column(String(10), nullable=False, default="NEW")
    fetched_at = Column(DateTime, nullable=False)


class Vehicle(Base):
    """A car a home uses for shopping trips (drives the fuel or electricity cost of a trip).

    Values are stored in metric: ``consumption`` is litres per 100 km for fuel cars and kWh per
    100 km for electric ones; ``energy_price`` is per litre or per kWh.
    """

    __tablename__ = "vehicles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    kind = Column(String(20), nullable=False, default="GAS")  # GAS | DIESEL | HYBRID | EV
    consumption = Column(Float, nullable=False)
    energy_price = Column(Float, nullable=False)
    other_cost_per_km = Column(Float, nullable=False, default=0.0)  # wear, tyres, depreciation (optional)
    range_km = Column(Float)  # full-charge or full-tank range (optional, used for a warning)
    is_default = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class Address(Base):
    """Physical addresses for store locations."""

    __tablename__ = "addresses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    raw_address = Column(Text)
    street_line_1 = Column(String(255))
    street_line_2 = Column(String(255))
    city = Column(String(100))
    state_province = Column(String(100))
    postal_code = Column(String(20))
    country_code = Column(String(2))
    latitude = Column(Float)
    longitude = Column(Float)
    geocoding_confidence = Column(Float)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    store_locations = relationship("StoreLocation", back_populates="address")


class StoreChain(Base):
    """Store chains (e.g., Walmart, Target)."""

    __tablename__ = "store_chains"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False, unique=True)
    # The chain's own web pages, used by online lookups before searching (see app/services/webinfo.py):
    # its site (searches are limited to it), a product search page with {query}, its weekly ad page
    website = Column(String(500))
    search_url = Column(String(500))
    deals_url = Column(String(500))
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    store_locations = relationship("StoreLocation", back_populates="store_chain")


class StoreChainAlias(Base):
    """Another printed name that resolves to a chain (left behind by renames and merges)."""

    __tablename__ = "store_chain_aliases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id", ondelete="CASCADE"), nullable=False)
    normalized_alias = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class StoreLocation(Base):
    """Individual store locations."""

    __tablename__ = "store_locations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id"), nullable=False)
    address_id = Column(String(36), ForeignKey("addresses.id"))
    name = Column(String(255))
    # Weekly opening hours as JSON, see app/services/planner.py parse_hours (NULL = unknown)
    opening_hours = Column(Text)
    page_url = Column(String(500))  # this store's own web page (hours are read from it before searching)
    store_number = Column(String(50))
    latitude = Column(Float)
    longitude = Column(Float)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    store_chain = relationship("StoreChain", back_populates="store_locations")
    address = relationship("Address", back_populates="store_locations")
    receipts = relationship("Receipt", back_populates="store_location")
    store_tax_observations = relationship("StoreTaxObservation", back_populates="store_location")


class ReceiptDraft(Base):
    """Temporary receipt drafts pending user review."""

    __tablename__ = "receipt_drafts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    home_id = Column(String(36), ForeignKey("homes.id"))
    status = Column(String(50), nullable=False, default="PROCESSING")
    # Set when the draft is approved: the permanent receipt created from it
    approved_receipt_id = Column(String(36))
    source = Column(String(50), nullable=False)
    raw_ocr_text = Column(Text)
    raw_llm_output = Column(Text)
    extraction_model = Column(String(255))
    extraction_prompt_version = Column(String(50))
    extraction_schema_version = Column(String(50))
    created_at = Column(DateTime, server_default=func.current_timestamp())
    expires_at = Column(DateTime)

    # Relationships
    user = relationship("User", back_populates="receipt_drafts")
    items = relationship("ReceiptDraftItem", back_populates="receipt_draft")
    taxes = relationship("ReceiptDraftTax", back_populates="receipt_draft")
    images = relationship("ReceiptImage", back_populates="draft")


class ReceiptDraftItem(Base):
    """Items in a receipt draft."""

    __tablename__ = "receipt_draft_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    receipt_draft_id = Column(String(36), ForeignKey("receipt_drafts.id", ondelete="CASCADE"), nullable=False)
    receipt_description = Column(String(500), nullable=False)
    original_description = Column(String(500))  # the line as first read, before any correction
    suggested_common_item_id = Column(String(36))
    suggested_common_item_name = Column(String(255))
    item_type = Column(String(50))
    quantity = Column(Float)
    weight_value = Column(Float)
    weight_unit = Column(String(50))
    unit_price = Column(Float)
    unit_price_unit = Column(String(50))
    line_subtotal = Column(Float)
    discount_amount = Column(Float)
    line_total = Column(Float)
    tax_amount = Column(Float)
    extraction_confidence = Column(Float)
    normalization_confidence = Column(Float)
    raw_text = Column(Text)
    receipt_draft = relationship("ReceiptDraft", back_populates="items")

    @property
    def filled_note(self) -> str | None:
        """What was filled in from the last purchase at this store ("about 3.15 lb, from ..."), if anything."""
        import json as _json
        try:
            return ((_json.loads(self.raw_text or "{}") or {}).get("filled_from_last_time") or {}).get("note")
        except (ValueError, AttributeError):
            return None


class ReceiptDraftTax(Base):
    """Tax records in a receipt draft."""

    __tablename__ = "receipt_draft_taxes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    receipt_draft_id = Column(String(36), ForeignKey("receipt_drafts.id", ondelete="CASCADE"), nullable=False)
    tax_type = Column(String(50))
    tax_name = Column(String(100))
    tax_rate = Column(Float)
    taxable_amount = Column(Float)
    tax_amount = Column(Float)

    # Relationships
    receipt_draft = relationship("ReceiptDraft", back_populates="taxes")


class ReceiptImage(Base):
    """Receipt images (original and processed)."""

    __tablename__ = "receipt_images"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    receipt_draft_id = Column(String(36), ForeignKey("receipt_drafts.id", ondelete="CASCADE"))
    receipt_id = Column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"))
    image_type = Column(String(50), nullable=False)  # "original" or "processed"
    file_path = Column(String(500), nullable=False)
    mime_type = Column(String(100))
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    rotation_applied = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # Relationships
    draft = relationship("ReceiptDraft", back_populates="images")


class Receipt(Base):
    """Permanent receipt records."""

    __tablename__ = "receipts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    home_id = Column(String(36), ForeignKey("homes.id"))
    store_location_id = Column(String(36), ForeignKey("store_locations.id"))
    purchase_date = Column(String(10))
    purchase_time = Column(String(8))
    currency_code = Column(String(3))
    receipt_number = Column(String(100))
    transaction_number = Column(String(100))
    register_number = Column(String(50))
    subtotal = Column(Float)
    discount_total = Column(Float)
    tax_total = Column(Float)
    fee_total = Column(Float)
    grand_total = Column(Float)
    tags = Column(Text)  # JSON list, for example ["business", "warranty"]
    extraction_status = Column(String(50), nullable=False, default="UPLOADED")
    validation_status = Column(String(50))
    extraction_version = Column(String(50))
    extraction_model = Column(String(255))
    extraction_prompt_version = Column(String(50))
    extraction_schema_version = Column(String(50))
    raw_ocr_text = Column(Text)
    raw_llm_output = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    user = relationship("User", back_populates="receipts")
    store_location = relationship("StoreLocation", back_populates="receipts")
    items = relationship("ReceiptItem", back_populates="receipt")


class CommonItem(Base):
    """Normalized common items across receipts."""

    __tablename__ = "common_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    home_id = Column(String(36), ForeignKey("homes.id"))
    name = Column(String(255), nullable=False)
    # 1 once a person has chosen or confirmed the name; 0 while it is only the printed text
    name_confirmed = Column(Integer, nullable=False, default=0)
    category = Column(String(100))
    brand = Column(String(100))
    variant = Column(String(100))
    default_unit = Column(String(50))
    status = Column(String(50), nullable=False, default="ACTIVE")
    merged_into_id = Column(String(36))
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    user = relationship("User", back_populates="common_items")
    aliases = relationship("CommonItemAlias", back_populates="common_item")


class CommonItemAlias(Base):
    """Aliases for common items (learned from receipt descriptions)."""

    __tablename__ = "common_item_aliases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    common_item_id = Column(String(36), ForeignKey("common_items.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String(255), nullable=False)
    normalized_alias = Column(String(255), nullable=False)
    source = Column(String(50), nullable=False)  # "EXTRACTION", "USER_ADDED", etc.
    confidence = Column(Float)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # Relationships
    user = relationship("User")
    common_item = relationship("CommonItem", back_populates="aliases")


class ReceiptItem(Base):
    """Items on a permanent receipt."""

    __tablename__ = "receipt_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    receipt_id = Column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False)
    common_item_id = Column(String(36), ForeignKey("common_items.id"))
    receipt_description = Column(String(500), nullable=False)
    normalized_description = Column(String(255))
    brand = Column(String(100))
    variant = Column(String(100))
    item_type = Column(String(50), nullable=False, default="UNKNOWN")
    quantity = Column(Float)
    weight_value = Column(Float)
    weight_unit = Column(String(50))
    unit_price = Column(Float)
    unit_price_unit = Column(String(50))
    line_subtotal = Column(Float)
    discount_amount = Column(Float)
    line_total = Column(Float)
    tax_amount = Column(Float)
    raw_text = Column(Text)
    extraction_confidence = Column(Float)
    normalization_confidence = Column(Float)
    normalization_status = Column(String(50), nullable=False, default="UNRESOLVED")
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # Relationships
    receipt = relationship("Receipt", back_populates="items")


class TaxRecord(Base):
    """Tax records from receipts."""

    __tablename__ = "tax_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    receipt_id = Column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False)
    receipt_item_id = Column(String(36), ForeignKey("receipt_items.id", ondelete="SET NULL"))
    tax_type = Column(String(50), nullable=False)
    tax_name = Column(String(100))
    tax_rate = Column(Float)
    taxable_amount = Column(Float)
    tax_amount = Column(Float)
    jurisdiction = Column(String(100))
    source = Column(String(50), nullable=False, default="RECEIPT")
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # Relationships
    receipt = relationship("Receipt")
    receipt_item = relationship("ReceiptItem")


class PriceObservation(Base):
    """Price observations from approved receipts."""

    __tablename__ = "price_observations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    home_id = Column(String(36), ForeignKey("homes.id"))
    common_item_id = Column(String(36), ForeignKey("common_items.id"), nullable=False)
    receipt_item_id = Column(String(36), ForeignKey("receipt_items.id"), nullable=False)
    store_chain_id = Column(String(36), ForeignKey("store_chains.id"), nullable=False)
    store_location_id = Column(String(36), ForeignKey("store_locations.id"), nullable=False)
    purchase_date = Column(String(10), nullable=False)
    quantity = Column(Float)
    weight_value = Column(Float)
    weight_unit = Column(String(50))
    unit_price = Column(Float)
    unit_price_unit = Column(String(50))
    line_total = Column(Float)
    currency_code = Column(String(3), nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # Relationships (using backref to avoid conflicts)
    user = relationship("User", backref="price_observations")
    common_item = relationship("CommonItem", backref="price_observations")
    store_chain = relationship("StoreChain", backref="price_observations")
    store_location = relationship("StoreLocation", backref="price_observations")

    __table_args__ = (
        Index("idx_price_item_store_date", "common_item_id", "store_location_id", "purchase_date"),
    )


class StoreTaxObservation(Base):
    """Observed tax rates at specific stores."""

    __tablename__ = "store_tax_observations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    store_location_id = Column(String(36), ForeignKey("store_locations.id"), nullable=False)
    receipt_id = Column(String(36), ForeignKey("receipts.id"), nullable=False)
    tax_name = Column(String(100))
    tax_rate = Column(Float)
    taxable_amount = Column(Float)
    tax_amount = Column(Float)
    observation_date = Column(String(10), nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # Relationships
    store_location = relationship("StoreLocation", back_populates="store_tax_observations")
    receipt = relationship("Receipt")


class ItemPurchaseStats(Base):
    """Aggregated purchase statistics per item per user."""

    __tablename__ = "item_purchase_stats"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    common_item_id = Column(String(36), ForeignKey("common_items.id", ondelete="CASCADE"), nullable=False)
    purchase_count = Column(Integer, nullable=False, default=0)
    average_days_between_purchases = Column(Float)
    last_purchase_date = Column(String(10))
    average_quantity = Column(Float)
    average_weight = Column(Float)
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("user_id", "common_item_id", name="uq_user_item_stats"),
    )


class Recommendation(Base):
    """Price-based recommendations for users."""

    __tablename__ = "recommendations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recommendation_type = Column(String(50), nullable=False)
    common_item_id = Column(String(36), ForeignKey("common_items.id"))
    store_location_id = Column(String(36), ForeignKey("store_locations.id"))
    comparison_store_location_id = Column(String(36), ForeignKey("store_locations.id"))
    title = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    potential_savings = Column(Float)
    savings_percent = Column(Float)
    confidence = Column(Float)
    freshness_score = Column(Float)
    status = Column(String(50), nullable=False, default="ACTIVE")
    generated_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())
    expires_at = Column(DateTime)

    # Relationships
    user = relationship("User", back_populates="recommendations")


class RecommendationSnapshot(Base):
    """The latest daily recommendation run for a home (one row per home, replaced each run)."""

    __tablename__ = "recommendation_snapshots"

    home_id = Column(String(36), ForeignKey("homes.id", ondelete="CASCADE"), primary_key=True)
    generated_at = Column(DateTime, nullable=False)  # UTC
    duration_ms = Column(Integer)
    status = Column(String(20), nullable=False, default="OK")  # OK | FAILED
    error = Column(Text)
    payload = Column(Text)  # JSON: see app/services/deals.py


class RecommendationEvidence(Base):
    """Evidence supporting a recommendation."""

    __tablename__ = "recommendation_evidence"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    recommendation_id = Column(String(36), ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False)
    price_observation_id = Column(String(36))
    receipt_id = Column(String(36))
    evidence_type = Column(String(50), nullable=False)
    evidence_value = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # Relationships
    recommendation = relationship("Recommendation")


class Job(Base):
    """Background job queue."""

    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_type = Column(String(50), nullable=False)
    payload = Column(Text, nullable=False)  # JSON payload
    status = Column(String(50), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    available_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        Index("idx_jobs_status_available", "status", "available_at"),
    )


class AuditLog(Base):
    """Audit trail for all significant operations."""

    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"))
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(36), nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class ExtractionRun(Base):
    """Records of model extraction attempts.

    Kept as a history of past reads, so deleting the receipt or draft it was for only detaches
    it (ON DELETE SET NULL), never deletes it. Existing databases keep whatever constraint they
    were created with (SQLite cannot alter one in place), so the code that deletes a receipt or
    draft also does this by hand first; see ``_detach_extraction_runs`` in drafts.py.
    """

    __tablename__ = "extraction_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    receipt_id = Column(String(36), ForeignKey("receipts.id", ondelete="SET NULL"))
    receipt_draft_id = Column(String(36), ForeignKey("receipt_drafts.id", ondelete="SET NULL"))
    model = Column(String(255), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    schema_version = Column(String(50), nullable=False)
    input_image_ids = Column(Text, nullable=False)  # JSON array of image IDs
    raw_output = Column(Text)
    status = Column(String(50), nullable=False)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())


# Columns added after the first release. ``create_all`` only creates missing tables, so
# existing databases get these through ALTER TABLE (additive and idempotent).
_ADDED_COLUMNS = {
    "receipt_drafts": [("home_id", "TEXT REFERENCES homes(id)"), ("approved_receipt_id", "TEXT")],
    "common_items": [("home_id", "TEXT REFERENCES homes(id)"), ("name_confirmed", "INTEGER NOT NULL DEFAULT 0")],
    "price_observations": [("home_id", "TEXT REFERENCES homes(id)")],
    "homes": [("address", "TEXT"), ("latitude", "REAL"), ("longitude", "REAL")],
    "store_locations": [("opening_hours", "TEXT"), ("page_url", "TEXT")],
    "store_chains": [("website", "TEXT"), ("search_url", "TEXT"), ("deals_url", "TEXT")],
    "receipts": [("home_id", "TEXT REFERENCES homes(id)"), ("tags", "TEXT")],
    "receipt_draft_items": [("original_description", "TEXT")],
    "web_deals": [("kind", "TEXT NOT NULL DEFAULT 'DEAL'"), ("method", "TEXT")],
    "app_settings": [("updated_by", "VARCHAR(255)")],
    "users": [("assistant_ok", "INTEGER NOT NULL DEFAULT 1")],
}
_ADDED_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_drafts_home ON receipt_drafts(home_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_receipts_home ON receipts(home_id)",
    "CREATE INDEX IF NOT EXISTS idx_common_items_home ON common_items(home_id)",
    "CREATE INDEX IF NOT EXISTS idx_price_home_item ON price_observations(home_id, common_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_price_home_date ON price_observations(home_id, purchase_date)",
    "CREATE INDEX IF NOT EXISTS idx_receipts_home_date ON receipts(home_id, purchase_date)",
]


def ensure_columns(engine) -> None:
    """Add columns introduced after the first release to existing databases."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    logger.info("Added column %s.%s", table, name)
        for statement in _ADDED_INDEXES:
            conn.execute(text(statement))


# Indexes for the lookups the app makes most (by home, item, store and date). Created at start-up if missing, so
# databases made by older versions get them too; each is harmless if it already exists.
_INDEXES = {
    "idx_receipts_home_date": ("receipts", "home_id, purchase_date"),
    "idx_receipts_location": ("receipts", "store_location_id"),
    "idx_prices_home_date": ("price_observations", "home_id, purchase_date"),
    "idx_prices_home_item": ("price_observations", "home_id, common_item_id"),
    "idx_prices_receipt_item": ("price_observations", "receipt_item_id"),
    "idx_receipt_items_receipt": ("receipt_items", "receipt_id"),
    "idx_receipt_items_item": ("receipt_items", "common_item_id"),
    "idx_items_home_status": ("common_items", "home_id, status"),
    "idx_item_aliases_item": ("common_item_aliases", "common_item_id"),
    "idx_locations_chain": ("store_locations", "store_chain_id"),
    "idx_chain_aliases_chain": ("store_chain_aliases", "store_chain_id"),
    "idx_web_deals_home_pair": ("web_deals", "home_id, common_item_id, store_chain_id"),
    "idx_web_deals_home_fetched": ("web_deals", "home_id, fetched_at"),
    "idx_web_hours_location": ("web_hours_suggestions", "store_location_id"),
    "idx_nearby_home": ("nearby_stores", "home_id"),
    "idx_drafts_home_status": ("receipt_drafts", "home_id, status"),
    "idx_images_draft": ("receipt_images", "receipt_draft_id"),
    "idx_images_receipt": ("receipt_images", "receipt_id"),
    "idx_alerts_home": ("alerts", "home_id"),
}


def ensure_indexes(engine) -> None:
    """Create the indexes in ``_INDEXES`` that are missing (a table that does not exist yet is skipped)."""
    from sqlalchemy import inspect, text

    tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for name, (table, columns) in _INDEXES.items():
            if table in tables:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))


def init_models():
    """Create all database tables and apply additive column migrations."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_columns(engine)
    try:
        ensure_indexes(engine)
    except Exception:  # noqa: BLE001 - an index is only for speed; never stop the app starting
        logger.exception("Could not create all indexes")
    logger.info("Database models initialized")
