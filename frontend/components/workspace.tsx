"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import BirthForm from "./birth-form";
import ChartPanel from "./chart-panel";
import Messages from "./messages";
import { api } from "@/lib/api";
import { readEvents } from "@/lib/sse";
import type {
  Bootstrap,
  Conversation,
  ConversationSummary,
  StreamEvent,
} from "@/lib/types";
export default function Workspace() {
  const [csrf, setCsrf] = useState("");
  const [persistent, setPersistent] = useState(false);
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [ready, setReady] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generation = useRef(0);
  const posting = useRef(false);
  const remember = useCallback((c: Conversation) => {
    setHistory((items) =>
      items.some((v) => v.id === c.id)
        ? items.map((v) => (v.id === c.id ? c : v))
        : [c, ...items],
    );
  }, []);
  const load = useCallback(
    async (id: string, token: string, version: number) => {
      const c = await api<Conversation>(`/api/conversations/${id}`, token);
      if (generation.current !== version) return;
      setCurrent(c);
      remember(c);
      sessionStorage.setItem("conversation", id);
      return c;
    },
    [remember],
  );
  useEffect(() => {
    let disposed = false;
    api<Bootstrap>("/api/session", "")
      .then(async (data) => {
        if (disposed) return;
        setCsrf(data.csrf_token);
        setPersistent(data.storage === "database");
        setHistory(data.conversations);
        const id = sessionStorage.getItem("conversation");
        if (id && data.conversations.some((c) => c.id === id))
          await load(id, data.csrf_token, generation.current);
        if (!disposed) setReady(true);
      })
      .catch((e) => {
        if (!disposed) setError(e.message);
      });
    return () => {
      disposed = true;
    };
  }, [load]);
  useEffect(() => {
    if (current?.busy && !sending) {
      const id = current.id;
      const version = generation.current;
      timer.current = setTimeout(
        () => load(id, csrf, version).catch((e) => setError(e.message)),
        1500,
      );
    }
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [current, csrf, sending, load]);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const busy = sending || !!current?.busy;
  function reset() {
    generation.current++;
    setCurrent(null);
    setError("");
    setDraft("");
    sessionStorage.removeItem("conversation");
  }
  async function select(id: string) {
    generation.current++;
    setError("");
    try {
      await load(id, csrf, generation.current);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function remove(c: ConversationSummary) {
    if (!confirm("删除这段对话？删除后无法恢复。")) return;
    try {
      await api(`/api/conversations/${c.id}`, csrf, { method: "DELETE" });
      setHistory((items) => items.filter((v) => v.id !== c.id));
      if (current?.id === c.id) reset();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function send(message: string) {
    if (!ready || busy || posting.current || current?.failed) return;
    posting.current = true;
    setSending(true);
    setError("");
    let id = current?.id;
    let terminal = false;
    try {
      let c = current;
      if (!c) {
        c = await api<Conversation>("/api/conversations", csrf, {
          method: "POST",
        });
        id = c.id;
        remember(c);
        sessionStorage.setItem("conversation", c.id);
      }
      setCurrent({
        ...c,
        messages: [...c.messages, { role: "user", content: message }],
      });
      const response = await fetch(`/api/conversations/${id}/runs`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ message }),
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail || "发送失败");
      }
      if (!response.body)
        throw new Error("浏览器不支持流式响应，请刷新后重试。");
      for await (const raw of readEvents(response.body)) {
        const event = raw as StreamEvent;
        if (event.event === "final") {
          terminal = true;
          setDraft("");
        }
        if (event.event === "error") {
          terminal = true;
          setError(event.data.message || "本轮解读未完成");
        }
      }
      const latest = await load(id!, csrf, generation.current);
      if (!terminal && !latest?.busy)
        setError("连接已中断，已恢复服务端保存的对话。");
    } catch (e) {
      const message = (e as Error).message;
      if (id) {
        try {
          await load(id, csrf, generation.current);
        } catch {}
      }
      setError(message || "网络中断，请刷新查看结果。");
    } finally {
      posting.current = false;
      setSending(false);
    }
  }
  return (
    <>
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="命理知时首页">
          <span className="seal">时</span>
          <span>
            命理<span className="brand-dot"> · </span>知时
            <small>METAPHYS</small>
          </span>
        </a>
        <button className="new-chat" disabled={busy || !ready} onClick={reset}>
          ＋ <span>开启新的解读</span>
        </button>
        <div className="sidebar-heading">最近的对话</div>
        <nav id="history" aria-label="对话历史">
          {!history.length && <p className="muted">还没有对话</p>}
          {history.map((c) => (
            <div
              className={`history-row ${current?.id === c.id ? "selected" : ""}`}
              key={c.id}
            >
              <button
                className="history-open"
                title={c.title}
                disabled={busy}
                onClick={() => select(c.id)}
              >
                {c.title}
              </button>
              <button
                className="delete-chat"
                aria-label={`删除会话：${c.title}`}
                disabled={busy || c.busy}
                onClick={() => remove(c)}
              >
                ×
              </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="status-dot" /> {persistent ? "账号会话" : "本地会话"}
          <div>
            {persistent ? "记录已保存到账号" : "记录暂存于内存"}
            <br />
            {persistent ? "重新登录后可继续查看" : "服务重启后清空"}
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            探索自己 <span>/</span> <strong>命盘与解读</strong>
          </div>
          <span className="edition">
            本地预览版 <i /> 知时
          </span>
        </header>
        <main>
          <section className="conversation-pane">
            {!current?.messages.length && (
              <div className="intro">
                <div className="eyebrow">每一个时刻，都有它的坐标</div>
                <h1>
                  知来处，<em>看见自己。</em>
                </h1>
                <p>从一份准确的命盘开始，慢慢展开关于你的对话。</p>
              </div>
            )}
            <BirthForm
              disabled={busy || !ready || !!current?.failed}
              onSend={send}
              hasMessages={!!current?.messages.length}
            />
            <Messages messages={current?.messages || []} />
            {busy && (
              <div className="run-status" role="status">
                <span className="spinner" />
                正在排盘、解读并核对数据…
              </div>
            )}
            {(error || current?.failed) && (
              <div className="error-banner" role="alert">
                {error || "这段对话运行中断，请开启新的解读。"}
              </div>
            )}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                if (draft.trim()) send(draft.trim());
              }}
            >
              <label className="sr-only" htmlFor="message-input">
                继续提问
              </label>
              <textarea
                id="message-input"
                rows={2}
                maxLength={4000}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={busy || !ready || !!current?.failed}
                placeholder="也可以直接聊聊：我想了解自己的五行特点…"
                required
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    if (draft.trim()) send(draft.trim());
                  }
                }}
              />
              <div className="composer-bottom">
                <span>Enter 发送 · Shift + Enter 换行</span>
                <button
                  type="submit"
                  disabled={busy || !ready || !!current?.failed}
                  aria-label="发送消息"
                >
                  ↑
                </button>
              </div>
            </form>
            <p className="disclaimer">
              传统象数视角，仅供自我探索，不作为医疗、财务或其他重大决定的依据。
              <span className="storage-note">
                {persistent ? "对话保存在你的账号中，可在侧栏删除。" : "对话在本次服务运行期间保留，重启后清空。"}
              </span>
            </p>
          </section>
          <ChartPanel conversation={current} />
        </main>
      </div>
    </>
  );
}
