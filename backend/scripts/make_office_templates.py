"""Write the blank .docx, .xlsx and .pptx files behind "+ New".

Made from scratch with python-docx, openpyxl and python-pptx when the image is built (and
by the tests), so no third-party document is copied into the project.

Usage: python scripts/make_office_templates.py <output folder>
"""

import sys
from pathlib import Path


def make(out: Path) -> None:
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Emu

    out.mkdir(parents=True, exist_ok=True)

    doc = Document()
    props = doc.core_properties
    props.author = props.last_modified_by = props.comments = props.title = ""
    doc.save(out / "blank.docx")

    wb = Workbook()
    wb.properties.creator = ""
    wb.save(out / "blank.xlsx")

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9
    prs.core_properties.author = prs.core_properties.title = ""
    prs.slides.add_slide(prs.slide_layouts[6])  # one empty slide
    prs.save(out / "blank.pptx")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: make_office_templates.py <output folder>")
    make(Path(sys.argv[1]))
