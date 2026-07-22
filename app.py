import io
import zipfile

import streamlit as st
import fitz  # pymupdf

from engine import (
    parse_blocks, build_cutout, cutout_filename,
    settlement_to_ymd, format_amount_cs, alias_for,
)

st.set_page_config(page_title="Donation ACH Processor", layout="wide")

st.markdown("""
<style>
.step-badge {
    display: inline-block; background: #16a34a; color: white;
    border-radius: 20px; padding: 2px 12px; font-size: 12px;
    font-weight: 600; margin-bottom: 4px;
}
.info-box {
    background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px;
    padding: 10px 14px; font-size: 14px; color: #166534; margin-bottom: 12px;
}
.warn-box {
    background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px;
    padding: 10px 14px; font-size: 13px; color: #92400e; margin-bottom: 12px;
}
</style>
""", unsafe_allow_html=True)

st.title("🏦 Donation ACH Processor")
st.write(
    "Upload Atlantic Union RAD reports (one or a whole month). Each donation "
    "credit is cut out onto its own page, the five key fields are highlighted "
    "green, and the file is named in the Cornerstones deposit convention — "
    "ready to send to Development."
)

# ── Session state ─────────────────────────────────────────────────────────
ss = st.session_state
ss.setdefault("stage", "upload")
ss.setdefault("sources", {})   # filename -> pdf bytes
ss.setdefault("rows", [])      # parsed transactions (dicts)
ss.setdefault("results", [])   # (filename, bytes)


def reset():
    ss.stage = "upload"
    ss.sources = {}
    ss.rows = []
    ss.results = []


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — UPLOAD
# ══════════════════════════════════════════════════════════════════════════════
if ss.stage == "upload":
    st.markdown('<div class="step-badge">Step 1 — Upload RAD reports</div>',
                unsafe_allow_html=True)
    uploads = st.file_uploader(
        "Drop the daily 'ACH Remittance Advice Detail Report' PDFs here "
        "(you can select many at once)",
        type="pdf", accept_multiple_files=True,
    )
    if uploads and st.button("🔍 Detect transactions", type="primary"):
        ss.sources = {}
        ss.rows = []
        for up in sorted(uploads, key=lambda u: u.name):
            data = up.read()
            ss.sources[up.name] = data
            for blk in parse_blocks(data):
                ymd, iso = settlement_to_ymd(blk["settlement_date"])
                ss.rows.append({
                    **blk,
                    "source": up.name,
                    "ymd": ymd,
                    "iso": iso,
                    "include": bool(blk["is_credit"]),
                })
        # assign NN per settlement date, in report order
        counters = {}
        for row in sorted(ss.rows, key=lambda r: (r["iso"], r["source"], r["page"], r["clip"][1])):
            if row["include"]:
                counters[row["iso"]] = counters.get(row["iso"], 0) + 1
                row["nn"] = counters[row["iso"]]
            else:
                row["nn"] = 0
        ss.stage = "review"
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — REVIEW
# ══════════════════════════════════════════════════════════════════════════════
elif ss.stage == "review":
    st.markdown('<div class="step-badge">Step 2 — Review detected transactions</div>',
                unsafe_allow_html=True)
    n_credit = sum(1 for r in ss.rows if r["is_credit"])
    n_debit = len(ss.rows) - n_credit
    st.markdown(
        f'<div class="info-box">Found <b>{len(ss.rows)}</b> transactions across '
        f'<b>{len(ss.sources)}</b> report(s): <b>{n_credit}</b> credits '
        f'(pre-selected) and <b>{n_debit}</b> debits (excluded by default). '
        'Adjust the alias, the day counter (NN), or the include box, then generate.</div>',
        unsafe_allow_html=True,
    )

    header = st.columns([1, 2, 3, 2, 2, 1, 3])
    for col, label in zip(header, ["Include", "Date", "Originator", "Alias",
                                   "Amount", "NN", "Output filename"]):
        col.markdown(f"**{label}**")

    for i, row in enumerate(ss.rows):
        cols = st.columns([1, 2, 3, 2, 2, 1, 3])
        row["include"] = cols[0].checkbox(" ", value=row["include"],
                                          key=f"inc_{i}", label_visibility="collapsed")
        cols[1].write(row["settlement_date"] or "?")
        cols[2].write((row["originator"] or "?") +
                      ("" if row["is_credit"] else "  \n:red[debit]"))
        row["alias"] = cols[3].text_input(" ", value=row["alias"], key=f"alias_{i}",
                                          label_visibility="collapsed")
        cols[4].write(row["amount"])
        row["nn"] = int(cols[5].number_input(" ", min_value=0, max_value=99,
                                             value=int(row["nn"]), key=f"nn_{i}",
                                             label_visibility="collapsed"))
        if row["include"]:
            cols[6].code(cutout_filename(row, row["nn"] or 1), language=None)
        else:
            cols[6].write("—")

    st.markdown("---")
    include_header = st.checkbox("Include the report title header on each cut-out",
                                 value=True)
    dup_names = {}
    for r in ss.rows:
        if r["include"]:
            fn = cutout_filename(r, r["nn"] or 1)
            dup_names[fn] = dup_names.get(fn, 0) + 1
    dups = [fn for fn, c in dup_names.items() if c > 1]
    if dups:
        st.markdown('<div class="warn-box">⚠️ Duplicate output names (fix the NN '
                    'counters): ' + ", ".join(dups) + '</div>', unsafe_allow_html=True)

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("🔄 Start over"):
            reset()
            st.rerun()
    with col_b:
        if st.button("✅ Cut, highlight & name", type="primary", disabled=bool(dups)):
            with st.spinner("Processing…"):
                results = []
                for row in ss.rows:
                    if not row["include"]:
                        continue
                    data = build_cutout(ss.sources[row["source"]], row,
                                        include_header=include_header)
                    results.append((cutout_filename(row, row["nn"] or 1), data))
                ss.results = results
            ss.stage = "done"
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════
elif ss.stage == "done":
    st.markdown('<div class="step-badge">Step 3 — Development-ready files</div>',
                unsafe_allow_html=True)
    st.success(f"✅ {len(ss.results)} cut-out(s) built — highlighted and named.")

    for idx, (fn, data) in enumerate(ss.results):
        col_img, col_dl = st.columns([3, 2])
        with col_img:
            doc = fitz.open(stream=data, filetype="pdf")
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.2, 1.2))
            st.image(pix.tobytes("png"), caption=fn, use_container_width=True)
            doc.close()
        with col_dl:
            st.download_button(f"📄 Download", data=data, file_name=fn,
                               mime="application/pdf", key=f"dl_{idx}")

    st.markdown("---")
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn, data in ss.results:
            zf.writestr(fn, data)
    st.download_button("📥 Download all for Development (ZIP)",
                       data=zip_buf.getvalue(),
                       file_name="Donation_ACH_For_Development.zip",
                       mime="application/zip")

    if st.button("🔄 Process more reports"):
        reset()
        st.rerun()
