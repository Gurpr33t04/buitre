"""Shared test helpers."""


def make_pdf(*page_texts: str) -> bytes:
    """Build a small valid text PDF (correct xref table) without a library."""

    objects: list[bytes] = []

    n = len(page_texts)
    page_ids = [3 + 2 * i for i in range(n)]

    objects.append(b"<</Type/Catalog/Pages 2 0 R>>")
    kids = b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
    objects.append(b"<</Type/Pages/Kids[" + kids + b"]/Count "
                   + str(n).encode() + b">>")

    font_id = 3 + 2 * n

    for i, text in enumerate(page_texts):
        escaped = (
            text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")

        objects.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents "
            + str(4 + 2 * i).encode()
            + b" 0 R/Resources<</Font<</F1 " + str(font_id).encode()
            + b" 0 R>>>>>>"
        )
        objects.append(
            b"<</Length " + str(len(stream)).encode() + b">>stream\n"
            + stream + b"\nendstream"
        )

    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []

    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"

    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()

    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\n"
        f"startxref\n{xref}\n%%EOF"
    ).encode()

    return bytes(out)
