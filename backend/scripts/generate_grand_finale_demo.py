"""Generate visibly synthetic source PDFs for the Grand Finale workflow demo."""
from pathlib import Path
import argparse

import fitz


def _text_page(page, heading: str, lines: list[str]) -> None:
    page.insert_text((48, 48), "SYNTHETIC TRAINING EXHIBIT · NOT A REAL BUSINESS RECORD", fontsize=8, color=(0.5, 0.1, 0.1))
    page.insert_text((48, 86), heading, fontsize=17, fontname="helv")
    for index, line in enumerate(lines):
        page.insert_text((52, 132 + index * 34), line, fontsize=11 if index != len(lines) - 1 else 4)


def create(output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    invoice_path = output / "SYNTHETIC_Apex_Invoice.pdf"
    invoice = fitz.open()
    page1 = invoice.new_page(width=612, height=792)
    _text_page(page1, "Apex Industrial Supplies · Invoice", [
        "Vendor ID: V-104 | Customer: Northstar Components Pvt Ltd",
        "Invoice Number: INV-778 | Invoice Date: 14 August 2026",
        "Precision bearing PBR-6205 · Quantity 50 · Unit price INR 1,200",
        "Industrial seal IS-204 · Quantity 100 · Unit price INR 450",
        "Coupling kit CPK-310 · Quantity 10 · Unit price INR 3,500",
        "Subtotal INR 140,000 · GST INR 25,200",
        "Invoice total INR 165,200",
        "Synthetic small-type review marker · generated solely for this demo",
    ])
    page2 = invoice.new_page(width=612, height=792)
    _text_page(page2, "Invoice terms · supporting page", [
        "Vendor ID: V-104 | Invoice Number: INV-778",
        "Payment due 04 September 2026",
        "Goods are subject to the synthetic demonstration terms.",
    ])
    invoice.save(invoice_path)
    invoice.close()

    ledger_path = output / "SYNTHETIC_Northstar_Ledger.pdf"
    ledger = fitz.open()
    page = ledger.new_page(width=612, height=792)
    _text_page(page, "Northstar Components · Vendor Ledger", [
        "Company: Northstar Components Pvt Ltd",
        "Vendor ID: V-104 | Period: August 2026",
        "14 August · Reference INV-778 · Debit INR 85,000",
        "20 August · Reference NEFT-4417 · Credit INR 85,000",
        "Recorded invoice amount INR 85,000; settlement INR 85,000",
        "Synthetic training exhibit only. Values are invented.",
    ])
    ledger.save(ledger_path)
    ledger.close()
    return [invoice_path, ledger_path]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("demo_fixtures/grand_finale"))
    for path in create(parser.parse_args().output):
        print(path.resolve())
