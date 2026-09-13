"use client";
import { useState, type FormEvent } from "react";
export default function BirthForm({
  disabled,
  onSend,
  hasMessages,
}: {
  disabled: boolean;
  onSend: (message: string) => void;
  hasMessages: boolean;
}) {
  const [system, setSystem] = useState<"bazi" | "astro">("bazi");
  const [accuracy, setAccuracy] = useState("exact");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const precision = system === "astro" ? "exact" : accuracy;
    const name = String(data.get("name") || "").trim();
    const stamp =
      data.get("birth-date") +
      (precision === "unknown" ? "" : " " + data.get("birth-time"));
    onSend(
      `请排${system === "bazi" ? "八字命理" : "西洋星盘"}并解读。${name ? `称呼：${name}。` : ""}出生日期时间：${stamp}（公历、当地钟表时间），出生地：${data.get("place")}${system === "bazi" ? `，性别${data.get("gender")}` : ""}。${{ exact: "出生时间已确认精确到分钟", hour_known: "只知道大致时辰，不要视为精确时间", unknown: "出生时间未知，不要猜时辰" }[precision]}。`,
    );
  }
  return (
    <details
      className="birth-card"
      key={hasMessages ? "closed" : "open"}
      open={!hasMessages}
    >
      <summary>
        <span>
          <span className="step">01</span> 出生资料
        </span>
        <span className="summary-hint">填写或更新出生资料 ⌄</span>
      </summary>
      <form onSubmit={submit}>
        <div className="system-switch" role="group" aria-label="选择排盘体系">
          {(["bazi", "astro"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              disabled={disabled}
              className={`system ${system === kind ? "active" : ""}`}
              aria-pressed={system === kind}
              onClick={() => setSystem(kind)}
            >
              {kind === "bazi" ? "八字命理" : "西洋星盘"}{" "}
              <small>{kind === "bazi" ? "四柱 · 五行" : "星座 · 宫位"}</small>
            </button>
          ))}
        </div>
        <fieldset disabled={disabled} className="form-grid">
          <label>
            出生日期
            <input
              name="birth-date"
              type="date"
              required
              min="1900-01-01"
              max="2100-12-31"
            />
          </label>
          <label>
            出生时间
            <input
              name="birth-time"
              type="time"
              required={system === "astro" || accuracy !== "unknown"}
              disabled={system === "bazi" && accuracy === "unknown"}
            />
          </label>
          <label>
            出生地
            <input
              name="place"
              placeholder="如：北京市朝阳区"
              required
              maxLength={120}
              autoComplete="off"
            />
          </label>
          {system === "bazi" && (
            <label>
              性别
              <select name="gender">
                <option>男</option>
                <option>女</option>
              </select>
            </label>
          )}
          <label>
            时间精度
            <select
              value={system === "astro" ? "exact" : accuracy}
              disabled={system === "astro"}
              onChange={(e) => setAccuracy(e.target.value)}
            >
              <option value="exact">精确到分钟</option>
              <option value="hour_known">只知道大致时辰</option>
              <option value="unknown">不知道出生时间</option>
            </select>
          </label>
          <label>
            称呼 <span className="optional">选填</span>
            <input
              name="name"
              placeholder="怎么称呼你"
              maxLength={40}
              autoComplete="off"
            />
          </label>
        </fieldset>
        <p className="input-hint">
          {system === "astro"
            ? "星盘需要精确到分钟的时间；日期按公历，时间为当地钟表时间。"
            : "日期按公历填写，时间为出生地当时的钟表时间。"}
        </p>
        <div className="form-footer">
          <span>
            由排盘引擎计算
            <br />
            <small>解读中的数据与命盘核对</small>
          </span>
          <button type="submit" disabled={disabled} className="primary">
            排盘并解读 <span>↗</span>
          </button>
        </div>
      </form>
    </details>
  );
}
