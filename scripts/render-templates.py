#!/usr/bin/env python3
"""Render the "New Document" templates Kulan OS puts in each person's Templates folder.

The OpenDocument files are minimal but valid (LibreOffice opens them as an
empty document). Output is byte-for-byte reproducible: fixed zip timestamps.

  python3 scripts/render-templates.py
"""
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "packages/kulan-desktop-settings/root/usr/share/kulan/templates"
STAMP = (2026, 1, 1, 0, 0, 0)
NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
      'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
      'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
      'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"')
HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'

BODIES = {
    "text": '<office:text><text:p/></office:text>',
    "spreadsheet": ('<office:spreadsheet><table:table table:name="Sheet1"><table:table-column/>'
                    '<table:table-row><table:table-cell/></table:table-row></table:table></office:spreadsheet>'),
    "presentation": ('<office:presentation><draw:page draw:name="Slide 1" draw:master-page-name="Default"/>'
                     '</office:presentation>'),
}
PRESENTATION_STYLES = ('<office:master-styles><style:master-page style:name="Default" '
                       'style:page-layout-name="PM1"/></office:master-styles>')
PRESENTATION_LAYOUT = ('<office:automatic-styles><style:page-layout style:name="PM1">'
                       '<style:page-layout-properties fo:page-width="28cm" fo:page-height="15.75cm" '
                       'style:print-orientation="landscape"/></style:page-layout></office:automatic-styles>')


def odf(path: Path, kind: str) -> None:
    mime = f"application/vnd.oasis.opendocument.{kind}"
    styles = (PRESENTATION_LAYOUT + PRESENTATION_STYLES) if kind == "presentation" else ""
    files = [
        ("mimetype", mime, zipfile.ZIP_STORED),
        ("content.xml", f'{HEAD}<office:document-content {NS} office:version="1.3">'
                        f'<office:body>{BODIES[kind]}</office:body></office:document-content>', zipfile.ZIP_DEFLATED),
        ("styles.xml", f'{HEAD}<office:document-styles {NS} office:version="1.3">{styles}'
                       '</office:document-styles>', zipfile.ZIP_DEFLATED),
        ("META-INF/manifest.xml", f'{HEAD}<manifest:manifest {NS} manifest:version="1.3">'
                                  f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
                                  f'manifest:media-type="{mime}"/>'
                                  '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                                  '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                                  '</manifest:manifest>', zipfile.ZIP_DEFLATED),
    ]
    with zipfile.ZipFile(path, "w") as z:
        for name, data, method in files:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = method
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "Text File.txt").write_text("")
    (OUT / "Markdown Note.md").write_text("# Title\n\n")
    odf(OUT / "Document.odt", "text")
    odf(OUT / "Spreadsheet.ods", "spreadsheet")
    odf(OUT / "Presentation.odp", "presentation")
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
