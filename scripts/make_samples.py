"""Build the sample batch: three invented start-ups and the documents they 'sent'.

Every company, person, investor and figure in these files is invented for the demo.
Run:  python scripts/make_samples.py     (needs Microsoft Edge for the PDFs, and openpyxl for the spreadsheet)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "samples"
FOOT = "SAMPLE DOCUMENT. Every company, person, investor and figure in this file is invented for a software demo."

EDGE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

CSS = """
@page { size: 1280px 720px; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; color: #14181f; }
.slide { width: 1280px; height: 720px; padding: 56px 72px 40px; page-break-after: always; position: relative; background: #fff; }
.slide:last-child { page-break-after: auto; }
.kicker { font-size: 15px; letter-spacing: .08em; text-transform: uppercase; color: #5b6472; margin-bottom: 10px; }
h1 { font-size: 46px; line-height: 1.12; margin: 0 0 18px; }
h2 { font-size: 32px; margin: 0 0 22px; }
p, li { font-size: 21px; line-height: 1.45; }
ul { margin: 0; padding-left: 26px; }
li { margin-bottom: 9px; }
.cols { display: flex; gap: 48px; }
.cols > div { flex: 1; }
.big { font-size: 26px; }
.foot { position: absolute; left: 72px; right: 72px; bottom: 22px; font-size: 12px; color: #7a828e; border-top: 1px solid #d9dde3; padding-top: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 20px; }
td, th { text-align: left; padding: 9px 12px; border-bottom: 1px solid #d9dde3; }
th { color: #5b6472; font-weight: 600; }
"""


def slide(kicker: str, title: str, body: str, first: bool = False) -> str:
    head = f"<h1>{title}</h1>" if first else f"<h2>{title}</h2>"
    return f'<section class="slide"><div class="kicker">{kicker}</div>{head}{body}<div class="foot">{FOOT}</div></section>'


def html_doc(slides: list[str]) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(slides)}</body></html>'


def to_pdf(html: str, out: Path) -> None:
    edge = next((p for p in EDGE_PATHS if p.exists()), None)
    if edge is None:
        raise SystemExit("Microsoft Edge was not found, so the sample PDFs cannot be built.")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(html, encoding="utf-8")
        profile = Path(tmp) / "profile"
        subprocess.run([str(edge), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={profile}", f"--print-to-pdf={out}", src.as_uri()],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out.exists() or out.stat().st_size < 1000:
        raise SystemExit(f"PDF was not written: {out}")


def to_docx(paragraphs: list[tuple[str, str]], out: Path) -> None:
    """A minimal Word file. Each item is (style, text) with style 'h' (heading) or 'p'."""
    body = []
    for style, text in paragraphs:
        run_props = "<w:rPr><w:b/><w:sz w:val=\"30\"/></w:rPr>" if style == "h" else "<w:rPr><w:sz w:val=\"22\"/></w:rPr>"
        body.append(f'<w:p><w:pPr><w:spacing w:after="140"/></w:pPr><w:r>{run_props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(body) +
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1200" w:right="1200" w:bottom="1200" w:left="1200"/></w:sectPr>'
                '</w:body></w:document>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)


# ------------------------------------------------------------------ A: Halcyra Materials

def build_a(folder: Path) -> list[str]:
    deck = html_doc([
        slide("Seed round, October 2026", "Halcyra Materials",
              '<p class="big">Battery-grade cathode material from spent lithium-ion cells, without smelting.</p>'
              "<p>Halcyra Materials Pte. Ltd. is a private limited company registered in Singapore, incorporated in 2023. "
              "Our laboratory and pilot line are in Tuas, Singapore.</p>", first=True),
        slide("Technology", "Direct regeneration of cathode powder",
              '<div class="cols"><div><ul>'
              "<li>Our relithiation process restores spent NMC cathode powder to battery grade without breaking it down into metal salts.</li>"
              "<li>In pilot runs the process used 68% less energy per kilogram than the hydrometallurgical route.</li>"
              "<li>All research and development is done at our Singapore laboratory.</li>"
              "</ul></div><div><ul>"
              "<li>Intellectual property: two patent families (PCT applications filed in 2024 and 2025), owned by Halcyra Materials Pte. Ltd.</li>"
              "<li>Process know-how on particle healing is held as a trade secret.</li>"
              "<li>Hydrometallurgical recyclers sell metal salts; we return finished cathode powder, which keeps the crystal structure and most of the value.</li>"
              "</ul></div></div>"),
        slide("Traction and team", "A working pilot line and two paying pilots",
              '<div class="cols"><div><ul>'
              "<li>Pilot line of 50 tonnes a year commissioned in March 2026.</li>"
              "<li>Two paying pilot customers: a regional e-waste recycler and a battery pack assembler.</li>"
              "<li>Revenue was S$240,000 in 2025 and S$410,000 for January to September 2026.</li>"
              "</ul></div><div><ul>"
              "<li>14 full-time staff, 12 of them in Singapore.</li>"
              "<li>Dr Mei-Lin Tay, Chief Executive: PhD in materials science, eight years in battery R&amp;D at a cell maker.</li>"
              "<li>Arjun Velan, Chief Technology Officer: former process engineer at a speciality chemicals company.</li>"
              "<li>Sofia Brandt, Chief Operating Officer: ran operations at a recycling plant for six years.</li>"
              "</ul></div></div>"),
        slide("The round", "Raising S$6.5 million",
              '<div class="cols"><div><ul>'
              "<li>Seed round of S$6.5 million in priced equity, at a pre-money valuation of S$18 million.</li>"
              "<li>Lead investor: Vantrel Climate Partners, an independent venture fund, has committed S$3.0 million (term sheet signed 30 September 2026).</li>"
              "<li>This is the company's first institutional round.</li>"
              "<li>Raised to date: S$1.2 million from angel investors. No government equity has been invested.</li>"
              "</ul></div><div><ul>"
              "<li>Use of funds: a demonstration plant of 500 tonnes a year and growing the team to 26.</li>"
              "<li>Market: we estimate the global lithium-ion battery recycling market at US$38 billion by 2030.</li>"
              "<li>Main risks: securing feedstock contracts, and scaling the process from 50 to 500 tonnes a year.</li>"
              "</ul></div></div>"),
    ])
    to_pdf(deck, folder / "Halcyra_seed_deck.pdf")

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "KPIs"
    for row in [
        ("Halcyra Materials Pte. Ltd.: key figures, as at 30 September 2026", ""),
        ("Metric", "Value"),
        ("Full-time staff", 14),
        ("Staff based in Singapore", 12),
        ("Pilot line capacity (tonnes a year)", 50),
        ("Revenue, 2025 (S$)", 240000),
        ("Revenue, January to September 2026 (S$)", 410000),
        ("Monthly cash burn (S$)", 165000),
        ("Cash runway without the new round (months)", 7),
        (FOOT, ""),
    ]:
        ws.append(row)
    ws2 = wb.create_sheet("Customers")
    for row in [
        ("Customer", "Relationship", "Customer's yearly spend on cathode material processing (S$)",
         "Paid to Halcyra in the last 12 months (S$)", "Note"),
        ("Customer 1: regional e-waste recycler (name withheld)", "Paying pilot", 1200000, 180000, "Renewed in August 2026"),
        ("Customer 2: battery pack assembler (name withheld)", "Paying pilot", 2600000, 230000, "Started in April 2026"),
        (FOOT, "", "", "", ""),
    ]:
        ws2.append(row)
    wb.save(folder / "Halcyra_kpi_sheet.xlsx")

    (folder / "Halcyra_founder_call_notes.txt").write_text(
        "Notes from an introductory call with Halcyra Materials, 4 November 2026\n"
        "Present: Dr Mei-Lin Tay (Chief Executive), Arjun Velan (Chief Technology Officer)\n\n"
        "- All research and development is done in the Tuas laboratory in Singapore. The patents and know-how are assigned to the Singapore company, Halcyra Materials Pte. Ltd.\n"
        "- Two PCT patent applications are filed (2024 and 2025). Neither has been granted yet.\n"
        "- Vantrel Climate Partners has finished its due diligence and signed a term sheet for S$3.0 million. Halcyra is looking for a co-investor for the rest of the S$6.5 million round.\n"
        "- Both pilot customers pay for processed material. Customer 1 renewed in August 2026.\n"
        "- The biggest worry is feedstock: there is one supply contract and it runs to mid-2027.\n"
        "- Scaling from 50 tonnes to 500 tonnes a year has not been done before by this team.\n"
        "- They asked for introductions to battery cell makers in the region.\n\n"
        + FOOT + "\n", encoding="utf-8")
    return ["A/Halcyra_seed_deck.pdf", "A/Halcyra_kpi_sheet.xlsx", "A/Halcyra_founder_call_notes.txt"]


# ------------------------------------------------------------------ B: Orvelle Diagnostics

def build_b(folder: Path) -> list[str]:
    to_docx([
        ("h", "Orvelle Diagnostics Pte. Ltd.: investor brief, October 2026"),
        ("p", "Orvelle Diagnostics Pte. Ltd. is a private limited company registered in Singapore, incorporated in 2024."),
        ("h", "What we do"),
        ("p", "We are developing a rapid antimicrobial susceptibility test for bloodstream infections. It tells the clinician which antibiotic will work in about 90 minutes from a positive blood culture, where today's culture-based methods take two to three days."),
        ("p", "The test uses a microfluidic cartridge and single-cell imaging to watch bacteria respond to each antibiotic."),
        ("h", "Technology and intellectual property"),
        ("p", "The company owns one granted Singapore patent and two PCT patent applications on the cartridge design and the image-analysis method. All of them are assigned to Orvelle Diagnostics Pte. Ltd."),
        ("p", "All research and development is carried out at our laboratory in one-north, Singapore."),
        ("h", "Team"),
        ("p", "The company has 9 full-time staff, all based in Singapore."),
        ("p", "Dr Hannah Okafor-Lim, Chief Executive: infectious diseases physician and clinician-scientist with ten years in hospital practice."),
        ("p", "Dr Wei Jie Koh, Chief Technology Officer: PhD in microfluidics, formerly a research engineer at a diagnostics instrument maker."),
        ("p", "A regulatory affairs lead works with the company two days a week."),
        ("h", "Progress"),
        ("p", "Analytical validation on 310 archived bacterial isolates is complete. A clinical study of 200 patients with a Singapore hospital laboratory is agreed and is due to start in the first quarter of 2027."),
        ("p", "The product is at prototype stage. The company has no revenue and no paying customers yet."),
        ("p", "The regulatory plan is registration with Singapore's Health Sciences Authority as a Class C in-vitro diagnostic, followed by CE marking under the IVDR."),
        ("h", "Funding"),
        ("p", "Raised to date: S$900,000 in a pre-seed round from angel investors. No government equity has been invested."),
        ("p", "We are raising a seed round of S$4.0 million. This will be the company's first institutional round."),
        ("p", "We are in discussions with two venture funds. No term sheet has been signed and no investor has committed yet."),
        ("p", "Monthly cash burn is S$95,000 and the company has 8 months of runway."),
        ("h", "Market and competition"),
        ("p", "We estimate the global sepsis diagnostics market at US$1.1 billion by 2030."),
        ("p", "Hospital laboratories use culture-based susceptibility systems today. Several companies are developing rapid tests; ours works directly from positive blood culture with a single-use cartridge."),
        ("h", "Risks"),
        ("p", "The clinical study has not started, so clinical performance is unproven. Regulatory approval will take at least 18 months."),
        ("p", FOOT),
    ], folder / "Orvelle_investor_brief.docx")

    (folder / "Orvelle_validation_summary.md").write_text(
        "# Orvelle Diagnostics: analytical validation summary (September 2026)\n\n"
        "## What was tested\n\n"
        "The Orvelle rapid susceptibility test was run on 310 archived bacterial isolates from bloodstream infections, "
        "against the reference broth microdilution method.\n\n"
        "## Results\n\n"
        "- Categorical agreement with the reference method was 94.2% across 310 isolates.\n"
        "- The median time to result was 92 minutes.\n"
        "- Very major errors were 1.3%.\n\n"
        "## Limits of this work\n\n"
        "- These were archived isolates, not fresh patient samples. Clinical performance is not yet shown.\n"
        "- Six antibiotics were covered. The commercial panel will need at least twelve.\n\n"
        "## Next step\n\n"
        "A 200-patient clinical study with a Singapore hospital laboratory, due to start in the first quarter of 2027.\n\n"
        + FOOT + "\n", encoding="utf-8")
    return ["B/Orvelle_investor_brief.docx", "B/Orvelle_validation_summary.md"]


# ------------------------------------------------------------------ C: Brevanta AI

def build_c(folder: Path) -> list[str]:
    teaser = html_doc([
        slide("Series A, October 2026", "Brevanta AI",
              '<p class="big">A no-code builder of AI customer-service agents for e-commerce sellers in Southeast Asia.</p>'
              "<p>Brevanta AI Pte. Ltd. is a private limited company registered in Singapore, incorporated in 2019.</p>", first=True),
        slide("Product and traction", "420 paying customers",
              '<div class="cols"><div><ul>'
              "<li>Sellers set up a customer-service agent in an afternoon, with no code.</li>"
              "<li>The product is commercial and has 420 paying customers.</li>"
              "<li>Annual recurring revenue was S$1.4 million in September 2026.</li>"
              "<li>Net revenue retention is 104%.</li>"
              "</ul></div><div><ul>"
              "<li>The product is built on commercial large language model APIs from third-party providers. We route between them and do not train our own models.</li>"
              "<li>The company holds no patents.</li>"
              "<li>Product development is split between Singapore and Manila.</li>"
              "</ul></div></div>"),
        slide("Team and round", "Raising S$5.0 million",
              '<div class="cols"><div><ul>'
              "<li>31 full-time staff: 18 in Singapore, 13 in Manila and Ho Chi Minh City.</li>"
              "<li>Daniel Sarmiento, Chief Executive: previously founded and sold an e-commerce analytics company.</li>"
              "<li>Priya Nathan, Chief Product Officer: former product lead at a helpdesk software company.</li>"
              "</ul></div><div><ul>"
              "<li>Series A of S$5.0 million. Lead investor: Quenby Growth Partners, an independent venture fund, has committed S$2.0 million.</li>"
              "<li>Raised to date: S$2.3 million, in a seed round in 2022 led by a venture fund. This is not the company's first institutional round.</li>"
              "<li>Market: we estimate conversational AI for customer service at US$30 billion by 2030.</li>"
              "<li>Competitors: global helpdesk suites that have added AI agents. We are cheaper and built for marketplace sellers.</li>"
              "</ul></div></div>"),
    ])
    to_pdf(teaser, folder / "Brevanta_series_a_teaser.pdf")

    (folder / "Brevanta_customer_metrics.csv").write_text(
        "Brevanta AI Pte. Ltd.: largest customers, 12 months to 30 September 2026\n"
        "Customer,Relationship,Customer's yearly spend on customer-support software (S$),Paid to Brevanta in the last 12 months (S$)\n"
        "Online fashion retailer (name withheld),Paying,96000,31000\n"
        "Electronics marketplace seller (name withheld),Paying,54000,22000\n"
        "Home goods brand (name withheld),Paying,40000,9000\n"
        f"{FOOT}\n", encoding="utf-8")
    return ["C/Brevanta_series_a_teaser.pdf", "C/Brevanta_customer_metrics.csv"]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    files = {}
    for letter, build in (("A", build_a), ("B", build_b), ("C", build_c)):
        folder = OUT / letter
        folder.mkdir(parents=True)
        files[letter] = build(folder)
    batch = {
        "note": "Sample batch: the three companies, their people, investors, figures and documents are invented for this demo. The web research is real: the competitors, market reports and links it found are real.",
        "startups": [
            {"id": "A", "name": "Halcyra Materials", "website": "", "country": "Singapore",
             "one_liner": "Recycles spent lithium-ion battery cathode material back to battery grade by direct regeneration, without smelting.",
             "files": files["A"]},
            {"id": "B", "name": "Orvelle Diagnostics", "website": "", "country": "Singapore",
             "one_liner": "Rapid antimicrobial susceptibility test for bloodstream infections, with results in about 90 minutes.",
             "files": files["B"]},
            {"id": "C", "name": "Brevanta AI", "website": "", "country": "Singapore",
             "one_liner": "No-code builder of AI customer-service agents for e-commerce sellers in Southeast Asia.",
             "files": files["C"]},
        ],
    }
    (OUT / "batch.json").write_text(json.dumps(batch, indent=1), encoding="utf-8")
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            print(f"{path.relative_to(OUT)}  {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    sys.exit(main())
