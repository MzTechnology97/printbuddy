// Fork (printfarm): reads the plate's G-code off the page. It streams the response (decoded a chunk at a time,
// never the whole file as one string), reports progress, and hands the finished columns over without copying.
import { BYTES_PER_MOVE, GcodeParser, TooLargeError, buffers, type Toolpaths } from './parse';

export type WorkerRequest = { url: string; token: string | null; maxBytes: number };
export type WorkerReply =
  | { kind: 'progress'; read: number; total: number }
  | { kind: 'done'; toolpaths: Toolpaths }
  | { kind: 'error'; reason: 'http' | 'tooLarge' | 'memory' | 'other'; detail: string };

const post = (msg: WorkerReply, transfer: Transferable[] = []) => (self as unknown as Worker).postMessage(msg, transfer);

self.onmessage = async (e: MessageEvent<WorkerRequest>) => {
  const { url, token, maxBytes } = e.data;
  try {
    const r = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!r.ok || !r.body) {
      post({ kind: 'error', reason: 'http', detail: `HTTP ${r.status}` });
      return;
    }
    const total = Number(r.headers.get('content-length')) || 0;
    const parser = new GcodeParser(Math.floor(maxBytes / BYTES_PER_MOVE));
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let read = 0;
    let reported = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      read += value.byteLength;
      parser.push(decoder.decode(value, { stream: true }));
      if (read - reported > 4 << 20) {
        reported = read;
        post({ kind: 'progress', read, total });
      }
    }
    parser.push(decoder.decode());
    const toolpaths = parser.finish();
    post({ kind: 'done', toolpaths }, buffers(toolpaths));
  } catch (err) {
    if (err instanceof TooLargeError) post({ kind: 'error', reason: 'tooLarge', detail: String(err.moves) });
    else if (err instanceof RangeError) post({ kind: 'error', reason: 'memory', detail: err.message });
    else post({ kind: 'error', reason: 'other', detail: (err as Error)?.message ?? String(err) });
  }
};
