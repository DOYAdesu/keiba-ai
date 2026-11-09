"use strict";

const TABS = [
  { id: "top", label: "トップ" },
  { id: "yoso", label: "予想" },
  { id: "kekka", label: "結果" },
];

const main = document.getElementById("main");
let META = null;

/* ---------- 小物 ---------- */

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pct = (v, d = 0) => (v == null ? "" : (v * 100).toFixed(d) + "%");
const num = (v, d = 1) => (v == null ? "" : Number(v).toFixed(d));
const yen = (v) => (v == null ? "" : Number(v).toLocaleString());

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function weekday(date) {
  return "日月火水木金土"[new Date(date + "T00:00:00").getDay()];
}
const fmtDate = (d) => (d ? `${d.replace(/-/g, "/")}(${weekday(d)})` : "");

/** 着順 → バッジのクラス */
function finishBadge(f) {
  if (f == null) return "";
  const cls = f === 1 ? "f1" : f === 2 ? "f2" : f === 3 ? "f3" : "fx";
  return `<span class="badge ${cls}">${f}着</span>`;
}

/** 推奨の答え合わせ記号: 1着=◎ / 2〜3着=○ / 着外=× */
function hitMark(f) {
  if (f == null) return "";
  if (f === 1) return '<span class="hit o2">◎</span>';
  if (f <= 3) return '<span class="hit o1">○</span>';
  return '<span class="hit x">×</span>';
}

function raceLink(r, text) {
  return `<a href="#/race/${r.race_id}">${esc(text)}</a>`;
}

function dayNav(date, prev, next, route) {
  const back = prev ? `<a href="#/${route}/${prev}">‹ 前</a>`
                    : `<span class="off">‹ 前</span>`;
  const fwd = next ? `<a href="#/${route}/${next}">次 ›</a>`
                   : `<span class="off">次 ›</span>`;
  return `<div class="daynav">${back}<span class="cur">${fmtDate(date)}</span>${fwd}</div>`;
}

/** 学習期間のデータは成績が良く出るので、その旨を出す */
function sampleNote(inSample) {
  if (!inSample) return "";
  return `<p class="note"><span class="badge warn">学習期間</span>
    この日はモデルの学習に使われた期間です。的中率・回収率は実力より高めに出ます。
    ${META?.valid_from ? `検証期間は ${fmtDate(META.valid_from)} 以降。` : ""}</p>`;
}

/* ---------- 画面 ---------- */

function recommendTable(rows, { withResult }) {
  if (!rows.length) {
    return `<p class="note">この日に推奨条件を満たすレースはありませんでした。</p>`;
  }
  const head = `
    <tr>
      <th>区分</th><th class="mid">★</th><th class="nw">レース</th><th class="nw">推奨馬</th>
      <th class="num">勝率</th><th class="num">複勝率</th><th class="num">複勝期待値</th>
      <th class="num">単勝</th><th class="num">想定</th>
      ${withResult ? '<th class="mid">結果</th><th class="num">着順</th><th class="num">複勝払戻</th>' : ""}
      <th>根拠</th>
    </tr>`;
  const body = rows.map((r) => `
    <tr>
      <td><span class="badge rec">堅軸</span></td>
      <td class="mid">${r.is_star ? '<span class="badge star">★</span>' : ""}</td>
      <td class="nw">${raceLink(r, `${r.place} ${r.race_no}R`)}</td>
      <td class="nw">${r.top_horse_no} ${esc(r.top_horse_name)}</td>
      <td class="num">${pct(r.top_p_win, 1)}</td>
      <td class="num">${pct(r.top_p_top3)}</td>
      <td class="num">${num(r.top_ev, 2)}</td>
      <td class="num">${num(r.top_odds)}</td>
      <td class="num">${num(1 / r.top_p_win)}</td>
      ${withResult ? `<td class="mid">${hitMark(r.top_finish)}</td>
        <td class="num">${r.top_finish ?? ""}</td>
        <td class="num">${yen(r.top_fuku_pay)}</td>` : ""}
      <td>${esc(r.reason)}</td>
    </tr>`).join("");
  return `<div class="scroll"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

function venueCards(venues) {
  return `<div class="venues">${venues.map((v) => `
    <div class="venue">
      <h3>${esc(v.place)}</h3>
      ${v.races.map(raceCard).join("")}
    </div>`).join("")}</div>`;
}

function raceCard(r) {
  const meta = [
    r.post_time || "",
    `<span class="dist">${esc(r.surface || "")}${r.distance || ""}m</span>`,
    r.field_size ? `${r.field_size}頭` : "",
    r.is_rec ? '<span class="badge rec">堅軸</span>' : "",
    r.is_star ? '<span class="badge star">★</span>' : "",
  ].filter(Boolean).join(" ");
  return `
    <div class="rrow">
      <div class="rno">${r.race_no}<small>R</small></div>
      <div class="rbody">
        <div class="rhead">${raceLink(r, `${r.race_no}R`)} ${esc(r.race_name || "")}</div>
        <div class="rmeta">${meta}</div>
        <div class="rpick">
          <span class="name"><span class="mark m0">◎</span>
            ${r.top_horse_no} ${esc(r.top_horse_name)}</span>
          <span><span class="pct">${pct(r.top_p_win)}</span> ${finishBadge(r.top_finish)}</span>
        </div>
      </div>
    </div>`;
}

async function viewDay(date, { route, title }) {
  const d = await api(`/api/day/${date}`);
  const withResult = d.recommends.some((r) => r.top_finish != null);
  main.innerHTML = `
    ${title ? `<h1 class="page">${title}</h1>` : ""}
    ${sampleNote(d.in_sample)}
    ${route === "top" ? `
      <h2 class="section">推奨レース（${fmtDate(d.date)}）</h2>
      ${recommendTable(d.recommends, { withResult })}` : ""}
    <h2 class="section">レース一覧</h2>
    ${dayNav(d.date, d.prev, d.next, route)}
    ${venueCards(d.venues)}`;
}

async function viewResults(date) {
  const d = await api(`/api/results/${date}`);
  const s = d.summary;
  main.innerHTML = `
    <h1 class="page">結果</h1>
    ${sampleNote(d.in_sample)}
    ${dayNav(d.date, d.prev, d.next, "kekka")}
    <div class="scroll"><table>
      <thead><tr>
        <th>開催日</th><th class="num">レース数</th>
        <th class="num">本命 的中率</th><th class="num">本命 複勝率</th>
        <th class="num">本命 単勝回収</th>
        <th class="num">推奨 件数</th><th class="num">推奨 的中率</th>
        <th class="num">推奨 単勝回収</th><th class="num">推奨 複勝率</th>
        <th class="num">推奨 複勝回収</th>
      </tr></thead>
      <tbody><tr>
        <td>${fmtDate(d.date)}</td>
        <td class="num">${s.n_races}</td>
        <td class="num">${pct(s.honmei_win_rate)}</td>
        <td class="num">${pct(s.honmei_top3_rate)}</td>
        <td class="num">${pct(s.honmei_tansho_roi)}</td>
        <td class="num">${s.n_rec}</td>
        <td class="num">${pct(s.rec_win_rate)}</td>
        <td class="num">${pct(s.rec_tansho_roi)}</td>
        <td class="num">${pct(s.rec_top3_rate)}</td>
        <td class="num">${pct(s.rec_fukusho_roi)}</td>
      </tr></tbody>
    </table></div>
    <p class="note">本命＝AI勝率1位の馬。回収率は100円均等買いで、単勝は確定オッズ、
      複勝は実際の払戻データを使っています。</p>
    <h2 class="section">推奨の結果</h2>
    ${recommendTable(d.recommends, { withResult: true })}`;
}

const SUBTABS = [["shutuba", "出馬表"], ["ai", "AI予想"]];

async function viewRace(raceId, sub = "shutuba") {
  const d = await api(`/api/race/${raceId}`);
  const i = d.info;
  const links = d.same_day.map((r) =>
    r.race_id === raceId
      ? `<span class="cur">${r.race_no}R</span>`
      : `<a href="#/race/${r.race_id}">${r.race_no}R</a>`).join("");
  const others = d.other_venues.map((r) =>
    `<a href="#/race/${r.race_id}">${esc(r.place)} ${r.race_no}R ›</a>`).join(" ");

  main.innerHTML = `
    <div class="rlinks"><a href="#/yoso/${i.date}">${fmtDate(i.date)}</a>
      <span class="cur">${esc(i.place)}</span></div>
    <div class="rlinks">${links}</div>
    <div class="racehead">
      <div class="big">${i.race_no}<small>R</small></div>
      <div>
        <h1>${esc(i.place)} ${i.race_no}R ${esc(i.race_name || "")}</h1>
        <div class="rmeta">${i.post_time ? i.post_time + "発走 / " : ""}
          <span class="dist">${esc(i.surface || "")}${i.distance || ""}m</span>
          ${i.weather ? " / 天候:" + esc(i.weather) : ""}
          ${i.going ? " / 馬場:" + esc(i.going) : ""}</div>
        <div class="rmeta">${fmtDate(i.date)} ${esc(i.place)} ${i.field_size}頭
          ${i.top_finish != null ? '<span class="badge fx">結果あり</span>' : ""}</div>
      </div>
    </div>
    <div class="rlinks">${others}</div>
    ${sampleNote(i.in_sample)}
    <div class="subtabs">${SUBTABS.map(([id, label]) =>
      `<button data-sub="${id}" class="${id === sub ? "on" : ""}">${label}</button>`
    ).join("")}</div>
    <div id="subbody"></div>`;

  main.querySelectorAll("[data-sub]").forEach((b) =>
    b.addEventListener("click", () => {
      location.hash = `#/race/${raceId}/${b.dataset.sub}`;
    }));

  document.getElementById("subbody").innerHTML =
    sub === "ai" ? aiTable(d) : shutubaTable(d);
}

function shutubaTable(d) {
  const maxP = Math.max(...d.entries.map((e) => e.p_win || 0), 0.01);
  const rows = d.entries.map((e) => `
    <tr>
      <td class="num">${e.finish ?? ""}</td>
      <td class="num">${e.waku ?? ""}</td>
      <td class="num">${e.horse_no}</td>
      <td class="mid"><span class="mark m${Math.min(e.rank - 1, 3)}">${e.mark}</span></td>
      <td class="nw">${esc(e.horse_name)}
        ${e.sire ? `<div class="ped">父 ${esc(e.sire)}${
          e.dam_sire ? ` ／ 母父 ${esc(e.dam_sire)}` : ""}</div>` : ""}</td>
      <td class="mid">${esc(e.sex || "")}${e.age ?? ""}</td>
      <td class="num">${num(e.kinryo)}</td>
      <td class="nw">${esc(e.jockey_name || "")}
        ${e.trainer_name ? `<div class="ped">厩舎 ${esc(e.trainer_name)}</div>` : ""}</td>
      <td class="num">${num(e.odds)}</td>
      <td class="num">${e.popularity ?? ""}</td>
      <td class="num">${e.weight ?? ""}${e.weight_diff != null
          ? `<small>(${e.weight_diff > 0 ? "+" : ""}${e.weight_diff})</small>` : ""}</td>
      <td><div class="barcell"><span class="bar" style="width:70px">
            <i style="width:${((e.p_win || 0) / maxP) * 100}%"></i></span>
          <span>${pct(e.p_win, 1)}</span></div></td>
      <td class="num">${pct(e.p_top3)}</td>
      <td class="num">${num(e.time_sec, 1)}</td>
      <td class="num">${num(e.agari, 1)}</td>
    </tr>`).join("");
  return `<div class="scroll"><table>
    <thead><tr>
      <th class="num">着</th><th class="num">枠</th><th class="num">馬番</th>
      <th class="mid">印</th><th>馬名</th><th class="mid">性齢</th>
      <th class="num">斤量</th><th>騎手</th><th class="num">単勝</th>
      <th class="num">人気</th><th class="num">馬体重</th>
      <th>AI勝率</th><th class="num">複勝率</th>
      <th class="num">タイム</th><th class="num">上がり</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <p class="note">印＝AIの勝率順 ◎○▲△。AI勝率はレース内で合計100%になるよう正規化しています。</p>`;
}

function aiTable(d) {
  const sorted = [...d.entries].sort((a, b) => a.rank - b.rank);
  const rows = sorted.map((e) => `
    <tr>
      <td class="num">${e.rank}</td>
      <td class="mid"><span class="mark m${Math.min(e.rank - 1, 3)}">${e.mark}</span></td>
      <td class="num">${e.horse_no}</td>
      <td>${esc(e.horse_name)}</td>
      <td class="num">${pct(e.p_win, 1)}</td>
      <td class="num">${num(e.fair_odds)}</td>
      <td class="num">${num(e.odds)}</td>
      <td class="num">${pct(e.p_top3)}</td>
      <td class="num">${num(e.fuku_odds)}</td>
      <td class="num">${num(e.ev_fuku, 2)}</td>
      <td class="mid">${e.ev_fuku >= 1 ? '<span class="badge star">★</span>' : ""}</td>
      <td class="mid">${finishBadge(e.finish)}</td>
    </tr>`).join("");
  return `<div class="scroll"><table>
    <thead><tr>
      <th class="num">AI順</th><th class="mid">印</th><th class="num">馬番</th><th>馬名</th>
      <th class="num">勝率</th><th class="num">想定単勝</th><th class="num">実際の単勝</th>
      <th class="num">複勝率</th><th class="num">想定複勝</th><th class="num">複勝期待値</th>
      <th class="mid">妙味</th><th class="mid">着順</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <p class="note">想定単勝＝1÷AI勝率（控除率を引く前の「適正オッズ」）。
      実際の単勝がこれより大きければ妙味あり。
      複勝期待値は 複勝率×想定複勝オッズ で、1.00 が損益分岐です。</p>`;
}

/* ---------- ルーティング ---------- */

async function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [page, a, b] = hash.split("/");
  const tab = ["yoso", "kekka"].includes(page) ? page : page === "race" ? "" : "top";
  renderTabs(tab);
  main.innerHTML = `<div class="msg">読み込み中...</div>`;
  try {
    if (page === "race") return await viewRace(a, b || "shutuba");
    const date = a || META.latest;
    if (page === "kekka") return await viewResults(date);
    if (page === "yoso") return await viewDay(date, { route: "yoso", title: "予想" });
    return await viewDay(date, { route: "top" });
  } catch (e) {
    main.innerHTML = `<div class="msg">${esc(e.message)}</div>`;
  }
}

function renderTabs(active) {
  const d = META?.latest || "";
  document.getElementById("tabs").innerHTML = TABS.map((t) => {
    const href = t.id === "top" ? "#/" : `#/${t.id}/${d}`;
    return `<a href="${href}" class="${t.id === active ? "active" : ""}">${t.label}</a>`;
  }).join("");
}

function setupViewToggle() {
  const apply = (v) => {
    document.body.classList.remove("pc", "sp");
    if (v !== "auto") document.body.classList.add(v);
    document.querySelectorAll("[data-view]").forEach((b) =>
      b.classList.toggle("on", b.dataset.view === v));
    try { localStorage.setItem("view", v); } catch {}
  };
  let saved = "auto";
  try { saved = localStorage.getItem("view") || "auto"; } catch {}
  document.querySelectorAll("[data-view]").forEach((b) =>
    b.addEventListener("click", () => apply(b.dataset.view)));
  apply(saved);
}

async function start() {
  setupViewToggle();
  try {
    META = await api("/api/meta");
    const days = await api("/api/days?limit=1");
    META.latest = days[0]?.date;
  } catch (e) {
    main.innerHTML = `<div class="msg">${esc(e.message)}</div>`;
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}

start();
