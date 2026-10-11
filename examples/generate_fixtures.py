"""Regenerate the sample PDFs, cassettes, and expected JSON.

The documents are fictional. Names, addresses, and the taxpayer id 000-00-0000
are placeholders. Nothing here is a real person, invoice, or government form.

    uv sync --extra dev
    uv run python examples/generate_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from proofjson.evidence import verify_quote
from proofjson.paths import unflatten
from proofjson.pdf import read_pdf
from proofjson.validate import load_schema, order_like_schema, validation_messages

ROOT = Path(__file__).resolve().parent

INVOICE_PAGE_1 = [
    "NORTHWIND TRADING CO.",
    "100 Demo Street",
    "Springfield, IL 62701",
    "",
    "INVOICE",
    "Invoice number: INV-1042",
    "Invoice date: 2026-03-14",
    "Due date: 2026-04-13",
    "",
    "Bill to",
    "Ada Example",
    "200 Sample Avenue",
    "Shelbyville, IL 62565",
    "",
    "2  Canvas tote bag  48.00",
    "1  Steel water bottle  24.00",
]

INVOICE_PAGE_2 = [
    "INVOICE INV-1042 continued",
    "",
    "3  Linen notebook  36.00",
    "",
    "Subtotal: 108.00",
    "Tax: 20.40",
    "Total due: 128.40",
    "",
    "Payment terms: Net 30",
    "Notes: Thank you for your demo order.",
]

RECEIPT_PAGE = [
    "CORNER CAFE",
    "18 Market Lane",
    "Springfield, IL 62701",
    "",
    "RECEIPT",
    "Order: R-8831",
    "Date: 2026-06-02",
    "",
    "Drip coffee  4.50",
    "Blueberry muffin  5.25",
    "Total due  9.75",
    "",
    "Paid with Visa ending 4242",
]

VENDOR_PAGE = [
    "VENDOR INFORMATION RECORD",
    "Sample public-domain form. Not an official government document.",
    "",
    "Legal name: Sam Placeholder",
    "Business name: Placeholder Goods LLC",
    "Street: 1 Example Avenue",
    "City: Springfield",
    "State: IL",
    "ZIP: 62701",
    "Taxpayer ID: 000-00-0000",
    "Signed date: 2026-01-15",
]

FIXTURES = [
    {
        "directory": "invoice",
        "pdf": "invoice.pdf",
        "title": "Northwind Trading invoice INV-1042 (sample)",
        "pages": [INVOICE_PAGE_1, INVOICE_PAGE_2],
        "fields": [
            {"path": "vendor.name", "value": "Northwind Trading Co.", "quote": "NORTHWIND TRADING CO.", "page": 1, "confidence": 1},
            {"path": "vendor.city", "value": "Springfield", "quote": "Springfield, IL 62701", "page": 1, "confidence": 0.9},
            {"path": "invoice_number", "value": "INV-1042", "quote": "Invoice number: INV-1042", "page": 1, "confidence": 1},
            {"path": "invoice_date", "value": "2026-03-14", "quote": "Invoice date: 2026-03-14", "page": 1, "confidence": 1},
            {"path": "due_date", "value": "2026-04-13", "quote": "Due date: 2026-04-13", "page": 1, "confidence": 0.9},
            {"path": "bill_to.name", "value": "Ada Example", "quote": "Ada Example", "page": 1, "confidence": 1},
            {"path": "bill_to.city", "value": "Shelbyville", "quote": "Shelbyville, IL 62565", "page": 1, "confidence": 0.9},
            {"path": "line_items[0].description", "value": "Canvas tote bag", "quote": "Canvas tote bag", "page": 1, "confidence": 1},
            {"path": "line_items[0].qty", "value": 2, "quote": "2  Canvas tote bag  48.00", "page": 1, "confidence": 1},
            {"path": "line_items[0].amount", "value": 48.0, "quote": "2  Canvas tote bag  48.00", "page": 1, "confidence": 1},
            {"path": "line_items[1].description", "value": "Steel water bottle", "quote": "Steel water bottle", "page": 1, "confidence": 1},
            {"path": "line_items[1].qty", "value": 1, "quote": "1  Steel water bottle  24.00", "page": 1, "confidence": 1},
            {"path": "line_items[1].amount", "value": 24.0, "quote": "1  Steel water bottle  24.00", "page": 1, "confidence": 1},
            {"path": "line_items[2].description", "value": "Linen notebook", "quote": "Linen notebook", "page": 2, "confidence": 1},
            {"path": "line_items[2].qty", "value": 3, "quote": "3  Linen notebook  36.00", "page": 2, "confidence": 1},
            {"path": "line_items[2].amount", "value": 36.0, "quote": "3  Linen notebook  36.00", "page": 2, "confidence": 1},
            {"path": "subtotal", "value": 108.0, "quote": "Subtotal: 108.00", "page": 2, "confidence": 1},
            {"path": "tax", "value": 20.4, "quote": "Tax: 20.40", "page": 2, "confidence": 1},
            {"path": "total", "value": 128.4, "quote": "Total due: 128.40", "page": 2, "confidence": 1},
            {"path": "notes", "value": "Thank you for your demo order.", "quote": "Notes: Thank you for your demo order.", "page": 2, "confidence": 0.75},
        ],
    },
    {
        "directory": "receipt",
        "pdf": "receipt.pdf",
        "title": "Corner Cafe receipt R-8831 (sample)",
        "pages": [RECEIPT_PAGE],
        "fields": [
            {"path": "merchant", "value": "Corner Cafe", "quote": "CORNER CAFE", "page": 1, "confidence": 1},
            {"path": "order_number", "value": "R-8831", "quote": "Order: R-8831", "page": 1, "confidence": 1},
            {"path": "date", "value": "2026-06-02", "quote": "Date: 2026-06-02", "page": 1, "confidence": 1},
            {"path": "items[0].name", "value": "Drip coffee", "quote": "Drip coffee", "page": 1, "confidence": 1},
            {"path": "items[0].amount", "value": 4.5, "quote": "Drip coffee  4.50", "page": 1, "confidence": 1},
            {"path": "items[1].name", "value": "Blueberry muffin", "quote": "Blueberry muffin", "page": 1, "confidence": 1},
            {"path": "items[1].amount", "value": 5.25, "quote": "Blueberry muffin  5.25", "page": 1, "confidence": 1},
            {
                "path": "total",
                "value": 9.75,
                "quote": "Complimentary dessert included",
                "page": 1,
                "confidence": 0.9,
                "expect_verified": False,
            },
            {"path": "payment", "value": "Visa ending 4242", "quote": "Paid with Visa ending 4242", "page": 1, "confidence": 1},
        ],
    },
    {
        "directory": "vendor_form",
        "pdf": "vendor_form.pdf",
        "title": "Vendor information record (sample, not a government form)",
        "pages": [VENDOR_PAGE],
        "fields": [
            {"path": "legal_name", "value": "Sam Placeholder", "quote": "Legal name: Sam Placeholder", "page": 1, "confidence": 1},
            {"path": "business_name", "value": "Placeholder Goods LLC", "quote": "Business name: Placeholder Goods LLC", "page": 1, "confidence": 1},
            {"path": "street", "value": "1 Example Avenue", "quote": "Street: 1 Example Avenue", "page": 1, "confidence": 1},
            {"path": "city", "value": "Springfield", "quote": "City: Springfield", "page": 1, "confidence": 1},
            {"path": "state", "value": "IL", "quote": "State: IL", "page": 1, "confidence": 1},
            {"path": "postal_code", "value": "62701", "quote": "ZIP: 62701", "page": 1, "confidence": 1},
            {"path": "taxpayer_id", "value": "000-00-0000", "quote": "Taxpayer ID: 000-00-0000", "page": 1, "confidence": 1},
            {"path": "signed_date", "value": "2026-01-15", "quote": "Signed date: 2026-01-15", "page": 1, "confidence": 1},
        ],
    },
]


def draw_pdf(path: Path, title: str, pages: list[list[str]]) -> None:
    document = canvas.Canvas(str(path), pagesize=letter)
    document.setTitle(title)
    document.setAuthor("proofjson examples")
    for lines in pages:
        document.setFont("Helvetica", 12)
        y = 740
        for line in lines:
            if line:
                document.drawString(72, y, line)
            y -= 18
        document.showPage()
    document.save()


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    for spec in FIXTURES:
        directory = ROOT / spec["directory"]
        directory.mkdir(parents=True, exist_ok=True)
        pdf_path = directory / spec["pdf"]
        draw_pdf(pdf_path, spec["title"], spec["pages"])
        pages = read_pdf(pdf_path)
        failures: list[str] = []
        cassette_pages: dict[str, list[dict[str, object]]] = {}
        for field in spec["fields"]:
            page = pages[field["page"] - 1]
            match = verify_quote(str(field["quote"]), page.text)
            expected = field.get("expect_verified", True)
            if match.verified is not expected:
                failures.append(
                    f"{spec['directory']} {field['path']} verified={match.verified} method={match.method}"
                )
            item = {
                "path": field["path"],
                "value": field["value"],
                "quote": field["quote"],
                "page": field["page"],
                "confidence": field["confidence"],
            }
            cassette_pages.setdefault(str(field["page"]), []).append(item)
        if failures:
            extracted = "\n".join(f"--- page {page.number} ---\n{page.text}" for page in pages)
            raise SystemExit("Quote check failed:\n" + "\n".join(failures) + "\n\n" + extracted)
        schema = load_schema(directory / "schema.json")
        flat = {field["path"]: field["value"] for field in spec["fields"]}
        data = order_like_schema(unflatten(flat), schema, schema)
        errors = validation_messages(schema, data)
        if errors:
            raise SystemExit(f"{spec['directory']} expected JSON is invalid:\n" + "\n".join(errors))
        write_json(directory / "cassette.json", {"pages": cassette_pages})
        write_json(directory / "expected.json", data)
        print(f"wrote {pdf_path.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
