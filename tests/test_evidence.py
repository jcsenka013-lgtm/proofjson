import pytest

from proofjson.evidence import verify_quote


@pytest.mark.parametrize(
    ("quote", "page", "verified", "method"),
    [
        ("Total due: 128.40", "Total due: 128.40\nThanks", True, "exact"),
        ("total   due: 128.40", "Total due: 128.40", True, "normalized"),
        ("Total: $128.40", "Total $128.40", True, "normalized"),
        ("ﬁle number: 42", "file number: 42", True, "normalized"),
        ("Totl due: 128.40", "Invoice\nTotal due: 128.40\nEnd", True, "fuzzy"),
        ("Total due: 128.41", "Total due: 128.40", False, "none"),
        ("Tax", "Total due: 128.40", False, "none"),
        ("Complimentary dessert included", "Total due  9.75\nPaid with Visa ending 4242", False, "none"),
        ("", "Total due: 128.40", False, "none"),
        ("Total due: 128.40", "", False, "none"),
    ],
)
def test_verify_quote(quote: str, page: str, verified: bool, method: str) -> None:
    match = verify_quote(quote, page)
    assert match.verified is verified
    assert match.method == method


def test_fuzzy_does_not_cross_a_digit_change_inside_a_long_page() -> None:
    page = "Header\n" + ("note " * 40) + "\nTotal due: 128.40\nFooter"
    assert verify_quote("Total due: 128.40", page).verified is True
    assert verify_quote("Total due: 999.40", page).verified is False
