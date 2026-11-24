#!/usr/bin/env python3
"""Make an A4 PDF of login cards to print and cut apart with scissors.

Reads the playbook's credentials file (one "login password" per line) and puts
10 cards on each A4 page (2 columns x 5 rows), separated by dashed cut lines
that run from edge to edge. Passwords are set in a monospaced font. Standard
library only (writes the PDF itself).

    sudo cat /root/choras_credentials.txt | python3 credentials_pdf.py - logins.pdf
    python3 credentials_pdf.py credentials.txt logins.pdf --users user01,user02
    python3 credentials_pdf.py --selftest
"""
import argparse
import sys

URL = "https://communityday.choras.src.surf-hosted.nl"
A4_W, A4_H = 595.28, 841.89
COLS, ROWS = 2, 5


def read_credentials(text, only=None):
    pairs = [line.split()[:2] for line in text.splitlines() if len(line.split()) >= 2]
    if only:
        pairs = [p for p in pairs if p[0] in only]
    return pairs


def esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text(x, y, font, size, s):
    return f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({esc(s)}) Tj ET"


def page_content(cards, url, title, note):
    w, h = A4_W / COLS, A4_H / ROWS
    ops = ["0.6 G 0.5 w [4 3] 0 d"]  # grey dashed cut lines across the whole page
    ops += [f"{w * c:.1f} 0 m {w * c:.1f} {A4_H:.1f} l S" for c in range(1, COLS)]
    ops += [f"0 {h * r:.1f} m {A4_W:.1f} {h * r:.1f} l S" for r in range(1, ROWS)]
    ops.append("[] 0 d 0 g")
    for i, (login, password) in enumerate(cards):
        x = (i % COLS) * w + 24
        top = A4_H - (i // COLS) * h - 34
        ops += [
            text(x, top, "F2", 13, title),
            text(x, top - 20, "F1", 9.5, url),
            text(x, top - 48, "F1", 10, "Login"),
            text(x + 70, top - 48, "F3", 16, login),
            text(x, top - 74, "F1", 10, "Password"),
            text(x + 70, top - 74, "F3", 16, password),
            text(x, top - 104, "F1", 8.5, note),
        ]
    return "\n".join(ops)


def build_pdf(pairs, url, title, note):
    per_page = COLS * ROWS
    pages = [pairs[i:i + per_page] for i in range(0, len(pairs), per_page)] or [[]]
    fonts = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Courier-Bold"}
    # Object numbers: 1 catalog, 2 pages, 3-5 fonts, then content/page pairs.
    font_refs = " ".join(f"/{k} {3 + i} 0 R" for i, k in enumerate(fonts))
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [%s] /Count %d >>" % (
            " ".join(f"{7 + 2 * i} 0 R" for i in range(len(pages))), len(pages)),
    ]
    objs += [f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>"
             for name in fonts.values()]
    for cards in pages:
        stream = page_content(cards, url, title, note).encode("latin-1")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {A4_W} {A4_H}] "
                    f"/Resources << /Font << {font_refs} >> >> /Contents {len(objs)} 0 R >>")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        body = obj if isinstance(obj, bytes) else obj.encode()
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("credentials", nargs="?", help='credentials file, or "-" for stdin')
    ap.add_argument("output", nargs="?", default="logins.pdf")
    ap.add_argument("--users", help="only these logins, comma-separated")
    ap.add_argument("--url", default=URL)
    ap.add_argument("--title", default="CHORAS Community Day")
    ap.add_argument("--note", default="One person per group logs in.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.credentials:
        ap.error("credentials file required")
    raw = sys.stdin.read() if args.credentials == "-" else open(args.credentials).read()
    pairs = read_credentials(raw, set(args.users.split(",")) if args.users else None)
    if not pairs:
        sys.exit("no logins found")
    with open(args.output, "wb") as f:
        f.write(build_pdf(pairs, args.url, args.title, args.note))
    pages = -(-len(pairs) // (COLS * ROWS))
    print(f"{args.output}: {len(pairs)} cards on {pages} A4 page(s)")
    return 0


def selftest():
    import re
    sample = "\n".join(f"user{i:02d} pw{i:02d}abc(x)" for i in range(1, 21)) + "\n\n"
    pairs = read_credentials(sample)
    assert len(pairs) == 20 and pairs[0] == ["user01", "pw01abc(x)"]
    assert read_credentials(sample, {"user02"}) == [["user02", "pw02abc(x)"]]
    pdf = build_pdf(pairs, URL, "CHORAS Community Day", "One person per group logs in.")
    assert pdf.count(b"/Type /Page ") == 2
    assert b"(pw20abc\\(x\\)) Tj" in pdf  # parentheses escaped
    # Every xref offset points at the start of its object.
    xref = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
    offsets = [int(o) for o in re.findall(rb"(\d{10}) 00000 n", pdf[xref:])]
    assert all(pdf[o:].startswith(f"{n} 0 obj".encode()) for n, o in enumerate(offsets, 1))
    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
