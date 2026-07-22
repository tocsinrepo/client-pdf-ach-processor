"""
ACH RAD processor engine — cut each transaction block out of an
Atlantic Union Bank "ACH Remittance Advice Detail Report" PDF,
apply Cornerstones' green field highlights, and name the output
files in the deposit convention.

Pattern reverse-engineered from 26.03 March - Processed examples:
  - one mini-page per transaction (~590 x 180-320 pts), vector clip of the block
  - 5 green highlights (RGB 0.0235, 0.5412, 0.1098):
      DFI Account Number, ID Number, Settlement Date, Amount, Originator Name
  - filename: YY.MM.DD.NN Donation ACH <Alias> $X,XXX.pdf
"""
import io
import re
import fitz  # pymupdf

HIGHLIGHT_GREEN = (0.02352909930050373, 0.5411829948425293, 0.1098020002245903)
HIGHLIGHT_OPACITY = 0.2044370025396347  # matches Jon's March originals (Multiply blend)

# Fields to highlight: (label text, which column). Left column labels sit at
# x ~= 48; right column labels at x ~= 460 on a letter page.
HIGHLIGHT_FIELDS = [
    ("DFI Account Number:", "left"),
    ("ID Number:", "left"),
    ("Settlement Date:", "left"),
    ("Amount:", "left"),
    ("Originator Name:", "right"),
]

# Originator Name -> short alias used in Cornerstones deposit filenames.
# Matching is case-insensitive "contains". First hit wins; extend freely.
ORIGINATOR_ALIASES = [
    ("FIDELITY", "Fidelity"),
    ("BBGF", "BBGF"),
    ("BLACKBAUD GIV", "BBGF"),
    ("BB MERCHAN", "BBMS"),
    ("BLACKBAUD MERCH", "BBMS"),
    ("FUNDRAISEUP", "FRU"),
    ("SCHWAB", "Schwab"),
    ("AMER ONLINE", "AOL"),
    ("AOGF", "AOL"),
    ("BIDKIT", "BidKit"),
    ("PLEDGELING", "Pledgeling"),
    ("AMAZON", "Amazon"),
    ("AMERICA'S CHARIT", "America's Charity"),
    ("AMERICAS CHARIT", "America's Charity"),
    ("CYBERGRANT", "Cyber Grant"),
    ("CYBER GRANT", "Cyber Grant"),
    ("NETWORK FOR GOOD", "NFG"),
    ("BENEVITY", "Benevity"),
    ("STRIPE", "Stripe"),
    ("PAYPAL", "PayPal"),
    ("6465", "BBMS"),  # BBMS batches sometimes carry originator "6465"
]

CS_ACCOUNTS = {"8529174544", "2789303"}  # Operations Checking + donation stream acct


def alias_for(originator):
    up = (originator or "").upper()
    for needle, alias in ORIGINATOR_ALIASES:
        if needle in up:
            return alias
    return (originator or "Unknown").title().strip()


def _col_boundary(page, clip=None):
    """
    x position where the right ('ORIGINATOR INFORMATION') column starts.
    Measured per page/block because the bank has shipped layout variants —
    March 2026 cut-outs put the right column near x=440, June raws at x=308.
    """
    for marker in ("ORIGINATOR INFORMATION", "Originator Name:"):
        hits = page.search_for(marker, clip=clip)
        if hits:
            return min(r.x0 for r in hits) - 6
    return page.rect.width * 0.55


def _row_value(page, clip, label, column="left", boundary=None):
    """
    Geometry-based field read: find `label` inside `clip`, return the words
    sitting on the same row to its right — bounded by the column edge so a
    left-column value doesn't swallow the right column's label. (RAD text
    extraction order is jumbled, so plain-text regex is unreliable — label
    and value can come out on different lines and in either order.)
    """
    hits = page.search_for(label, clip=clip)
    if not hits:
        return ""
    lab = sorted(hits, key=lambda r: (r.y0, r.x0))[0]
    if boundary is None:
        boundary = _col_boundary(page, clip)
    right_limit = boundary if column == "left" else page.rect.width
    words = page.get_text("words", clip=clip)
    row = [w for w in words
           if w[0] > lab.x1 - 1 and w[0] < right_limit
           and (w[1] + w[3]) / 2 > lab.y0 - 2 and (w[1] + w[3]) / 2 < lab.y1 + 2]
    row.sort(key=lambda w: w[0])
    return " ".join(w[4] for w in row).strip()


def parse_blocks(pdf_bytes):
    """
    Find every transaction block in a RAD report.
    Returns a list of dicts with page index, clip rect, and parsed fields.
    A block runs from its "RECEIVER INFORMATION" heading down to just above
    the next heading on the same page, or to the bottom of that page's content.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    blocks = []
    for p_idx, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        heads = sorted(page.search_for("RECEIVER INFORMATION"), key=lambda r: r.y0)
        if not heads:
            continue
        # bottom of usable content: just above "*** END OF REPORT ***" if present
        end_marks = page.search_for("*** END OF REPORT ***")
        content_bottom = min([r.y0 - 4 for r in end_marks], default=h - 36)
        words = page.get_text("words")
        for i, head in enumerate(heads):
            top = head.y0 - 6
            bottom = heads[i + 1].y0 - 10 if i + 1 < len(heads) else content_bottom
            # tighten bottom to last word inside the band
            band_words = [wd for wd in words if top <= wd[1] and wd[3] <= bottom + 2]
            if band_words:
                bottom = min(bottom, max(wd[3] for wd in band_words) + 8)
            clip = fitz.Rect(0, top, w, bottom)
            blk = {
                "page": p_idx,
                "clip": tuple(clip),
                "receiver": _row_value(page, clip, "Receiver Name:"),
                "dfi_account": _row_value(page, clip, "DFI Account Number:"),
                "receiving_dfi": _row_value(page, clip, "Receiving DFI ID:"),
                "id_number": _row_value(page, clip, "ID Number:"),
                "settlement_date": _row_value(page, clip, "Settlement Date:"),
                "transaction_type": _row_value(page, clip, "Transaction Type:"),
                "amount": _row_value(page, clip, "Amount:"),
                "originator": _row_value(page, clip, "Originator Name:", column="right"),
                "description": _row_value(page, clip, "Transaction Description:", column="right"),
                "entry_description": _row_value(page, clip, "Entry Description:"),
            }
            ttype = re.sub(r"\D", "", blk["transaction_type"])[:2]
            blk["is_credit"] = ("credit" in blk["description"].lower()
                                or ttype in {"22", "32", "23", "33"})
            blk["is_cs_account"] = blk["dfi_account"] in CS_ACCOUNTS
            blk["alias"] = alias_for(blk["originator"])
            blocks.append(blk)
    doc.close()
    return blocks


def settlement_to_ymd(settlement_date):
    """'March 02, 2026' -> ('26.03.02', '2026-03-02'); returns ('','') if unparseable."""
    months = {m: i + 1 for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"])}
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", settlement_date or "")
    if not m or m.group(1) not in months:
        return "", ""
    mo, day, yr = months[m.group(1)], int(m.group(2)), int(m.group(3))
    return f"{yr % 100:02d}.{mo:02d}.{day:02d}", f"{yr:04d}-{mo:02d}-{day:02d}"


def format_amount_cs(amount_str):
    """'$4,408.09'->'4,408.09' ; '$1,372.00'->'1,372' (cents only when nonzero)."""
    try:
        val = float((amount_str or "0").replace("$", "").replace(",", ""))
    except ValueError:
        return (amount_str or "").replace("$", "")
    if abs(val - round(val)) < 0.005:
        return f"{int(round(val)):,}"
    return f"{val:,.2f}"


def _value_rects(page, label, column):
    """Rects of the VALUE words to the right of `label` on the same row."""
    hits = page.search_for(label)
    if not hits:
        return []
    lab = sorted(hits, key=lambda r: (r.y0, r.x0))[0]
    boundary = _col_boundary(page)
    right_limit = page.rect.width if column == "right" else boundary
    words = page.get_text("words")
    row = [w for w in words
           if w[0] > lab.x1 - 1 and w[0] < right_limit
           and (w[1] + w[3]) / 2 > lab.y0 - 2 and (w[1] + w[3]) / 2 < lab.y1 + 2]
    if not row:
        return []
    r = fitz.Rect(min(w[0] for w in row), min(w[1] for w in row),
                  max(w[2] for w in row), max(w[3] for w in row))
    return [r]


def build_cutout(pdf_bytes, block, include_header=True, header_height=52):
    """
    Vector-clip the block onto its own page, then add the 5 green highlights.
    Returns PDF bytes of the single-page cut-out.
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = src[block["page"]]
    clip = fitz.Rect(*block["clip"])
    w = page.rect.width

    header_clip = None
    if include_header:
        # the "Cornerstones Inc" + report title band near the top of the page
        title_hits = page.search_for("ACH REMITTANCE ADVICE DETAIL REPORT")
        if title_hits:
            t = title_hits[0]
            name_hits = page.search_for("Cornerstones Inc")
            top = min([n.y0 for n in name_hits if n.y1 <= t.y0 + 2], default=t.y0 - 14)
            header_clip = fitz.Rect(0, top - 2, w, t.y1 + 4)

    hh = (header_clip.height + 6) if header_clip else 0
    out = fitz.open()
    new_page = out.new_page(width=w, height=hh + clip.height + 12)
    y = 0.0
    if header_clip:
        new_page.show_pdf_page(fitz.Rect(0, y, w, y + header_clip.height),
                               src, block["page"], clip=header_clip)
        y += header_clip.height + 6
    new_page.show_pdf_page(fitz.Rect(0, y, w, y + clip.height),
                           src, block["page"], clip=clip)

    for label, column in HIGHLIGHT_FIELDS:
        for r in _value_rects(new_page, label, column):
            annot = new_page.add_highlight_annot(r)
            annot.set_colors(stroke=HIGHLIGHT_GREEN)
            annot.set_opacity(HIGHLIGHT_OPACITY)
            annot.update(blend_mode="Multiply")

    buf = out.tobytes(deflate=True, garbage=3)
    out.close()
    src.close()
    return buf


def cutout_filename(block, nn):
    ymd, _ = settlement_to_ymd(block["settlement_date"])
    ymd = ymd or "00.00.00"
    return f"{ymd}.{nn:02d} Donation ACH {block['alias']} ${format_amount_cs(block['amount'])}.pdf"
