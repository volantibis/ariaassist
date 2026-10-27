"""Mock booking tool. Validates the request, then writes a row to SQLite."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.db import BookingRow, Database
from app.tools.restaurants import Restaurant, is_open_at


class BookingError(ValueError):
    """Raised with a user-presentable message when a booking cannot be made."""


def validate_booking(restaurant: Restaurant, party_size: int, hhmm: str) -> None:
    if party_size < 1:
        raise BookingError("Party size must be at least 1.")
    if party_size > restaurant.capacity:
        raise BookingError(
            f"{restaurant.name} can seat at most {restaurant.capacity} people per booking."
        )
    if not is_open_at(restaurant, hhmm):
        raise BookingError(
            f"{restaurant.name} is open {restaurant.opens}-{restaurant.closes}, so {hhmm} won't work."
        )


async def book_table(
    db: Database, *, restaurant: Restaurant, party_size: int, hhmm: str, booked_by: str
) -> BookingRow:
    validate_booking(restaurant, party_size, hhmm)
    booking = BookingRow(
        id=uuid.uuid4().hex[:8].upper(),
        restaurant_id=restaurant.id,
        restaurant_name=restaurant.name,
        party_size=party_size,
        time=hhmm,
        booked_by=booked_by,
        ts=datetime.now(UTC).isoformat(),
    )
    await db.add_booking(booking)
    return booking


async def find_booking(
    db: Database, *, restaurant_id: str, booked_by: str
) -> BookingRow | None:
    """Most recent booking for this restaurant made by this person, if any."""
    bookings = await db.list_bookings()
    matches = [
        b for b in bookings
        if b.restaurant_id == restaurant_id and b.booked_by == booked_by
    ]
    return matches[0] if matches else None


async def cancel_booking(
    db: Database, *, booking_id: str, cancelled_by: str
) -> BookingRow:
    """Validate then delete a booking. Raises BookingError if nothing to cancel."""
    rows = await db.list_bookings()
    row = next((b for b in rows if b.id == booking_id), None)
    if row is None:
        raise BookingError("I couldn't find a booking like that to cancel.")
    if row.booked_by != cancelled_by:
        raise BookingError(
            f"You didn't book the table at {row.restaurant_name}, so I won't cancel it for you."
        )
    deleted = await db.delete_booking(booking_id)
    if not deleted:
        raise BookingError("The booking disappeared while we were talking.")
    return row
