/**
 * Zero-dependency .xlsx writer with cell styling.
 *
 * Produces a real OOXML spreadsheet (multiple worksheets, styled header row,
 * frozen panes, auto-filter, column widths, colored status cells, number
 * formats) as a Blob, packaged in a minimal ZIP (stored, no compression).
 *
 * We avoid SheetJS on purpose: this app's npm registry is mirrored through a
 * CDN that can't serve package metadata, so any third-party package is fragile
 * in CI. Styling in OOXML is just more XML — no dependency required.
 *
 * Cells are written as inline strings or numbers (no shared-string table).
 */

export type CellValue = string | number | null | undefined;

/** Named styles registered in the stylesheet (see STYLE_INDEX below). */
export type StyleName =
  | "header"
  | "title"
  | "label"
  | "mono"
  | "cost"
  | "orphan"
  | "root"
  | "leaf"
  | "connected";

export interface StyledCell {
  v: CellValue;
  s?: StyleName;
}
export type Cell = CellValue | StyledCell;

export interface Column {
  header: string;
  width?: number; // Excel width units (~characters); defaults to 16 if omitted
}

export interface Sheet {
  name: string;
  /** When provided, a styled header row is emitted automatically with frozen
   *  panes + auto-filter, and `rows` are the data rows (no header). */
  columns?: Column[];
  rows: Cell[][];
}

// Map style name → index into <cellXfs>. Order must match buildStylesXml().
const STYLE_INDEX: Record<StyleName, number> = {
  header: 1,
  title: 2,
  label: 3,
  mono: 4,
  cost: 5,
  orphan: 6,
  root: 7,
  leaf: 8,
  connected: 9,
};

// --- CRC-32 (required by the ZIP local/central headers) ---
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const utf8 = (s: string) => new TextEncoder().encode(s);

function xmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

// Strip control chars that are illegal in XML 1.0 (allow tab \x09, LF \x0A, CR \x0D).
function sanitizeText(s: string): string {
  // eslint-disable-next-line no-control-regex
  return s.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");
}

function colName(idx: number): string {
  let n = idx;
  let name = "";
  do {
    name = String.fromCharCode(65 + (n % 26)) + name;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return name;
}

function normCell(c: Cell): StyledCell {
  return c !== null && typeof c === "object" && "v" in c ? c : { v: c as CellValue };
}

function cellXml(cell: StyledCell, ref: string): string {
  const { v } = cell;
  const s = cell.s ? ` s="${STYLE_INDEX[cell.s]}"` : "";
  if (v === null || v === undefined || v === "") {
    return cell.s ? `<c r="${ref}"${s}/>` : "";
  }
  if (typeof v === "number" && Number.isFinite(v)) {
    return `<c r="${ref}"${s}><v>${v}</v></c>`;
  }
  const text = sanitizeText(String(v));
  return `<c r="${ref}"${s} t="inlineStr"><is><t xml:space="preserve">${xmlEscape(text)}</t></is></c>`;
}

function sheetXml(sheet: Sheet): string {
  const hasHeader = !!(sheet.columns && sheet.columns.length);
  const headerRow: StyledCell[] | null = hasHeader
    ? sheet.columns!.map((c) => ({ v: c.header, s: "header" as StyleName }))
    : null;
  const dataRows: StyledCell[][] = sheet.rows.map((r) => r.map(normCell));
  const allRows: StyledCell[][] = headerRow ? [headerRow, ...dataRows] : dataRows;

  const nCols = Math.max(
    hasHeader ? sheet.columns!.length : 0,
    ...allRows.map((r) => r.length),
    1
  );

  const rowsXml = allRows
    .map((row, r) => {
      const cells = row.map((cell, c) => cellXml(cell, `${colName(c)}${r + 1}`)).join("");
      return `<row r="${r + 1}">${cells}</row>`;
    })
    .join("");

  // Frozen header + column widths + auto-filter, only when we own the header row.
  const sheetViews = hasHeader
    ? `<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>`
    : "";

  let cols = "";
  if (hasHeader) {
    cols =
      `<cols>` +
      sheet.columns!
        .map((c, i) => {
          const w = c.width ?? 16;
          return `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`;
        })
        .join("") +
      `</cols>`;
  }

  const autoFilter = hasHeader
    ? `<autoFilter ref="A1:${colName(nCols - 1)}${allRows.length}"/>`
    : "";

  return (
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
    sheetViews +
    cols +
    `<sheetData>${rowsXml}</sheetData>` +
    autoFilter +
    `</worksheet>`
  );
}

// Full stylesheet. Indices below MUST match STYLE_INDEX.
// fills[0]=none and fills[1]=gray125 are required by the spec / Excel.
function buildStylesXml(): string {
  return (
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
    `<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/></numFmts>` +
    `<fonts count="9">` +
    `<font><sz val="11"/><name val="Calibri"/></font>` + // 0 default
    `<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>` + // 1 header white bold
    `<font><b/><sz val="15"/><color rgb="FF4F46E5"/><name val="Calibri"/></font>` + // 2 title indigo
    `<font><b/><sz val="11"/><color rgb="FF334155"/><name val="Calibri"/></font>` + // 3 label
    `<font><sz val="10"/><name val="Consolas"/></font>` + // 4 mono
    `<font><sz val="11"/><name val="Calibri"/></font>` + // 5 cost (default look)
    `<font><b/><sz val="11"/><color rgb="FF92400E"/><name val="Calibri"/></font>` + // 6 orphan amber
    `<font><b/><sz val="11"/><color rgb="FF075985"/><name val="Calibri"/></font>` + // 7 root sky
    `<font><b/><sz val="11"/><color rgb="FF5B21B6"/><name val="Calibri"/></font>` + // 8 leaf violet
    `</fonts>` +
    `<fills count="7">` +
    `<fill><patternFill patternType="none"/></fill>` + // 0
    `<fill><patternFill patternType="gray125"/></fill>` + // 1
    `<fill><patternFill patternType="solid"><fgColor rgb="FF4F46E5"/></patternFill></fill>` + // 2 header indigo
    `<fill><patternFill patternType="solid"><fgColor rgb="FFFEF3C7"/></patternFill></fill>` + // 3 amber light
    `<fill><patternFill patternType="solid"><fgColor rgb="FFE0F2FE"/></patternFill></fill>` + // 4 sky light
    `<fill><patternFill patternType="solid"><fgColor rgb="FFEDE9FE"/></patternFill></fill>` + // 5 violet light
    `<fill><patternFill patternType="solid"><fgColor rgb="FFD1FAE5"/></patternFill></fill>` + // 6 emerald light
    `</fills>` +
    `<borders count="2">` +
    `<border><left/><right/><top/><bottom/><diagonal/></border>` + // 0 none
    `<border><left/><right/><top/><bottom style="thin"><color rgb="FFCBD5E1"/></bottom><diagonal/></border>` + // 1 bottom rule
    `</borders>` +
    `<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>` +
    `<cellXfs count="10">` +
    `<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>` + // 0 default
    `<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>` + // 1 header
    `<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>` + // 2 title
    `<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>` + // 3 label
    `<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>` + // 4 mono
    `<xf numFmtId="164" fontId="5" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>` + // 5 cost
    `<xf numFmtId="0" fontId="6" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>` + // 6 orphan
    `<xf numFmtId="0" fontId="7" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1"/>` + // 7 root
    `<xf numFmtId="0" fontId="8" fillId="5" borderId="0" xfId="0" applyFont="1" applyFill="1"/>` + // 8 leaf
    `<xf numFmtId="0" fontId="0" fillId="6" borderId="0" xfId="0" applyFill="1"/>` + // 9 connected
    `</cellXfs>` +
    `<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>` +
    `</styleSheet>`
  );
}

// A worksheet name: ≤31 chars, none of []:*?/\, not blank.
function safeName(name: string, index: number): string {
  const cleaned = name.replace(/[[\]:*?/\\]/g, " ").trim().slice(0, 31);
  return cleaned || `Sheet${index + 1}`;
}

interface ZipEntry {
  name: string;
  data: Uint8Array;
}

function buildZip(entries: ZipEntry[]): Blob {
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  const u16 = (n: number) => new Uint8Array([n & 0xff, (n >>> 8) & 0xff]);
  const u32 = (n: number) =>
    new Uint8Array([n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff]);
  const concat = (...parts: Uint8Array[]) => {
    const len = parts.reduce((a, p) => a + p.length, 0);
    const out = new Uint8Array(len);
    let o = 0;
    for (const p of parts) {
      out.set(p, o);
      o += p.length;
    }
    return out;
  };

  for (const entry of entries) {
    const nameBytes = utf8(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    const local = concat(
      u32(0x04034b50), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(size), u32(size),
      u16(nameBytes.length), u16(0), nameBytes, entry.data
    );
    chunks.push(local);

    const centralHeader = concat(
      u32(0x02014b50), u16(20), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(size), u32(size),
      u16(nameBytes.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset), nameBytes
    );
    central.push(centralHeader);
    offset += local.length;
  }

  const centralBytes = central.reduce((a, p) => a + p.length, 0);
  const eocd = concat(
    u32(0x06054b50), u16(0), u16(0),
    u16(entries.length), u16(entries.length),
    u32(centralBytes), u32(offset), u16(0)
  );

  const blobParts: BlobPart[] = [...chunks, ...central, eocd].map(
    (u) => u.buffer.slice(u.byteOffset, u.byteOffset + u.byteLength) as ArrayBuffer
  );
  return new Blob(blobParts, {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}

/** Build a multi-sheet, styled .xlsx workbook as a Blob. */
export function buildXlsx(sheets: Sheet[]): Blob {
  const used = new Set<string>();
  const named = sheets.map((s, i) => {
    const base = safeName(s.name, i);
    let dedup = base;
    let n = 2;
    while (used.has(dedup.toLowerCase())) dedup = `${base.slice(0, 28)}_${n++}`;
    used.add(dedup.toLowerCase());
    return { ...s, name: dedup };
  });

  const contentTypes =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
    `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
    `<Default Extension="xml" ContentType="application/xml"/>` +
    `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
    `<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>` +
    named
      .map(
        (_, i) =>
          `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`
      )
      .join("") +
    `</Types>`;

  const rootRels =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>` +
    `</Relationships>`;

  const workbook =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ` +
    `xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>` +
    named
      .map((s, i) => `<sheet name="${xmlEscape(s.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`)
      .join("") +
    `</sheets></workbook>`;

  // Worksheets are rId1..rIdN; styles is the next id.
  const stylesRid = named.length + 1;
  const workbookRels =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    named
      .map(
        (_, i) =>
          `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`
      )
      .join("") +
    `<Relationship Id="rId${stylesRid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>` +
    `</Relationships>`;

  const entries: ZipEntry[] = [
    { name: "[Content_Types].xml", data: utf8(contentTypes) },
    { name: "_rels/.rels", data: utf8(rootRels) },
    { name: "xl/workbook.xml", data: utf8(workbook) },
    { name: "xl/_rels/workbook.xml.rels", data: utf8(workbookRels) },
    { name: "xl/styles.xml", data: utf8(buildStylesXml()) },
    ...named.map((s, i) => ({
      name: `xl/worksheets/sheet${i + 1}.xml`,
      data: utf8(sheetXml(s)),
    })),
  ];

  return buildZip(entries);
}

/** Build the workbook and trigger a browser download. */
export function downloadXlsx(sheets: Sheet[], filename: string): void {
  const blob = buildXlsx(sheets);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
