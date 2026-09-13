/** UTF-8 与网络分块无关；只解析 SSE data，不执行服务端内容。 */
export async function* readEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const chunk = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const data = chunk
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (data) yield JSON.parse(data);
      }
      if (done) {
        if (buffer.trim() && !buffer.trim().startsWith(":"))
          throw new Error("事件流不完整，请刷新恢复结果。");
        return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
