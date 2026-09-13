import type { Message } from "@/lib/types";
export default function Messages({ messages }: { messages: Message[] }) {
  return (
    <section
      id="messages"
      className="messages"
      aria-label="对话内容"
      aria-live="polite"
    >
      {messages.map((message, index) => (
        <article className={`message ${message.role}`} key={index}>
          <div className="message-label">
            {
              { user: "你", assistant: "知时 · 解读", error: "运行提示" }[
                message.role
              ]
            }
          </div>
          <div className="message-content">
            {message.content.split("\n").map((line, i) => {
              const heading = line.match(/^#{1,4}\s+(.+)$/);
              const content = (heading ? heading[1] : line)
                .split(/(\*\*[^*]+\*\*)/g)
                .map((part, j) =>
                  part.startsWith("**") && part.endsWith("**") ? (
                    <strong key={j}>{part.slice(2, -2)}</strong>
                  ) : (
                    part
                  ),
                );
              return heading ? (
                <h3 key={i}>{content}</h3>
              ) : (
                <p key={i}>{line ? content : <br />}</p>
              );
            })}
          </div>
        </article>
      ))}
    </section>
  );
}
