import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { buildXlsx, downloadXlsx, type Sheet } from "./xlsxWriter";

/**
 * The zip is "stored" (no compression) so worksheet XML appears verbatim in the
 * blob bytes — we decode the whole blob to UTF-8 and assert on substrings.
 */
async function blobText(blob: Blob): Promise<string> {
  const buf = await blob.arrayBuffer();
  return new TextDecoder().decode(new Uint8Array(buf));
}

describe("buildXlsx", () => {
  it("returns an OOXML-typed Blob with the expected zip parts", async () => {
    const sheets: Sheet[] = [{ name: "S1", rows: [["hello", 1]] }];
    const blob = buildXlsx(sheets);
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe(
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );
    const text = await blobText(blob);
    expect(text).toContain("[Content_Types].xml");
    expect(text).toContain("xl/workbook.xml");
    expect(text).toContain("xl/styles.xml");
    expect(text).toContain("xl/worksheets/sheet1.xml");
    expect(text).toContain("hello");
  });

  it("emits a styled header row, frozen pane, cols, and autofilter when columns given", async () => {
    const sheets: Sheet[] = [
      {
        name: "Tables",
        columns: [{ header: "Name", width: 20 }, { header: "Count" }],
        rows: [["a", 3]],
      },
    ];
    const text = await blobText(buildXlsx(sheets));
    expect(text).toContain("state=\"frozen\"");
    expect(text).toContain("<autoFilter");
    expect(text).toContain("customWidth=\"1\"");
    expect(text).toContain("Name");
    // default width fallback (16) for the column without an explicit width
    expect(text).toContain("width=\"16\"");
  });

  it("writes numbers as numeric cells and strings as inlineStr", async () => {
    const text = await blobText(buildXlsx([{ name: "S", rows: [[42, "txt"]] }]));
    expect(text).toContain("<v>42</v>");
    expect(text).toContain("t=\"inlineStr\"");
  });

  it("applies a style index for styled cells", async () => {
    const text = await blobText(
      buildXlsx([{ name: "S", rows: [[{ v: "T", s: "title" }]] }]),
    );
    // title maps to style index 2
    expect(text).toContain("s=\"2\"");
  });

  it("emits an empty styled cell for a blank styled value", async () => {
    const text = await blobText(
      buildXlsx([{ name: "S", rows: [[{ v: "", s: "cost" }]] }]),
    );
    // Blank + style → self-closing styled cell (index 5)
    expect(text).toContain("s=\"5\"/>");
  });

  it("skips NaN/Infinity numbers by treating them as inline strings", async () => {
    const text = await blobText(buildXlsx([{ name: "S", rows: [[NaN]] }]));
    expect(text).toContain("t=\"inlineStr\"");
    expect(text).toContain("NaN");
  });

  it("escapes XML special characters", async () => {
    const text = await blobText(buildXlsx([{ name: "S", rows: [["a & b < c > \"d\""]] }]));
    expect(text).toContain("a &amp; b &lt; c &gt; &quot;d&quot;");
  });

  it("strips illegal control characters", async () => {
    const text = await blobText(buildXlsx([{ name: "S", rows: [["x\x00\x07y"]] }]));
    expect(text).toContain(">xy<");
  });

  it("sanitizes and dedupes sheet names", async () => {
    const text = await blobText(
      buildXlsx([
        { name: "Weird:Name*[]?", rows: [["a"]] },
        { name: "Weird Name   ", rows: [["b"]] },
      ]),
    );
    // First cleaned to "Weird Name" (colon/star/brackets/? → space, trimmed)
    expect(text).toContain('name="Weird Name"');
    // Duplicate gets a _2 suffix
    expect(text).toMatch(/name="Weird Name_2"/);
  });

  it("falls back to SheetN when a name cleans to empty", async () => {
    const text = await blobText(buildXlsx([{ name: ":::", rows: [["a"]] }]));
    expect(text).toContain('name="Sheet1"');
  });

  it("handles many columns to exercise the colName base-26 rollover", async () => {
    const cols = Array.from({ length: 30 }, (_, i) => ({ header: `H${i}` }));
    const row = Array.from({ length: 30 }, (_, i) => i);
    const text = await blobText(buildXlsx([{ name: "Wide", columns: cols, rows: [row] }]));
    // Column 27 (0-based 26) is "AA"
    expect(text).toContain("AA1");
  });
});

describe("downloadXlsx", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (URL as any).createObjectURL = vi.fn(() => "blob:mock");
    (URL as any).revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("creates an anchor, clicks it, and revokes the URL later", () => {
    const clickSpy = vi.fn();
    const appendSpy = vi.spyOn(document.body, "appendChild");
    const removeSpy = vi.spyOn(document.body, "removeChild");
    const origCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = origCreate(tag) as HTMLElement;
      if (tag === "a") (el as HTMLAnchorElement).click = clickSpy;
      return el;
    });

    downloadXlsx([{ name: "S", rows: [["x"]] }], "report");

    expect(clickSpy).toHaveBeenCalled();
    expect(appendSpy).toHaveBeenCalled();
    expect(removeSpy).toHaveBeenCalled();
    // Revoke is deferred by a setTimeout
    vi.advanceTimersByTime(1000);
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("appends .xlsx when the filename lacks the extension", () => {
    let downloadName = "";
    const origCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = origCreate(tag) as HTMLElement;
      if (tag === "a") {
        (el as HTMLAnchorElement).click = vi.fn();
        Object.defineProperty(el, "download", {
          set(v: string) { downloadName = v; },
          get() { return downloadName; },
          configurable: true,
        });
      }
      return el;
    });

    downloadXlsx([{ name: "S", rows: [["x"]] }], "report");
    expect(downloadName).toBe("report.xlsx");

    downloadXlsx([{ name: "S", rows: [["x"]] }], "already.xlsx");
    expect(downloadName).toBe("already.xlsx");
  });
});
