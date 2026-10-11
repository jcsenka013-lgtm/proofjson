import pytest

from proofjson.paths import flatten, parse_path, unflatten


def test_roundtrip_nested_document() -> None:
    document = {
        "invoice_number": "INV-1042",
        "vendor": {"name": "Northwind", "city": "Springfield"},
        "line_items": [
            {"description": "Tote", "qty": 2, "amount": 48.0},
            {"description": "Bottle", "qty": 1, "amount": 24.0},
        ],
    }
    assert unflatten(flatten(document)) == document


def test_parse_path_indexes() -> None:
    assert parse_path("line_items[0].amount") == ["line_items", 0, "amount"]
    assert parse_path("vendor.name") == ["vendor", "name"]


@pytest.mark.parametrize("path", ["", ".", "line_items[", "line_items[]", ".name", "vendor."])
def test_parse_path_rejects_garbage(path: str) -> None:
    with pytest.raises(ValueError):
        parse_path(path)
