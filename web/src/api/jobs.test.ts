import { afterEach, describe, expect, it, vi } from "vitest";
import { createSSEParser, startJob, streamJob } from "./jobs";

afterEach(() => {
  vi.restoreAllMocks();
});

// Realistic sse-starlette output: \r\n line endings, \r\n\r\n frame separators.
// This exact parser bug (only splitting on \n\n, trimming data) shipped because
// there was no fixture test.
const CRLF_FIXTURE =
  "event: stdout\r\ndata: compiling entity furnace\r\n\r\n" +
  "event: stdout\r\ndata:     indented detail line\r\n\r\n" +
  "event: stderr\r\ndata: warning: model slow\r\n\r\n" +
  "event: stdout\r\ndata: part one\r\ndata: part two\r\n\r\n" +
  'event: end\r\ndata: {"status": "completed", "returncode": 0}\r\n\r\n';

describe("createSSEParser", () => {
  it("parses \\r\\n framed events, preserving indentation and multi-line data", () => {
    const events: Array<[string, string]> = [];
    const parser = createSSEParser((ev, data) => events.push([ev, data]));
    parser.push(CRLF_FIXTURE);
    expect(events).toEqual([
      ["stdout", "compiling entity furnace"],
      // one leading space after "data:" is stripped per spec — the rest is NOT trimmed
      ["stdout", "    indented detail line"],
      ["stderr", "warning: model slow"],
      // multiple data: lines in one frame join with \n
      ["stdout", "part one\npart two"],
      ["end", '{"status": "completed", "returncode": 0}'],
    ]);
  });

  it("handles chunk boundaries that split lines and frame separators", () => {
    const events: Array<[string, string]> = [];
    const parser = createSSEParser((ev, data) => events.push([ev, data]));
    // Feed the fixture byte-dribbled in awkward 7-char chunks
    for (let i = 0; i < CRLF_FIXTURE.length; i += 7) {
      parser.push(CRLF_FIXTURE.slice(i, i + 7));
    }
    expect(events).toHaveLength(5);
    expect(events[0]).toEqual(["stdout", "compiling entity furnace"]);
    expect(events[4][0]).toBe("end");
  });

  it("also tolerates plain \\n line endings and \\n\\n separators", () => {
    const events: Array<[string, string]> = [];
    const parser = createSSEParser((ev, data) => events.push([ev, data]));
    parser.push('event: stdout\ndata: hello\n\nevent: end\ndata: {"status": "failed", "returncode": 2}\n\n');
    expect(events).toEqual([
      ["stdout", "hello"],
      ["end", '{"status": "failed", "returncode": 2}'],
    ]);
  });
});

describe("startJob", () => {
  it("POSTs {op, args} to /api/jobs/run and returns job_id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ job_id: "abc123" }), { status: 200 }));

    const id = await startJob("lint");
    expect(id).toBe("abc123");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/jobs/run");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ op: "lint", args: [] });
  });
});

function sseResponse(text: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      // emit in two chunks to exercise incremental parsing
      controller.enqueue(enc.encode(text.slice(0, 40)));
      controller.enqueue(enc.encode(text.slice(40)));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

describe("streamJob", () => {
  it("collects stdout/stderr lines (stderr prefixed) and maps completed/rc", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(CRLF_FIXTURE));
    const lines: string[] = [];
    const res = await streamJob("abc123", (l) => lines.push(l));
    expect(lines).toEqual([
      "compiling entity furnace",
      "    indented detail line",
      "[stderr] warning: model slow",
      "part one\npart two",
    ]);
    expect(res).toEqual({ state: "done", rc: 0 });
  });

  it('maps {"status":"failed"} to error with the returncode', async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse('event: end\r\ndata: {"status": "failed", "returncode": 3}\r\n\r\n'),
    );
    const res = await streamJob("abc123", () => {});
    expect(res).toEqual({ state: "error", rc: 3 });
  });

  it("treats a stream that closes without an end event as an error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse("event: stdout\r\ndata: hi\r\n\r\n"),
    );
    const res = await streamJob("abc123", () => {});
    expect(res).toEqual({ state: "error", rc: null });
  });
});
