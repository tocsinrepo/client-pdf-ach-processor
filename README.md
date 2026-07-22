# client-pdf-ach-processor

Gets donation ACH transactions ready for Development: upload the daily Atlantic
Union Bank **ACH Remittance Advice Detail Reports** (RAD), and each donation
credit is cut out onto its own page, highlighted, and named — the exact manual
process from March 2026, automated.

## What it does

1. **Detects** every transaction block in one or many RAD PDFs (both bank
   layout generations are handled — the column positions are measured per page,
   not assumed).
2. **Filters** to donation credits (transaction type 22/32, "Demand Credit");
   debits like TuitionExpress fees are excluded by default but can be toggled on.
3. **Cuts** each transaction to its own mini-page (vector clip of the block,
   with the report title header).
4. **Highlights** the five key fields in the exact green from the March files
   (RGB 6-138-28, 20% opacity, Multiply): DFI Account Number, ID Number,
   Settlement Date, Amount, Originator Name.
5. **Names** each file `YY.MM.DD.NN Donation ACH <Alias> $X,XXX.pdf` using the
   settlement date, an auto-assigned per-day counter (editable), and a payer
   alias map learned from March (Fidelity, BBGF, BBMS, FRU, Schwab, AOL,
   BidKit, Pledgeling, Amazon, Cyber Grant, America's Charity, …).
6. **Downloads** individually or as one ZIP for Development.

## The full monthly cycle this fits into

ACH site (achedi.com Payments Reporter) → daily RAD PDFs → **this app** →
`26.MM.DD.NN Donation ACH …` cut-outs → Development attaches gift backup →
returns `2026-XXX …-combined.pdf` + batch xlsx (`26.03 March - Processed`) →
filed to `202603 March` with the `DD-NN` prefix.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Files

- `engine.py` — parsing, cutting, highlighting, naming (no UI; importable/testable)
- `app.py` — Streamlit UI (upload → review table → ZIP)

Tested against the 2026-06-01 raw RAD report and nine March 2026 processed
examples spanning both layout variants.
