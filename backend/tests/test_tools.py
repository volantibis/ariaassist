import pytest

from app.chat.mentions import is_mention, strip_mention
from app.tools.bookings import BookingError, book_table, validate_booking
from app.tools.restaurants import (
    find_by_name,
    get_by_id,
    is_open_at,
    load_restaurants,
    parse_time,
    search_restaurants,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@Aria what's up", True),
        ("hey @aria", True),
        ("@ARIA, help", True),
        ("email me at bob@aria.com", False),
        ("@ariana hi", False),
        ("no mention", False),
    ],
)
def test_is_mention(text, expected):
    assert is_mention(text) is expected


def test_strip_mention():
    assert strip_mention("@Aria, book a table") == "book a table"
    assert strip_mention("hey @aria summarize") == "hey summarize"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8pm", "20:00"),
        ("8:30 pm", "20:30"),
        ("8 PM", "20:00"),
        ("20:00", "20:00"),
        ("12am", "00:00"),
        ("12pm", "12:00"),
        ("11am", "11:00"),
        ("7", "19:00"),  # no meridiem, hours 1-10 read as evening
        ("12", "12:00"),
        ("soon", None),
        ("25:00", None),
        ("", None),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_restaurant_data_loads_and_is_consistent():
    rs = load_restaurants()
    assert len(rs) == 20
    assert len({r.id for r in rs}) == 20
    assert all(1 <= r.price <= 3 for r in rs)


def test_search_filters_and_ordering():
    italian = search_restaurants(cuisine="italian", limit=10)
    assert {r.cuisine for r in italian} == {"italian"}
    assert italian == sorted(italian, key=lambda r: r.rating, reverse=True)
    assert all(r.price <= 1 for r in search_restaurants(max_price=1, limit=20))
    assert all(r.capacity >= 12 for r in search_restaurants(party_size=12, limit=20))
    assert search_restaurants(cuisine="klingon") == []
    assert search_restaurants(cuisine="indian", limit=20)  # substring: north/south indian


def test_find_by_name_exact_substring_and_fuzzy():
    assert find_by_name("Saffron Table").id == "r01"
    assert find_by_name("saffron").id == "r01"
    assert find_by_name("Safron Tabel").id == "r01"  # typo
    assert find_by_name("zzzz nothing") is None
    assert get_by_id("r07").name == "Tokyo Counter"


def test_is_open_at_handles_midnight_close():
    burger = get_by_id("r20")  # closes 00:00
    assert is_open_at(burger, "23:30")
    assert not is_open_at(burger, "11:00")
    tokyo = get_by_id("r07")  # 18:00-23:00
    assert not is_open_at(tokyo, "17:00")
    assert is_open_at(tokyo, "18:00")
    assert not is_open_at(tokyo, "23:00")


def test_validate_booking_rules():
    tokyo = get_by_id("r07")
    with pytest.raises(BookingError, match="at most 4"):
        validate_booking(tokyo, 8, "19:00")
    with pytest.raises(BookingError, match="open"):
        validate_booking(tokyo, 2, "17:00")
    with pytest.raises(BookingError):
        validate_booking(tokyo, 0, "19:00")
    validate_booking(tokyo, 2, "19:00")


async def test_book_table_persists(db):
    booking = await book_table(
        db, restaurant=get_by_id("r01"), party_size=4, hhmm="20:00", booked_by="bob"
    )
    rows = await db.list_bookings()
    assert [r.id for r in rows] == [booking.id]
    assert rows[0].restaurant_name == "Saffron Table"
