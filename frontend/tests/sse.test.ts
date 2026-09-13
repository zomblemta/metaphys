import { test } from "node:test";
import assert from "node:assert/strict";
import { readEvents } from "../lib/sse.ts";
function stream(bytes: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(c) {
      for (const b of bytes) c.enqueue(b);
      c.close();
    },
  });
}
test("split UTF-8, CRLF and keep-alive do not change events", async () => {
  const data = new TextEncoder().encode(
    ': keep-alive\r\n\r\nevent: final\r\ndata: {"event":"final","data":{"reply":"你好"}}\r\n\r\n',
  );
  const got = [];
  for await (const event of readEvents(
    stream([...data].map((byte) => Uint8Array.of(byte))),
  ))
    got.push(event);
  assert.deepEqual(got, [{ event: "final", data: { reply: "你好" } }]);
});
test("truncated data is reported", async () => {
  await assert.rejects(async () => {
    for await (const event of readEvents(
      stream([new TextEncoder().encode('data: {"event":')]),
    ))
      void event;
  }, /不完整/);
});
