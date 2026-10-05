"""Test data for the data file store."""

import io
import zipfile

CSV = "nimi;vanus\nMänd;12\n".encode()


def xlsx(members=None):
    """A zip shaped like a workbook (only its layout matters to the check)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (members or {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>",
                                       "xl/worksheets/sheet1.xml": b"<worksheet/>"}).items():
            z.writestr(name, data)
    return out.getvalue()


def upload(c, name, data, slug=None):
    form = {"file": (io.BytesIO(data), name)}
    if slug:
        form["slug"] = slug
    return c.post("/api/failid", data=form, content_type="multipart/form-data")
