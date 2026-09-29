"""The benchmark tools' argument clean-up: values reach the mock backend typed as a
real API would take them."""

from duet_voice.fdb_tools import coerce


def test_a_filter_value_is_a_number_a_yes_no_or_text():
    assert coerce("update_search_filter", {"filter_name": "max_price", "value": "2,400"})["value"] == 2400
    assert coerce("update_search_filter", {"filter_name": "min_bedrooms", "value": 3.0})["value"] == 3
    assert coerce("update_search_filter", {"filter_name": "pets_allowed", "value": "True"})["value"] is True
    assert coerce("update_search_filter", {"filter_name": "neighborhood", "value": "Riverside"})["value"] == "Riverside"
    # a code with a leading zero stays text
    assert coerce("update_search_filter", {"filter_name": "zip", "value": "02139"})["value"] == "02139"


def test_a_date_is_month_and_day_without_an_ordinal():
    assert coerce("search_flights", {"destination": "Oslo", "date": "October 4th"})["date"] == "October 4"
    assert coerce("search_flights", {"destination": "Oslo", "date": "2026-10-04"})["date"] == "2026-10-04"


def test_a_spelled_out_id_is_joined():
    assert coerce("add_to_cart", {"product_id": "K-2", "quantity": 1})["product_id"] == "K2"
    assert coerce("track_order", {"order_id": "W-W-W"})["order_id"] == "WWW"
    assert coerce("update_identity_doc", {"doc_type": "passport", "doc_number": "E77 2211"})["doc_number"] == "E772211"
    # anything that is not a plain spelled-out id is left alone
    assert coerce("track_order", {"order_id": "#A1/B2"})["order_id"] == "#A1/B2"


def test_numbers_and_counts_are_coerced_and_nulls_dropped():
    args = coerce("search_apartments", {"city": "Denver", "bedrooms": "2", "max_price": "$1,500", "pets_allowed": None})
    assert args == {"city": "Denver", "bedrooms": 2, "max_price": 1500.0}
