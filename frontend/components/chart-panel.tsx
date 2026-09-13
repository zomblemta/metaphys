"use client";
import { useState } from "react";
import type { Conversation, AstroChart } from "@/lib/types";
function Astro({ chart, id }: { chart: AstroChart; id: string }) {
  const [failed, setFailed] = useState(false);
  const url = `/api/conversations/${id}/chart.svg`;
  return (
    <section className="chart-block">
      <h3>西洋星盘</h3>
      <div className="chart-subtitle">
        {chart.profile.place} ·{" "}
        {(
          { P: "普拉西达斯", K: "科赫", A: "等宫", W: "整宫" } as Record<
            string,
            string
          >
        )[chart.house_system] || chart.house_system}{" "}
        · {chart.zodiac_type === "tropical" ? "回归黄道" : "恒星黄道"}
      </div>
      {failed ? (
        <p className="chart-warning">星盘图片暂不可用，落点数据仍可查看。</p>
      ) : (
        <img
          className="astro-image"
          src={url}
          alt="当前出生资料的本命星盘"
          onError={() => setFailed(true)}
        />
      )}
      <a className="chart-link" href={url} target="_blank" rel="noopener">
        打开完整星盘 ↗
      </a>
      <table className="points" aria-label="行星落点">
        <tbody>
          {chart.points.map((p) => (
            <tr key={p.key}>
              <td>{p.name}</td>
              <td>
                {p.sign} {p.position.toFixed(2)}°
              </td>
              <td>
                {p.house ? `第${p.house}宫` : "—"}
                {p.retrograde ? " 逆行" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {chart.warnings.map((w, i) => (
        <p key={i} className="chart-warning">
          {w}
        </p>
      ))}
    </section>
  );
}
export default function ChartPanel({
  conversation,
}: {
  conversation: Conversation | null;
}) {
  const result = conversation?.result;
  const charts = result?.charts || {};
  const b = charts.bazi;
  return (
    <aside className="chart-pane" aria-label="当前命盘">
      <div className="chart-heading">
        <h2>你的命盘</h2>
        <span>{Object.keys(charts).length ? "引擎已排盘" : "等待排盘"}</span>
      </div>
      <div id="chart-content">
        {!Object.keys(charts).length && (
          <>
            <div className="chart-empty">
              <div className="orbit" aria-hidden="true">
                <span>命</span>
                <i />
                <b />
              </div>
              <h3>先落下时间的坐标</h3>
              <p>
                填写出生资料后，四柱或星盘
                <br />
                会呈现在这里，供你对照解读。
              </p>
            </div>
            <div className="quiet-note">
              <span>有据可循</span>
              <p>
                排盘计算交给确定性引擎。
                <br />
                对话负责解读，数据始终可查。
              </p>
            </div>
          </>
        )}
        {result?.response_status === "withheld" && (
          <p className="verification">
            这次解读未通过核验，已撤回。下方命盘仍可核对。
          </p>
        )}
        {result?.response_status === "safety_redirect" && (
          <p className="verification">
            这个问题需要现实依据，不能从命盘作出判断。请查看对话中的说明。
          </p>
        )}
        {b && (
          <section className="chart-block">
            <h3>四柱八字</h3>
            <div className="chart-subtitle">
              {b.profile.name || "命主"} · {b.profile.place}
              <br />
              {b.clock_time.replace("T", " ")} · 公历
            </div>
            <div className="pillars">
              {(
                [
                  "year_pillar",
                  "month_pillar",
                  "day_pillar",
                  "time_pillar",
                ] as const
              ).map((key, i) => (
                <div key={key} className={`pillar ${i === 2 ? "day" : ""}`}>
                  <small>{["年柱", "月柱", "日柱", "时柱"][i]}</small>
                  <strong>{b[key]?.stem || "—"}</strong>
                  <strong>{b[key]?.branch || "—"}</strong>
                </div>
              ))}
            </div>
            <dl className="chart-facts">
              {[
                ["日主", b.day_master],
                [
                  "流派",
                  (
                    {
                      ziping: "子平",
                      mangpai: "盲派",
                      xinpai: "新派",
                    } as Record<string, string>
                  )[b.school] || b.school,
                ],
                [
                  "时间精度",
                  {
                    exact: "精确到分",
                    hour_known: "大致时辰",
                    unknown: "时辰未知",
                  }[b.profile.time_accuracy],
                ],
                ["旺衰", b.strength?.verdict || "—"],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            {b.warnings.map((w, i) => (
              <p className="chart-warning" key={i}>
                {w}
              </p>
            ))}
          </section>
        )}
        {charts.astro && conversation && (
          <Astro
            key={JSON.stringify(charts.astro.profile)}
            id={conversation.id}
            chart={charts.astro}
          />
        )}
        {!!Object.keys(charts).length && (
          <details className="data-details">
            <summary>查看完整命盘数据</summary>
            <pre className="data-json">{JSON.stringify(charts, null, 2)}</pre>
          </details>
        )}
      </div>
    </aside>
  );
}
