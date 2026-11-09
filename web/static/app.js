"use strict";

// データは web/export.py が書き出した静的 JSON。1開催日=1ファイルで、
// その日のトップ・予想・結果・レース詳細を全部まかなう。

const TABS = [
  { id: "top", label: "トップ" },
  { id: "yoso", label: "予想" },
  { id: "kekka", label: "結果" },
  { id: "manken", label: "100万馬券" },
  { id: "nouryoku", label: "能力指数" },
  { id: "ketto", label: "血統" },
  { id: "game", label: "ゲーム" },
];

const main = document.getElementById("main");
let INDEX = null;
const DAY_CACHE = new Map();

/* ---------- 小物 ---------- */

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pct = (v, d = 0) => (v == null ? "" : (v * 100).toFixed(d) + "%");
// 0.0% と出ると壊れて見えるので、小さい値は「<0.1%」にする
const pctLo = (v, d = 1) =>
  v != null && v > 0 && v < 0.5 / 10 ** (d + 2) ? "<" + pct(0.5 / 10 ** (d + 2), d) : pct(v, d);
const num = (v, d = 1) => (v == null ? "" : Number(v).toFixed(d));
const yen = (v) => (v == null ? "" : Number(v).toLocaleString());

async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} が読めませんでした (${res.status})`);
  return res.json();
}

async function loadIndex() {
  if (!INDEX) INDEX = await getJSON("data/index.json");
  return INDEX;
}

let MILLION;
async function loadMillion() {
  if (MILLION === undefined) {
    try { MILLION = await getJSON("data/million.json"); } catch { MILLION = null; }
  }
  return MILLION;
}

/** 当てはまる法則の数 → 過去 (確認期間) の100万馬券の割合 */
function millionRate(n) {
  const bc = MILLION?.by_count;
  if (!bc || !n) return null;
  return bc[String(Math.min(n, 3))]?.rate ?? null;
}

function millionBadge(r, long = false) {
  const n = r.million_n || 0;
  if (!n) return "";
  const rate = millionRate(n);
  return `<span class="badge mk" title="3連単100万円超えが出やすい法則に${n}件当てはまる">100万法則×${n}</span>${
    long && rate != null ? ` <small>過去の100万超 ${pct(rate, 1)}</small>` : ""}`;
}

function millionRulesOf(r) {
  if (!r.million_rules || !MILLION) return [];
  return String(r.million_rules).split(",").map((i) => MILLION.rules[Number(i)]).filter(Boolean);
}

const payCell = (v) => v == null ? "" : v >= 1e6 ? `<b class="mk">${yen(v)}円</b>` : `${yen(v)}円`;

async function loadDay(date) {
  if (!DAY_CACHE.has(date)) {
    DAY_CACHE.set(date, await getJSON(`data/day/${date}.json`));
  }
  return DAY_CACHE.get(date);
}

function weekday(date) {
  return "日月火水木金土"[new Date(date + "T00:00:00").getDay()];
}
const fmtDate = (d) => (d ? `${d.replace(/-/g, "/")}(${weekday(d)})` : "");

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

const raceHref = (date, r) => `#/race/${date}/${r.race_id}`;

/* ---------- 予想まわりの小物 ---------- */

const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** これから (今日以降) の開催日。古い順 */
const upcomingDays = () =>
  (INDEX?.days || []).filter((d) => d.date >= todayStr()).reverse();

/** 開いたときに見せる日: これからの開催があれば一番近い日、無ければ最新の日 */
const defaultDate = () => upcomingDays()[0]?.date || INDEX.days[0]?.date;

function gradeBadge(g) {
  if (!g) return "";
  const cls = g === "G1" ? "g1" : g === "G2" ? "g2" : g === "G3" ? "g3" : "gl";
  return `<span class="badge grade ${cls}">${esc(g)}</span>`;
}

function stageBadge(stage) {
  if (stage === "登録") return '<span class="badge warn">特別登録段階</span>';
  if (stage === "出馬表") return '<span class="badge star">出馬表確定</span>';
  return "";
}

/** レースの印つき上位馬 (◎○▲△△) */
const marksOf = (r, n = 5) =>
  [...r.entries].filter((e) => e.rank <= n).sort((a, b) => a.rank - b.rank);

const markSpan = (e) =>
  `<span class="mark m${Math.min((e.rank || 9) - 1, 3)}">${e.mark || ""}</span>`;

/** 今週末などの「これからの開催」への案内 */
function upcomingBanner(current) {
  const days = upcomingDays();
  if (!days.length) return "";
  const links = days.map((d) => `
    <a class="${d.date === current ? "cur" : ""}" href="#/yoso/${d.date}">
      <b>${fmtDate(d.date)}</b> ${d.n_races}R
      ${d.stage === "登録" ? '<small>特別登録</small>' : d.stage === "出馬表" ? '<small>出馬表確定</small>' : ""}
      ${d.graded?.length ? `<small class="gname">${esc(d.graded.join("・"))}</small>` : ""}
    </a>`).join("");
  return `<div class="upcoming"><span class="lbl">これからの開催</span>${links}</div>`;
}

/** 各レースの予想を1行ずつ (場ごと) */
function predictionList(day) {
  const venues = new Map();
  for (const r of day.races) {
    if (!venues.has(r.place)) venues.set(r.place, []);
    venues.get(r.place).push(r);
  }
  const done = day.races.some((r) => r.winner_no != null);
  return [...venues].map(([place, races]) => `
    <h3 class="venue-h">${esc(place)}</h3>
    <div class="scroll"><table class="plist">
      <thead><tr>
        <th class="num">R</th><th class="nw">発走</th><th>レース</th><th class="nw">コース</th>
        <th>◎</th><th>○</th><th>▲</th><th class="nw">△</th>
        <th class="num">◎勝率</th><th class="nw">荒れ度</th><th>推奨買い目</th>
        ${done ? '<th class="nw">結果</th>' : ""}
      </tr></thead>
      <tbody>${withPlaceholders(day, races.sort((a, b) => a.race_no - b.race_no)).map((r) => {
        if (r.placeholder) {
          return `<tr class="ph"><td class="num">${r.race_no}</td>
            <td colspan="${done ? 11 : 10}">出馬表の発表待ち
              （土曜分は木曜・日曜分は金曜に出て、自動で予想が入ります）</td></tr>`;
        }
        const m = marksOf(r);
        const cell = (e) => e ? `${e.horse_no} ${esc(e.horse_name)}${
          e.finish != null ? ` <small>(${e.finish}着)</small>` : ""}` : "";
        return `<tr class="click" data-href="${raceHref(day.date, r)}">
          <td class="num"><a href="${raceHref(day.date, r)}">${r.race_no}</a></td>
          <td class="nw">${esc(r.post_time || "")}</td>
          <td class="nw"><a href="${raceHref(day.date, r)}">${esc(r.race_name || "")}</a>
            ${gradeBadge(r.grade)} ${r.is_rec ? '<span class="badge rec">堅軸</span>' : ""}</td>
          <td class="nw"><span class="dist">${esc(r.surface || "")}${r.distance || ""}m</span>
            ${r.field_size}頭</td>
          <td class="nw">${markSpan(m[0] || {})} ${cell(m[0])}</td>
          <td class="nw">${cell(m[1])}</td>
          <td class="nw">${cell(m[2])}</td>
          <td class="nw">${[m[3], m[4]].filter(Boolean).map((e) => e.horse_no).join("・")}</td>
          <td class="num">${pct(r.top_p_win)}</td>
          <td class="nw">${r.upset_label ? esc(r.upset_label) : ""} ${millionBadge(r)}</td>
          <td class="betcell">${betsInline(r)}</td>
          ${done ? `<td class="nw">${r.winner_no != null
            ? `1着 ${r.winner_no} ${esc(r.winner_name || "")}` : ""}</td>` : ""}
        </tr>`;
      }).join("")}</tbody></table></div>`).join("");
}

function dayNav(day, route) {
  const back = day.prev ? `<a href="#/${route}/${day.prev}">‹ 前</a>`
                        : `<span class="off">‹ 前</span>`;
  const fwd = day.next ? `<a href="#/${route}/${day.next}">次 ›</a>`
                       : `<span class="off">次 ›</span>`;
  return `<div class="daynav">${back}
    <span class="cur">${fmtDate(day.date)}</span>${fwd}</div>`;
}

/** 学習期間のデータは成績が良く出るので、その旨を出す */
function sampleNote(inSample) {
  if (!inSample) return "";
  return `<p class="note"><span class="badge warn">学習期間</span>
    この日はモデルの学習に使われた期間です。的中率・回収率は実力より高めに出ます。
    ${INDEX?.valid_from ? `検証期間は ${fmtDate(INDEX.valid_from)} 以降。` : ""}</p>`;
}

/* ---------- 画面 ---------- */

function recommendTable(date, rows, { withResult }) {
  if (!rows.length) {
    return `<p class="note">この日に推奨条件を満たすレースはありませんでした。</p>`;
  }
  const head = `
    <tr>
      <th>区分</th><th class="mid">★</th><th class="nw">レース</th><th class="nw">推奨馬</th>
      <th class="num">勝率</th><th class="num">複勝率</th><th class="num">複勝期待値</th>
      <th class="num">指数</th><th class="num">単勝</th><th class="num">想定</th>
      ${withResult ? `<th class="mid">結果</th><th class="num">着順</th>
        <th class="num">複勝払戻</th><th class="nw">払戻の注記</th>` : ""}
      <th class="nw">推奨買い目</th><th>根拠</th>
    </tr>`;
  const body = rows.map((r) => `
    <tr>
      <td><span class="badge rec">堅軸</span></td>
      <td class="mid">${r.is_star ? '<span class="badge star">★</span>' : ""}</td>
      <td class="nw"><a href="${raceHref(date, r)}">${esc(r.place)} ${r.race_no}R</a></td>
      <td class="nw">${r.top_horse_no} ${esc(r.top_horse_name)}</td>
      <td class="num">${pct(r.top_p_win, 1)}</td>
      <td class="num">${pct(r.top_p_top3)}</td>
      <td class="num">${num(r.top_ev, 2)}</td>
      <td class="num">${r.top_index ?? ""}</td>
      <td class="num">${num(r.top_odds)}</td>
      <td class="num">${num(1 / r.top_p_win)}</td>
      ${withResult ? `<td class="mid">${hitMark(r.top_finish)}</td>
        <td class="num">${r.top_finish ?? ""}</td>
        <td class="num">${r.top_finish == null ? "" : yen(r.top_fuku_pay ?? 0)}</td>
        <td class="nw">${esc(r.pay_note || "")}</td>` : ""}
      <td class="betcell">${betsInline(r)}</td>
      <td class="reason">${esc(r.reason)}</td>
    </tr>`).join("");
  return `<div class="scroll"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

/** 荒れそうなレース: 三連単で高額配当が出る確率が高い順 */
function upsetTable(date, races) {
  const rows = races
    .filter((r) => r.p_hit_1m != null)
    .sort((a, b) => b.p_hit_1m - a.p_hit_1m)
    .slice(0, 5);
  if (!rows.length) return "";
  const withResult = rows.some((r) => r.trifecta_pay != null);
  const cls = { "大荒れ警戒": "f1", "荒れ気味": "f2", "標準": "fx", "堅い": "rec" };
  const body = rows.map((r) => `
    <tr>
      <td><span class="badge ${cls[r.upset_label] || "fx"}">${esc(r.upset_label)}</span></td>
      <td class="nw"><a href="${raceHref(date, r)}">${esc(r.place)} ${r.race_no}R</a></td>
      <td class="nw">${esc(r.race_name || "")}</td>
      <td class="num">${r.stage === "登録" ? "登録" : ""}${r.field_size}頭</td>
      <td class="num">${pctLo(r.p_hit_1m)}</td>
      <td class="num">${r.p_hit_100k == null ? "—" : pct(r.p_hit_100k, 0)}</td>
      <td class="num">${r.max_payout ? yen(Math.round(r.max_payout)) : ""}</td>
      <td class="num">${pct(r.top_p_win)}</td>
      <td class="betcell">${betsInline(r)}</td>
      ${withResult ? `<td class="num">${yen(r.trifecta_pay)}</td>` : ""}
    </tr>`).join("");
  return `
    <h2 class="section">荒れそうなレース（高額配当狙い）</h2>
    <div class="scroll"><table>
      <thead><tr>
        <th>荒れ度</th><th class="nw">レース</th><th>レース名</th><th class="num">頭数</th>
        <th class="num">100万超</th><th class="num">10万超</th>
        <th class="num">想定最高配当</th><th class="num">本命勝率</th><th>狙い方</th>
        ${withResult ? '<th class="num">実際の三連単</th>' : ""}
      </tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">三連単の配当は「払戻率 ÷ その組み合わせへの投票割合」で決まるので、
      単勝オッズから各組み合わせの配当を見積もり、「その組み合わせが来る確率」
      （AIの勝率と市場を半々で混ぜたもの）を掛け合わせています。
      <b>100万超</b>＝三連単が100万円以上になる確率。${upsetCalibNote()}
      当てに行く買い目ではなく、<b>どのレースが荒れやすいか</b>の目安です。</p>
    ${upsetConditions()}`;
}

/** 荒れ度の較正と検証の実績 (index.json の upset_report から作る) */
function upsetCalibNote() {
  const u = INDEX?.upset_report;
  if (!u || !u.n_pay) return "";
  const base = u.base_rate
    ? `全体の発生率は${pct(u.base_rate, 1)}（${Math.round(1 / u.base_rate)}レースに1回）です。`
    : "";
  const m = u.paths?.market;
  let valid = "";
  if (m?.quintiles?.length) {
    const top = m.quintiles[m.quintiles.length - 1];
    const bot = m.quintiles[0];
    valid = `検証期間${m.n_valid.toLocaleString()}レースでは、確率が最も高い5分の1
      （${top.n}レース）で実際に${pct(top.act, 1)}発生し（推定${pct(top.est, 1)}）、
      最も低い5分の1では${pct(bot.act, 1)}でした。`;
  }
  return `実配当${u.n_pay.toLocaleString()}レースで較正済みで、${base} ${valid}`;
}

/** 過去の100万超レースから分かった発生条件 (index.json の upset_report から作る) */
function upsetConditions() {
  const c = INDEX?.upset_report?.conditions;
  if (!c?.axes?.length) return "";
  const cell = (it) =>
    it.hits === 0 ? `0件 / ${it.n.toLocaleString()}R` : pctLo(it.rate);
  const body = c.axes.map((ax) => ax.items.map((it, i) => `
    <tr>
      ${i === 0 ? `<td rowspan="${ax.items.length}" class="nw"><b>${esc(ax.name)}</b></td>` : ""}
      <td class="nw">${esc(it.label)}</td>
      <td class="num">${cell(it)}</td>
      <td class="num">${it.n.toLocaleString()}R</td>
      <td class="num">${it.lift == null ? "" : it.lift.toFixed(2) + "倍"}</td>
    </tr>`).join("")).join("");
  const fs = c.axes.find((a) => a.name === "頭数");
  const worst = fs && fs.items[fs.items.length - 1];
  return `<details class="cond">
    <summary>どんなレースが荒れるのか（配当の分かる${c.n.toLocaleString()}レースの実績）</summary>
    <div class="scroll"><table>
      <thead><tr><th>条件</th><th></th><th class="num">100万超の発生率</th>
        <th class="num">レース数</th><th class="num">全体比</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <p class="note">全体の発生率${pct(c.base, 1)}に対する倍率です。<b>頭数が最も効きます</b>
      ${worst ? `— ${esc(worst.label)}は${worst.n.toLocaleString()}レースで${pct(worst.rate, 2)}しかありません。` : ""}
      次が距離で、短いほど荒れます。
      これらは上の確率にすでに織り込まれています（頭数が多く混戦なほど、
      組み合わせが散らばって高額配当になりやすいため）。</p>
  </details>`;
}

function venueCards(day) {
  const venues = new Map();
  for (const r of day.races) {
    if (!venues.has(r.place)) venues.set(r.place, []);
    venues.get(r.place).push(r);
  }
  return `<div class="venues">${[...venues].map(([place, races]) => `
    <div class="venue">
      <h3>${esc(place)}</h3>
      ${withPlaceholders(day, [...races].sort((a, b) => a.race_no - b.race_no))
        .map((r) => r.placeholder ? `<div class="rrow ph">
          <div class="rno">${r.race_no}<small>R</small></div>
          <div class="rbody"><div class="rhead">${r.race_no}R</div>
            <div class="rmeta">出馬表の発表待ち</div></div></div>`
          : raceCard(day.date, r)).join("")}
    </div>`).join("")}</div>`;
}

function raceCard(date, r) {
  const meta = [
    r.post_time || "",
    `<span class="dist">${esc(r.surface || "")}${r.distance || ""}m</span>`,
    r.field_size ? `${r.field_size}頭` : "",
    r.is_rec ? '<span class="badge rec">堅軸</span>' : "",
    r.is_star ? '<span class="badge star">★</span>' : "",
    r.upset_label === "大荒れ警戒" ? '<span class="badge f1">大荒れ警戒</span>'
      : r.upset_label === "荒れ気味" ? '<span class="badge f2">荒れ気味</span>' : "",
    millionBadge(r),
  ].filter(Boolean).join(" ");
  const m = marksOf(r, 3);
  const subs = m.slice(1).map((e) =>
    `${markSpan(e)}${e.horse_no} ${esc(e.horse_name)}`).join("　");
  return `
    <a class="rrow card" href="${raceHref(date, r)}">
      <div class="rno">${r.race_no}<small>R</small></div>
      <div class="rbody">
        <div class="rhead">${r.race_no}R ${esc(r.race_name || "")} ${gradeBadge(r.grade)}</div>
        <div class="rmeta">${meta}</div>
        <div class="rpick">
          <span class="name"><span class="mark m0">◎</span>
            ${r.top_horse_no} ${esc(r.top_horse_name)}</span>
          <span><span class="pct">${pct(r.top_p_win)}</span> ${finishBadge(r.top_finish)}</span>
        </div>
        ${subs ? `<div class="rsubs">${subs}</div>` : ""}
        ${r.winner_no != null && r.top_finish !== 1 ? `<div class="rresult">1着
          ${r.winner_no} ${esc(r.winner_name || "")}</div>` : ""}
      </div>
    </a>`;
}

async function viewDay(date, { route, title }) {
  const day = await loadDay(date);
  const recs = day.races.filter((r) => r.is_rec)
    .sort((a, b) => (b.is_star - a.is_star) || (b.confidence - a.confidence));
  const withResult = recs.some((r) => r.top_finish != null);
  // オッズが出るまでは市場モデルが使えず、推奨と荒れ度が出せない
  const noOdds = day.races.length && day.races.every((r) => r.top_odds == null);
  const stage = day.races.find((r) => r.stage)?.stage;
  const stageNote = stage === "登録" ? `<p class="note">
    <span class="badge warn">特別登録段階</span>
    出馬表が出る前なので、<b>特別レースの登録馬</b>（除外・回避で出走しない馬を含む）で予想しています。
    枠・馬番・騎手は未確定で、表の番号は登録順です。出馬表が出ると（土曜分は木曜、日曜分は金曜）
    全レースの予想に自動で切り替わります。</p>` : "";
  const oddsNote = noOdds && stage !== "登録" ? `<p class="note">
    <span class="badge warn">オッズ未発表</span>
    この日はまだ単勝オッズが出ていないため、馬の実力だけを見る<b>能力モデル</b>の
    予想です。オッズが出ると、市場を加味した予想に切り替わり、
    <b>推奨レース</b>と<b>荒れ度</b>も計算されます。</p>` : "";
  const showList = route === "yoso" || noOdds;
  main.innerHTML = `
    ${title ? `<h1 class="page">${title}</h1>` : ""}
    ${upcomingBanner(day.date)}
    ${sampleNote(day.in_sample)}
    ${stageNote}${oddsNote}
    <h2 class="section">この日の注目（${fmtDate(day.date)}）</h2>
    ${highlights(day)}
    ${millionDayBox(day)}
    ${route === "top" ? `
      <h2 class="section">推奨レース（${fmtDate(day.date)}）</h2>
      ${recommendTable(day.date, recs, { withResult })}
      ${upsetTable(day.date, day.races)}` : ""}
    ${showList ? `
      <h2 class="section">各レースの予想（${fmtDate(day.date)}）</h2>
      ${dayNav(day, route)}
      ${predictionList(day)}
      <p class="note">行を押すとそのレースの詳しい予想（印・勝率・買い目・根拠）が見られます。</p>` : ""}
    <h2 class="section">レース一覧</h2>
    ${showList ? "" : dayNav(day, route)}
    ${venueCards(day)}`;
  main.querySelectorAll("tr.click").forEach((tr) =>
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;
      location.hash = tr.dataset.href;
    }));
}

async function viewResults(date) {
  const day = await loadDay(date);
  const s = day.summary;
  const recs = day.races.filter((r) => r.is_rec && r.top_finish != null);
  main.innerHTML = `
    <h1 class="page">結果</h1>
    ${sampleNote(day.in_sample)}
    ${dayNav(day, "kekka")}
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
        <td>${fmtDate(day.date)}</td>
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
    ${recommendTable(day.date, recs, { withResult: true })}`;
}

const SUBTABS = [["shutuba", "出馬表"], ["ai", "AI予想"], ["index", "能力指数"]];

/* ---------- 能力指数の内訳 ---------- */

const COMPS = [
  ["c_spd", "走破"], ["c_agari", "上がり"], ["c_recent", "近走"], ["c_career", "着順"],
  ["c_dist", "距離"], ["c_course", "コース"], ["c_jockey", "騎手"], ["c_trainer", "厩舎"],
  ["c_sire", "血統"], ["c_going", "馬場"],
];

/** レース内で強いほど濃い緑 */
function shade(v, lo, hi) {
  if (v == null || hi <= lo) return "";
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const a = 0.05 + 0.55 * t ** 1.4;
  return `background: rgba(46, 160, 110, ${a.toFixed(3)});${t >= 0.85 ? "font-weight:700;" : ""}`;
}

function indexTable(race, mode = "ai") {
  const es = [...race.entries];
  const val = (e, k) => (e[k] ?? 50);
  if (mode === "no") es.sort((a, b) => a.horse_no - b.horse_no);
  else if (mode === "odds") es.sort((a, b) => (a.odds ?? 1e9) - (b.odds ?? 1e9));
  else es.sort((a, b) => (b.ability_idx ?? -1) - (a.ability_idx ?? -1) || a.rank - b.rank);
  const byIdx = [...race.entries].sort((a, b) => (b.ability_idx ?? -1) - (a.ability_idx ?? -1));
  const idxRank = new Map(byIdx.map((e, i) => [e.horse_no, i + 1]));
  const range = (k) => {
    const vs = race.entries.map((e) => val(e, k));
    return [Math.min(...vs), Math.max(...vs)];
  };
  const ranges = Object.fromEntries([["ability_idx"], ...COMPS].map(([k]) => [k, range(k)]));
  const done = race.entries.some((e) => e.finish != null);
  const regNo = race.stage === "登録";
  const rows = es.map((e) => `
    <tr>
      <td class="num">${idxRank.get(e.horse_no)}</td>
      <td class="num">${regNo ? "" : e.horse_no}</td>
      <td class="nw">${markSpan(e)} ${esc(e.horse_name)}</td>
      <td class="num idx" style="${shade(e.ability_idx, ...ranges.ability_idx)}">${e.ability_idx ?? ""}</td>
      ${COMPS.map(([k]) => `<td class="num ${e[k] == null ? "na" : ""}"
          style="${shade(val(e, k), ...ranges[k])}">${val(e, k)}</td>`).join("")}
      ${done ? `<td class="num">${e.finish ?? ""}</td>` : ""}
    </tr>`).join("");
  return `
    <p class="note idxnote">各馬の能力を0〜100で数値化し、内訳を併記しています（50＝全馬の真ん中、
      データの無い項目は50）。<b>緑が濃いほど、このレースの中でその項目が強い馬</b>です。${INDEX_DEF}</p>
    ${indexReportBox()}
    <div class="scroll"><table class="idxtable">
      <thead><tr><th class="num">指数順</th><th class="num">${regNo ? "" : "馬番"}</th><th>馬名</th>
        <th class="num">指数</th>${COMPS.map(([, l]) => `<th class="num">${l}</th>`).join("")}
        ${done ? '<th class="num">着順</th>' : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

const LEFT_TURN = new Set(["東京", "新潟", "中京"]);

const INDEX_DEF = `<b>指数</b>＝AI（オッズを使わない能力モデル）7割＋下の10項目の合成3割。
  内訳はどれもそのレースより前の成績から作り、過去レースで候補の式を比べて当たりやすいものを選んでいます：
  <b>走破</b>＝走破タイム（競馬場・芝ダ・距離・馬場ごとに標準化）を近走ほど重く平均／
  <b>上がり</b>＝上がり3Fのレース内順位を近走ほど重く平均／
  <b>近走</b>＝着順÷頭数を近走ほど重く平均／<b>着順</b>＝通算の着順÷頭数の平均／
  <b>距離・コース・馬場</b>＝同じ距離帯（400m刻み）・競馬場・馬場（良／道悪）での複勝率／
  <b>騎手・厩舎</b>＝騎手・調教師の複勝率／<b>血統</b>＝父の産駒の同じ芝ダでの複勝率。
  複勝率は出走数が少ないと極端になるので全体の平均に寄せています。`;

/** 指数の答え合わせ (検証期間の実績) */
function indexReportBox() {
  const r = INDEX?.index_report;
  if (!r) return "";
  const bands = r.bands.map((b) => `<tr><td class="nw">${b.band}</td><td class="num">${yen(b.n)}頭</td>
    <td class="num"><b>${pct(b.top3)}</b></td><td class="num">${pct(b.win)}</td></tr>`).join("");
  const aucs = Object.entries(r.auc).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td class="nw">${k}</td><td class="num">${v.toFixed(3)}</td>
      <td><div class="bar" style="width:120px"><i style="width:${Math.max(0, (v - 0.5) / 0.3) * 100}%"></i></div></td></tr>`).join("");
  return `<details class="cond">
    <summary>指数の答え合わせ（${r.from ? fmtDate(r.from) + "以降の" : ""}${yen(r.n_races)}レース・AIの学習に使っていない期間）</summary>
    <div class="hl">
      <div><h3 class="sub">指数帯ごとの実際の成績</h3>
        <div class="scroll"><table><thead><tr><th>指数</th><th class="num">頭数</th>
          <th class="num">複勝率</th><th class="num">勝率</th></tr></thead><tbody>${bands}</tbody></table></div>
        <p class="note">レース内で指数1位の馬：複勝率 ${pct(r.top1_top3)}・勝率 ${pct(r.top1_win)}</p></div>
      <div><h3 class="sub">各項目の当たりやすさ（レース内AUC）</h3>
        <div class="scroll"><table><thead><tr><th>項目</th><th class="num">AUC</th><th></th></tr></thead>
          <tbody>${aucs}</tbody></table></div>
        <p class="note">AUC＝3着以内に来た馬が、来なかった馬より高い点だった割合。0.5はでたらめと同じ、1に近いほど良い。</p></div>
    </div></details>`;
}

/* ---------- 買い目 (勝率から Harville 近似で組み合わせの確率を出す) ---------- */

const pad2 = (n) => String(n).padStart(2, "0");
const comboKey = (...nos) => [...nos].sort((a, b) => a - b).map(pad2).join("");

function harville(race) {
  const es = race.entries.filter((e) => e.p_win != null);
  const tot = es.reduce((s, e) => s + e.p_win, 0) || 1;
  const p = new Map(es.map((e) => [e.horse_no, e.p_win / tot]));
  const q = (i, j) => p.get(i) * p.get(j) / (1 - p.get(i)) + p.get(j) * p.get(i) / (1 - p.get(j));
  const trio = (i, j, k) => {
    let s = 0;
    for (const [a, b, c] of [[i, j, k], [i, k, j], [j, i, k], [j, k, i], [k, i, j], [k, j, i]]) {
      const d1 = 1 - p.get(a), d2 = 1 - p.get(a) - p.get(b);
      if (d1 > 1e-9 && d2 > 1e-9) s += p.get(a) * (p.get(b) / d1) * (p.get(c) / d2);
    }
    return s;
  };
  const wide = (i, j) => [...p.keys()].filter((k) => k !== i && k !== j)
    .reduce((s, k) => s + trio(i, j, k), 0);
  return { p, q, trio, wide };
}

function suggestBets(race) {
  const m = marksOf(race);
  if (m.length < 3) return [];
  const H = harville(race);
  const [a, b, c] = m.map((e) => e.horse_no);
  const partners = m.slice(1).map((e) => e.horse_no);
  const trioCombos = [];
  for (let i = 0; i < partners.length; i++)
    for (let j = i + 1; j < partners.length; j++) trioCombos.push([a, partners[i], partners[j]]);
  return [
    { type: "単勝", label: `◎ ${a}`, combos: [comboKey(a)], prob: H.p.get(a) },
    { type: "複勝", label: `◎ ${a}`, combos: [comboKey(a)], prob: m[0].p_top3, good: true },
    { type: "馬連", label: `◎-○ ${a}-${b}`, combos: [comboKey(a, b)], prob: H.q(a, b), good: true },
    { type: "ワイド", label: `◎-○ ${a}-${b}`, combos: [comboKey(a, b)], prob: H.wide(a, b) },
    { type: "ワイド", label: `◎-▲ ${a}-${c}`, combos: [comboKey(a, c)], prob: H.wide(a, c) },
    { type: "三連複", label: `◎軸 相手 ${partners.join(",")}`,
      combos: trioCombos.map((t) => comboKey(...t)),
      prob: trioCombos.reduce((s, t) => s + H.trio(...t), 0) },
  ];
}

function betTable(race) {
  if (race.stage === "登録") {
    return `<p class="note">買い目は馬番が決まる出馬表確定後に表示します。</p>`;
  }
  const bets = suggestBets(race);
  if (!bets.length) return "";
  const pays = race.payouts;
  const rows = bets.map((b) => {
    let res = "";
    if (pays) {
      const table = new Map((pays[b.type] || []).map(([c, v]) => [c, v]));
      const hit = b.combos.map((c) => table.get(c)).filter((v) => v != null);
      res = hit.length
        ? `<td class="mid"><span class="hit o2">的中</span></td><td class="num">${yen(hit.reduce((s, v) => s + v, 0))}円</td>`
        : `<td class="mid"><span class="hit x">×</span></td><td class="num"></td>`;
    }
    return `<tr>
      <td class="nw">${b.type} ${b.good ? '<span class="badge rec">実績◎</span>' : ""}</td>
      <td class="nw">${esc(b.label)}</td>
      <td class="num">${b.combos.length}点</td>
      <td class="num">${pct(b.prob, 1)}</td>
      ${res}</tr>`;
  }).join("");
  return `<div class="scroll"><table>
    <thead><tr><th>券種</th><th>買い目</th><th class="num">点数</th>
      <th class="num">AIの的中確率</th>
      ${pays ? '<th class="mid">結果</th><th class="num">払戻(100円)</th>' : ""}</tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="note">的中確率はAIの勝率から計算した目安です。
      <span class="badge rec">実績◎</span>＝過去の検証で控除率を上回った券種
      （複勝 85.8%・馬連 83.6% ／ 平均はそれぞれ80%・77.5%）。どの券種も長期では回収率100%に届いていません。</p>`;
}

/* ---------- 推奨馬券 (レースの性格に合わせた買い方) ---------- */

/**
 * レースごとの推奨馬券。
 *  堅軸 (本命が抜けている)   → 複勝◎ ＋ 馬連◎-○  (過去の検証で控除率を上回った2券種)
 *  荒れそう (100万超が出やすい) → ワイド◎-○・◎-▲ ＋ 三連複◎軸流し を少額で
 *  それ以外                   → 複勝◎ ＋ ワイド◎-○
 */
function recommendBets(race) {
  if (race.stage === "登録") return { kind: "wait", label: "出馬表待ち", tickets: [] };
  const m = marksOf(race);
  if (m.length < 3) return null;
  const H = harville(race);
  const [a, b, c] = m.map((e) => e.horse_no);
  const partners = m.slice(1).map((e) => e.horse_no);
  const fuku = { type: "複勝", text: `${a}`, combos: [comboKey(a)], prob: m[0].p_top3 };
  if (race.is_rec) {
    return { kind: "rec", label: "堅軸勝負", tickets: [
      fuku, { type: "馬連", text: `${a}-${b}`, combos: [comboKey(a, b)], prob: H.q(a, b) }] };
  }
  if (race.upset_label === "大荒れ警戒" || race.upset_label === "荒れ気味") {
    const trios = [];
    for (let i = 0; i < partners.length; i++)
      for (let j = i + 1; j < partners.length; j++) trios.push([a, partners[i], partners[j]]);
    return { kind: "upset", label: "穴狙い（少額）", tickets: [
      { type: "ワイド", text: `${a}-${b}・${a}-${c}`, combos: [comboKey(a, b), comboKey(a, c)],
        prob: H.wide(a, b) + H.wide(a, c) - H.trio(a, b, c) },
      { type: "三連複", text: `${a}軸-${partners.join(",")}（${trios.length}点）`,
        combos: trios.map((t) => comboKey(...t)),
        prob: trios.reduce((s, t) => s + H.trio(...t), 0) }] };
  }
  return { kind: "std", label: "標準", tickets: [
    fuku, { type: "ワイド", text: `${a}-${b}`, combos: [comboKey(a, b)], prob: H.wide(a, b) }] };
}

/** 推奨馬券の払戻 (結果が出ていれば)。返り値: null=未確定 / 0=外れ / 金額 */
function ticketPayout(race, t) {
  if (!race.payouts) return null;
  const table = new Map((race.payouts[t.type] || []).map(([c, v]) => [c, v]));
  return t.combos.reduce((s, c) => s + (table.get(c) || 0), 0);
}

const KIND_CLS = { rec: "rec", upset: "f1", std: "fx", wait: "warn" };

/** 一覧用の短い表記 */
function betsInline(race) {
  const rb = recommendBets(race);
  if (!rb) return "";
  if (rb.kind === "wait") return `<span class="badge warn">出馬表待ち</span>`;
  return `<span class="badge ${KIND_CLS[rb.kind]}">${rb.label}</span> ` +
    rb.tickets.map((t) => {
      const pay = ticketPayout(race, t);
      const mark = pay == null ? "" : pay > 0 ? ` <b class="hitpay">${yen(pay)}円</b>` : ' <span class="miss">×</span>';
      return `${t.type} ${esc(t.text)}${mark}`;
    }).join(" ／ ");
}

/** レースページの推奨馬券の枠 */
function recommendBox(race) {
  const rb = recommendBets(race);
  if (!rb || rb.kind === "wait") return "";
  const rows = rb.tickets.map((t) => {
    const pay = ticketPayout(race, t);
    return `<tr><td class="nw"><b>${t.type}</b></td><td class="nw">${esc(t.text)}</td>
      <td class="num">${t.combos.length}点</td><td class="num">${pct(t.prob, 1)}</td>
      ${race.payouts ? `<td class="mid">${pay > 0 ? '<span class="hit o2">的中</span>' : '<span class="hit x">×</span>'}</td>
        <td class="num">${pay > 0 ? yen(pay) + "円" : ""}</td>` : ""}</tr>`;
  }).join("");
  const why = {
    rec: "本命が抜けている堅いレース。過去の検証で控除率を上回った複勝と馬連で勝負します。",
    upset: "高額配当が出やすいレース。本命が飛ぶことも多いので、相手を広げて少額で狙います。",
    std: "本命を軸に、当たりやすい複勝とワイドで手堅く。",
  }[rb.kind];
  return `<div class="recbox ${rb.kind}">
    <h3><span class="badge ${KIND_CLS[rb.kind]}">${rb.label}</span> 推奨馬券</h3>
    <p class="note">${why}</p>
    <div class="scroll"><table>
      <thead><tr><th>券種</th><th>買い目</th><th class="num">点数</th><th class="num">AIの的中確率</th>
        ${race.payouts ? '<th class="mid">結果</th><th class="num">払戻(100円)</th>' : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div></div>`;
}

/* ---------- この日の注目 (堅軸・大荒れ) ---------- */

function highlights(day) {
  const recs = day.races.filter((r) => r.is_rec)
    .sort((a, b) => b.confidence - a.confidence);
  const upsets = day.races.filter((r) => r.upset_label === "大荒れ警戒" || r.upset_label === "荒れ気味")
    .sort((a, b) => (b.p_hit_1m || 0) - (a.p_hit_1m || 0)).slice(0, 6);
  const aiBasis = day.races.some((r) => r.upset_basis === "ai");
  const line = (r, extra) => `<li><a href="${raceHref(day.date, r)}">${raceLabel(r)}
      ${esc(r.race_name || "")}</a> ${gradeBadge(r.grade)} ${extra}
      <div class="bets">${betsInline(r)}</div></li>`;
  const recList = recs.length
    ? recs.map((r) => line(r, `◎${r.top_horse_no} ${esc(r.top_horse_name)}
        <small>複勝率${pct(r.top_p_top3)}</small>${r.top_finish != null ? " " + finishBadge(r.top_finish) : ""}`)).join("")
    : '<li class="none">この日は条件を満たす堅軸がありません（本命が抜けたレースが無い）</li>';
  const upList = upsets.length
    ? upsets.map((r) => line(r, `<span class="badge ${r.upset_label === "大荒れ警戒" ? "f1" : "f2"}">${esc(r.upset_label)}</span>
        <small>100万超 ${pctLo(r.p_hit_1m)}・${r.stage === "登録" ? "登録" : ""}${r.field_size}頭</small>
        ${r.trifecta_pay != null ? `<small>（実際の三連単 ${yen(r.trifecta_pay)}円）</small>` : ""}`)).join("")
    : '<li class="none">大荒れが警戒されるレースはありません</li>';
  return `<div class="hl">
    <div class="hlcol"><h3>この日の堅軸 <small>${recs.length}レース</small></h3><ul>${recList}</ul></div>
    <div class="hlcol"><h3>大荒れしそうなレース</h3><ul>${upList}</ul>
      ${aiBasis ? `<p class="note">オッズ発表前のレースは、AIの勝率から荒れ度を見積もっています
        （実配当で較正済み）。10万超の確率と想定配当はオッズが出てから出します。</p>` : ""}</div>
  </div>`;
}

/** その日の、100万馬券の法則に当てはまるレース */
function millionDayBox(day) {
  if (!MILLION) return "";
  const hits = day.races.filter((r) => (r.million_n || 0) > 0)
    .sort((a, b) => (b.million_n - a.million_n) || (a.race_no - b.race_no));
  const items = hits.map((r) => `<li><a href="${raceHref(day.date, r)}">${raceLabel(r)}
      ${esc(r.race_name || "")}</a> ${gradeBadge(r.grade)} ${millionBadge(r, true)}
      ${r.trifecta_pay != null ? `<small>（実際の三連単 ${payCell(r.trifecta_pay)}）</small>` : ""}
      <div class="mrules">${millionRulesOf(r).map((x) => esc(x.label)).join(" ／ ")}</div></li>`).join("");
  return `<div class="hlcol mbox"><h3>100万馬券の法則に当てはまるレース <small>${hits.length}レース</small></h3>
    <ul>${items || '<li class="none">この日は当てはまるレースがありません</li>'}</ul>
    <p class="note">過去の3連単100万円超えから見つけた法則です（全レース平均の100万超は${pct(MILLION.base_B, 1)}）。
      オッズを使う法則はオッズ発表後に判定されます。<a href="#/manken/${day.date}">法則の一覧 ›</a></p></div>`;
}

/** 100万馬券タブ: 法則の一覧と当てはまるレース */
async function viewMillion(date) {
  const m = await loadMillion();
  if (!m) throw new Error("100万馬券の法則はまだ作られていません。");
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = m.races.filter((r) => r.date >= today)
    .sort((a, b) => a.date.localeCompare(b.date) || (b.million_n - a.million_n) || (a.race_no - b.race_no));
  const past = m.races.filter((r) => r.date < today && r.trifecta_pay != null);
  const bc = m.by_count || {};
  const countRows = ["0", "1", "2", "3"].filter((k) => bc[k]).map((k) => `<tr>
      <td class="nw">${k === "0" ? "当てはまらない" : k === "3" ? "3件以上" : k + "件"}</td>
      <td class="num">${yen(bc[k].n)}</td><td class="num"><b>${pct(bc[k].rate, 1)}</b></td>
      <td class="num">${(bc[k].rate / m.base_B).toFixed(1)}倍</td></tr>`).join("");
  const ruleRows = m.rules.map((x, i) => `<tr>
      <td class="num">${i + 1}</td>
      <td>${esc(x.label)} ${x.uses_odds ? '<span class="badge fx">オッズ</span>' : ""}</td>
      <td class="num">${yen(x.n_A)}</td><td class="num">${pct(x.rate_A, 1)}</td>
      <td class="num">${yen(x.n_B)}</td><td class="num">${x.hit_B}</td>
      <td class="num"><b>${pct(x.rate_B, 1)}</b></td><td class="num">${x.lift_B.toFixed(1)}倍</td>
      <td class="num">${pct(x.rate_100k_B)}</td></tr>`).join("");
  const raceRows = (rows, withPay) => rows.map((r) => `<tr>
      <td class="nw"><a href="#/yoso/${r.date}">${fmtDate(r.date)}</a></td>
      <td class="nw"><a href="${raceHref(r.date, r)}">${raceLabel(r)} ${esc(r.race_name || "")}</a>
        ${r.stage === "登録" ? stageBadge(r.stage) : ""}</td>
      <td class="num">${r.stage === "登録" ? "登録" : ""}${r.field_size}頭</td>
      <td class="nw">${millionBadge(r)}</td>
      <td class="mrules">${millionRulesOf(r).map((x) => esc(x.label)).join(" ／ ")}</td>
      ${withPay ? `<td class="num">${payCell(r.trifecta_pay)}</td>` : ""}</tr>`).join("");
  const hitN = (rs) => rs.filter((r) => r.trifecta_pay >= 1e6).length;
  const hitRate = (rs) => rs.length ? hitN(rs) / rs.length : null;
  const multi = past.filter((r) => r.million_n >= 2);
  main.innerHTML = `
    <h1 class="page">100万馬券の法則</h1>
    <p class="note">中央競馬の${yen(m.n_A + m.n_B)}レース（2021年〜）の3連単の払戻から、
      <b>100万円を超えた${yen(m.hits_A + m.hits_B)}レース</b>に多い条件の組み合わせを探しました。
      ${fmtDate(m.split)}より前のレースで法則を選び、それ以降の${yen(m.n_B)}レース（選定に使っていない）でも
      全体平均（${pct(m.base_B, 1)}）の1.5倍以上出ていた法則だけを載せています。
      条件はすべて出走前に分かるもの（頭数・クラス・距離・馬場・斤量・オッズの形など）です。</p>

    <h2 class="section">これからのレースで当てはまるもの</h2>
    ${upcoming.length ? `<div class="scroll"><table class="mtable"><thead><tr><th>日付</th><th>レース</th>
      <th class="num">頭数</th><th>当てはまり</th><th>法則</th></tr></thead>
      <tbody>${raceRows(upcoming, false)}</tbody></table></div>`
      : '<p class="note">今週のレースで当てはまるものはまだありません（出馬表・オッズが出ると判定されます）。</p>'}
    <p class="note">特別登録の段階では頭数を18頭（フルゲート）とみなして判定しています。
      出馬表・オッズが出ると判定し直すので、当てはまる法則は増えたり減ったりします。</p>

    <h2 class="section">当てはまる法則の数と、100万馬券の出やすさ</h2>
    <div class="scroll"><table><thead><tr><th>当てはまる法則</th><th class="num">レース数</th>
      <th class="num">100万超の割合</th><th class="num">平均との比</th></tr></thead>
      <tbody>${countRows}</tbody></table></div>
    <p class="note">${fmtDate(m.split)}以降（法則選びに使っていないレース）での実績。法則を多く満たすほど荒れやすくなっています。
      ただし3件以上でも100万馬券は${pct(bc["3"]?.rate, 1)}程度で、確実に荒れるわけではありません。
      「当たれば大きい」レースを選ぶ目安として使ってください。</p>

    <h2 class="section">法則の一覧</h2>
    <div class="scroll"><table class="mtable"><thead>
      <tr><th class="num">#</th><th>条件</th><th class="num">選定レース</th><th class="num">選定の100万超</th>
        <th class="num">確認レース</th><th class="num">確認で出た数</th><th class="num">確認の100万超</th>
        <th class="num">平均との比</th><th class="num">10万超</th></tr></thead>
      <tbody>${ruleRows}</tbody></table></div>
    <p class="note">選定＝${fmtDate(m.split)}より前、確認＝それ以降。「オッズ」の付いた法則は単勝オッズの形を使うので、
      オッズ発表後に判定されます。斤量差は出走馬の斤量の最大と最小の差（ハンデ戦で大きくなる）。
      「10万超」は確認期間で3連単が10万円を超えた割合です。</p>

    <h2 class="section">直近1年の答え合わせ</h2>
    <p class="note">法則に当てはまった${yen(past.length)}レースのうち、100万馬券は
      <b>${hitN(past)}レース（${pct(hitRate(past), 1)}）</b>。
      2件以上当てはまったレースでは ${yen(multi.length)}レース中 ${hitN(multi)}（${pct(hitRate(multi), 1)}）。
      ※この期間の一部は法則を選んだ期間と重なります。</p>
    <details class="cond"><summary>レースの一覧（${yen(past.length)}レース）</summary>
      <div class="scroll"><table class="mtable"><thead><tr><th>日付</th><th>レース</th><th class="num">頭数</th>
        <th>当てはまり</th><th>法則</th><th class="num">3連単</th></tr></thead>
        <tbody>${raceRows(past, true)}</tbody></table></div></details>`;
}

/** 登録段階の日は、まだ出馬表の無いレースも 1〜12R を枠だけ並べる */
function withPlaceholders(day, races) {
  if (!races.some((r) => r.stage === "登録")) return races;
  const prefix = races[0].race_id.slice(0, 10);
  const byNo = new Map(races.map((r) => [r.race_no, r]));
  return Array.from({ length: 12 }, (_, i) => byNo.get(i + 1) ||
    { placeholder: true, race_no: i + 1, place: races[0].place, race_id: `${prefix}${pad2(i + 1)}` });
}

/** レースページ上部の「AIの予想」まとめ */
function raceSummary(race) {
  const m = marksOf(race);
  const regNo = race.stage === "登録";
  const rows = m.map((e) => `
    <tr>
      <td class="mid">${markSpan(e)}</td>
      <td class="num">${regNo ? "" : e.horse_no}</td>
      <td class="nw"><b>${esc(e.horse_name)}</b>
        ${e.sire ? `<div class="ped">父 ${esc(e.sire)}</div>` : ""}</td>
      <td class="num">${pct(e.p_win, 1)}</td>
      <td class="num">${pct(e.p_top3)}</td>
      <td class="num">${e.ability_idx ?? ""}</td>
      <td class="num">${num(e.odds)}</td>
      <td class="mid">${esc(e.style || "")}</td>
      <td class="nw">${esc(e.jockey_name || "")}</td>
      ${race.winner_no != null ? `<td class="mid">${finishBadge(e.finish)}</td>` : ""}
    </tr>`).join("");
  return `
    <div class="predbox">
      <h2>AIの予想</h2>
      <div class="scroll"><table>
        <thead><tr><th class="mid">印</th><th class="num">${regNo ? "" : "馬番"}</th><th>馬名</th>
          <th class="num">勝率</th><th class="num">複勝率</th><th class="num">指数</th>
          <th class="num">単勝</th><th class="mid">脚質</th><th>${regNo ? "前走騎手" : "騎手"}</th>
          ${race.winner_no != null ? '<th class="mid">着順</th>' : ""}</tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="why"><b>根拠</b> ${esc(race.reason || "")}</p>
      ${race.million_n ? `<p class="why">${millionBadge(race, true)}
        ${millionRulesOf(race).map((x) => esc(x.label)).join(" ／ ")}
        <a href="#/manken/">法則の一覧 ›</a></p>` : ""}
      ${race.is_rec ? '<p><span class="badge rec">堅軸</span> 本命が抜けている推奨レースです。</p>' : ""}
      ${recommendBox(race)}
      <h3>買い目の例（全券種）</h3>
      ${betTable(race)}
    </div>`;
}

async function viewRace(date, raceId, sub = "ai") {
  const day = await loadDay(date);
  const race = day.races.find((r) => r.race_id === raceId);
  if (!race) throw new Error(`${raceId} はこの日のデータにありません。`);

  const sameVenue = day.races.filter((r) => r.place === race.place);
  const links = sameVenue.map((r) =>
    r.race_id === raceId
      ? `<span class="cur">${r.race_no}R</span>`
      : `<a href="${raceHref(date, r)}">${r.race_no}R</a>`).join("");
  const others = day.races
    .filter((r) => r.place !== race.place && r.race_no === race.race_no)
    .map((r) => `<a href="${raceHref(date, r)}">${esc(r.place)} ${r.race_no}R ›</a>`)
    .join(" ");

  main.innerHTML = `
    <div class="rlinks"><a href="#/yoso/${date}">${fmtDate(date)}</a>
      <span class="cur">${esc(race.place)}</span></div>
    <div class="rlinks">${links}</div>
    <div class="racehead">
      <div class="big">${race.race_no}<small>R</small></div>
      <div>
        <h1>${esc(race.place)} ${race.race_no}R ${esc(race.race_name || "")}
          ${gradeBadge(race.grade)} ${stageBadge(race.stage)}</h1>
        <div class="rmeta">${race.post_time ? race.post_time + "発走 / " : ""}
          <span class="dist">${esc(race.surface || "")}${race.distance || ""}m${
            race.surface === "障" || race.is_nar ? "" : LEFT_TURN.has(race.place) ? " (左)" : " (右)"}</span>
          ${race.weather ? " / 天候:" + esc(race.weather) : ""}
          ${race.going ? " / 馬場:" + esc(race.going) : ""}</div>
        <div class="rmeta">${fmtDate(date)} ${esc(race.place)} ${race.field_size}頭
          ${race.top_finish != null ? '<span class="badge fx">結果あり</span>' : ""}</div>
      </div>
    </div>
    <div class="rlinks between"><span>${others}</span>
      <span><a href="#/yoso/${date}">この日の予想一覧</a>
        <a href="#/kekka/${date}">この日の結果</a></span></div>
    ${sampleNote(race.in_sample)}
    ${race.stage === "登録" ? `<p class="note"><span class="badge warn">特別登録段階</span>
      登録馬（${race.field_size}頭）全体での予想です。除外・回避で出走しない馬も含み、
      枠・馬番・騎手は未確定です。出馬表が出ると自動で更新されます。</p>` : ""}
    ${raceSummary(race)}
    <div class="subtabs">${SUBTABS.map(([id, label]) =>
      `<button data-sub="${id}" class="${id === sub ? "on" : ""}">${label}</button>`
    ).join("")}</div>
    <div class="sortbar">並び順
      ${SORTS.map(([id, label]) => `<button data-sort="${id}"
        ${id === "odds" && !hasOdds(race) ? 'disabled title="オッズ発表前"' : ""}>${label}</button>`).join("")}
    </div>
    <div id="subbody"></div>`;

  main.querySelectorAll("[data-sub]").forEach((b) =>
    b.addEventListener("click", () => {
      location.hash = `#/race/${date}/${raceId}/${b.dataset.sub}`;
    }));

  const render = () => {
    let mode = getSort();
    if (mode === "odds" && !hasOdds(race)) mode = "ai";
    main.querySelectorAll("[data-sort]").forEach((b) =>
      b.classList.toggle("on", b.dataset.sort === mode));
    document.getElementById("subbody").innerHTML =
      sub === "ai" ? aiTable(race, mode)
        : sub === "index" ? indexTable(race, mode) : shutubaTable(race, mode);
  };
  main.querySelectorAll("[data-sort]").forEach((b) =>
    b.addEventListener("click", () => { setSort(b.dataset.sort); render(); }));
  render();
}

/* ---------- 並び順 (AI順 / オッズ順 / 馬番順) ---------- */

const SORTS = [["ai", "AI順"], ["odds", "オッズ順"], ["no", "馬番順"]];
const hasOdds = (race) => race.entries.some((e) => e.odds != null);
function getSort() {
  try { return localStorage.getItem("sort") || "ai"; } catch { return "ai"; }
}
function setSort(v) {
  try { localStorage.setItem("sort", v); } catch {}
}
function sortEntries(race, mode) {
  const es = [...race.entries];
  if (mode === "odds") {
    // オッズの無い馬 (取消など) は最後
    return es.sort((a, b) => (a.odds ?? 1e9) - (b.odds ?? 1e9) || a.rank - b.rank);
  }
  if (mode === "no") return es.sort((a, b) => a.horse_no - b.horse_no);
  return es.sort((a, b) => a.rank - b.rank);
}

function shutubaTable(race, mode = "no") {
  const byNo = sortEntries(race, mode);
  const maxP = Math.max(...byNo.map((e) => e.p_win || 0), 0.01);
  const rows = byNo.map((e) => `
    <tr>
      <td class="num">${e.finish ?? ""}</td>
      <td class="num">${race.stage === "登録" ? "" : e.waku ?? ""}</td>
      <td class="num">${e.horse_no}</td>
      <td class="mid"><span class="mark m${Math.min(e.rank - 1, 3)}">${e.mark || ""}</span></td>
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
      <th class="num">着</th><th class="num">枠</th>
      <th class="num">${race.stage === "登録" ? "登録順" : "馬番"}</th>
      <th class="mid">印</th><th>馬名</th><th class="mid">性齢</th>
      <th class="num">斤量</th><th>騎手</th><th class="num">単勝</th>
      <th class="num">人気</th><th class="num">馬体重</th>
      <th>AI勝率</th><th class="num">複勝率</th>
      <th class="num">タイム</th><th class="num">上がり</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <p class="note">印＝AIの勝率順 ◎○▲△。AI勝率はレース内で合計100%になるよう正規化しています。</p>`;
}

function aiTable(race, mode = "ai") {
  const sorted = sortEntries(race, mode);
  const rows = sorted.map((e) => `
    <tr>
      <td class="num">${e.rank}</td>
      <td class="mid"><span class="mark m${Math.min(e.rank - 1, 3)}">${e.mark || ""}</span></td>
      <td class="num">${e.horse_no}</td>
      <td class="nw">${esc(e.horse_name)}</td>
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

/* ---------- 能力指数 ---------- */

const raceLabel = (r) => `${esc(r.place)}${r.race_no}R`;

/** その日の全出走馬を能力指数の高い順に並べる */
async function viewIndex(date, place = "") {
  const day = await loadDay(date);
  const places = [...new Set(day.races.map((r) => r.place))];
  const rows = [];
  for (const r of day.races) {
    if (place && r.place !== place) continue;
    for (const e of r.entries) {
      if (e.ability_idx != null) rows.push({ r, e });
    }
  }
  rows.sort((a, b) => b.e.ability_idx - a.e.ability_idx
    || (b.e.p_win || 0) - (a.e.p_win || 0));
  const shown = rows.slice(0, 60);
  const withResult = rows.some(({ e }) => e.finish != null);
  const body = shown.map(({ r, e }, i) => `
    <tr>
      <td class="num">${i + 1}</td>
      <td class="num"><b>${e.ability_idx}</b></td>
      <td class="nw">${esc(e.horse_name)}
        ${e.sire ? `<div class="ped">父 ${esc(e.sire)}</div>` : ""}</td>
      <td class="nw"><a href="${raceHref(date, r)}">${raceLabel(r)}</a>
        <div class="ped">${esc(r.surface || "")}${r.distance || ""}m</div></td>
      <td class="mid"><span class="mark m${Math.min((e.rank || 9) - 1, 3)}">${e.mark || ""}</span></td>
      <td class="num">${pct(e.p_win, 1)}</td>
      <td class="num">${num(e.odds)}</td>
      <td class="num">${e.popularity ?? ""}</td>
      <td class="mid">${esc(e.style || "")}</td>
      ${withResult ? `<td class="mid">${finishBadge(e.finish)}</td>` : ""}
    </tr>`).join("");
  const filter = ["", ...places].map((p) =>
    `<a href="#/nouryoku/${date}${p ? "/" + encodeURIComponent(p) : ""}"
        class="${p === place ? "cur" : ""}">${p || "全場"}</a>`).join("");
  main.innerHTML = `
    <h1 class="page">能力指数</h1>
    ${dayNav(day, "nouryoku")}
    <div class="rlinks pills">${filter}</div>
    <div class="scroll"><table>
      <thead><tr>
        <th class="num">順</th><th class="num">指数</th><th>馬名</th><th>レース</th>
        <th class="mid">印</th><th class="num">AI勝率</th><th class="num">単勝</th>
        <th class="num">人気</th><th class="mid">脚質</th>
        ${withResult ? '<th class="mid">着順</th>' : ""}
      </tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">能力指数は0〜100で、全出走馬の中での位置（100＝上位1%）。
      レースの枠を越えて馬の地力を比べられます。上位${shown.length}頭を表示。
      脚質は近5走の1コーナー通過位置から。${INDEX_DEF}</p>
    ${indexReportBox()}`;
}

/* ---------- 血統 ---------- */

let SIRES = null;
const normSire = (s) => (s || "")
  .replace(/[(（].*?[)）]/g, "").replace(/[A-Za-z0-9'’.\-\s]+/g, "");

async function loadSires() {
  if (!SIRES) {
    const list = await getJSON("data/sires.json");
    SIRES = { list, by: new Map(list.map((s) => [s.sire, s])) };
  }
  return SIRES;
}

const SIRE_COLS = [
  ["n", "産駒出走"], ["win", "勝率"], ["top3", "複勝率"], ["turf", "芝"],
  ["dirt", "ダート"], ["short", "〜1400"], ["mile", "〜1800"], ["mid", "〜2200"],
  ["long", "2200〜"], ["roi", "単勝回収"],
];

function sireCells(s) {
  return SIRE_COLS.map(([k]) => `<td class="num">${
    s[k] == null ? "" : k === "n" ? s[k] : pct(s[k], k === "roi" ? 0 : 1)}</td>`).join("");
}

async function viewSires(date, sortKey = "n") {
  const [day, sires] = await Promise.all([loadDay(date), loadSires()]);

  // この日の出走馬を父ごとにまとめる
  const today = new Map();
  for (const r of day.races) {
    for (const e of r.entries) {
      const key = normSire(e.sire);
      if (!key) continue;
      if (!today.has(key)) today.set(key, []);
      today.get(key).push({ r, e });
    }
  }
  const todayRows = [...today]
    .map(([key, runs]) => ({ key, runs, s: sires.by.get(key) }))
    .sort((a, b) => b.runs.length - a.runs.length || (b.s?.top3 || 0) - (a.s?.top3 || 0))
    .slice(0, 25)
    .map(({ key, runs, s }) => `
      <tr>
        <td class="nw"><b>${esc(key)}</b></td>
        ${s ? sireCells(s) : `<td colspan="${SIRE_COLS.length}" class="note">産駒の出走が少なく集計対象外</td>`}
        <td>${runs.map(({ r, e }) => `<a href="${raceHref(date, r)}">${raceLabel(r)}</a>
          ${esc(e.horse_name)}${e.finish != null ? `（${e.finish}着）` : ""}`).join(" ／ ")}</td>
      </tr>`).join("");

  const sorted = [...sires.list].sort((a, b) => (b[sortKey] ?? -1) - (a[sortKey] ?? -1));
  const head = SIRE_COLS.map(([k, label]) =>
    `<th class="num"><a href="#/ketto/${date}/${k}" class="${k === sortKey ? "sorted" : ""}">${label}</a></th>`
  ).join("");
  const allRows = sorted.slice(0, 100).map((s, i) => `
    <tr><td class="num">${i + 1}</td><td class="nw">${esc(s.sire)}</td>${sireCells(s)}</tr>`).join("");

  main.innerHTML = `
    <h1 class="page">血統</h1>
    ${dayNav(day, "ketto")}
    <h2 class="section">この日の出走馬の父（${fmtDate(date)}）</h2>
    <div class="scroll"><table>
      <thead><tr><th>種牡馬</th>${SIRE_COLS.map(([, l]) => `<th class="num">${l}</th>`).join("")}
        <th>この日の産駒</th></tr></thead>
      <tbody>${todayRows || `<tr><td colspan="${SIRE_COLS.length + 2}">血統データがありません</td></tr>`}</tbody>
    </table></div>
    <h2 class="section">種牡馬ランキング</h2>
    <div class="scroll"><table>
      <thead><tr><th class="num">順</th><th>種牡馬</th>${head}</tr></thead>
      <tbody>${allRows}</tbody></table></div>
    <p class="note">中央競馬の全出走（${INDEX?.from ? fmtDate(INDEX.from) : ""}〜）の産駒成績。
      芝・ダート・距離帯の数字は複勝率です（10走未満は空欄）。
      産駒が40走以上の種牡馬だけを載せています。見出しを押すと並べ替えられます。</p>`;
}

/* ---------- ゲーム (AIと予想対決) ---------- */

const gameKey = (date) => `game:${date}`;
function loadPicks(date) {
  try { return JSON.parse(localStorage.getItem(gameKey(date)) || "{}"); } catch { return {}; }
}
function savePicks(date, picks) {
  try { localStorage.setItem(gameKey(date), JSON.stringify(picks)); } catch {}
}
/** 1着=3点、2〜3着=1点 */
const points = (f) => (f == null ? 0 : f === 1 ? 3 : f <= 3 ? 1 : 0);

async function viewGame(date) {
  const day = await loadDay(date);
  const picks = loadPicks(date);
  const done = day.races.some((r) => r.top_finish != null);

  let you = 0, ai = 0, youHit = 0, aiHit = 0, judged = 0;
  const rows = day.races.map((r) => {
    const byNo = [...r.entries].sort((a, b) => a.horse_no - b.horse_no);
    const mine = picks[r.race_id];
    const pick = byNo.find((e) => e.horse_no === mine);
    const aiPick = r.entries.find((e) => e.rank === 1);
    if (pick && pick.finish != null && aiPick) {
      judged++;
      you += points(pick.finish); ai += points(aiPick.finish);
      youHit += pick.finish <= 3; aiHit += aiPick.finish <= 3;
    }
    const opts = byNo.map((e) =>
      `<option value="${e.horse_no}" ${e.horse_no === mine ? "selected" : ""}>
        ${e.horse_no} ${esc(e.horse_name)}</option>`).join("");
    return `
      <tr>
        <td class="nw"><a href="${raceHref(date, r)}">${raceLabel(r)}</a>
          <div class="ped">${esc(r.race_name || "")}</div></td>
        <td><select data-race="${r.race_id}"><option value="">― 選ぶ ―</option>${opts}</select></td>
        <td class="mid">${pick ? finishBadge(pick.finish) : ""}</td>
        <td class="nw">${done && aiPick ? `◎ ${aiPick.horse_no} ${esc(aiPick.horse_name)}` : "（結果後に公開）"}</td>
        <td class="mid">${done && aiPick ? finishBadge(aiPick.finish) : ""}</td>
      </tr>`;
  }).join("");

  const score = judged ? `
    <div class="scoreboard">
      <div><span>あなた</span><b>${you}点</b><small>複勝圏 ${youHit}/${judged}</small></div>
      <div class="vs">VS</div>
      <div><span>AI</span><b>${ai}点</b><small>複勝圏 ${aiHit}/${judged}</small></div>
    </div>
    <p class="msgline">${you > ai ? "🎉 あなたの勝ち！" : you < ai ? "AIの勝ち。次は勝てるかも" : "引き分け"}</p>` : "";

  main.innerHTML = `
    <h1 class="page">ゲーム：AIと予想対決</h1>
    ${dayNav(day, "game")}
    <p class="note">各レースで本命を1頭選んでください。1着なら3点、2〜3着なら1点。
      AIの本命（◎）と点数を競います。${done ? "この日は結果が出ているので、選ぶとすぐ採点されます。"
      : "結果が出たら、この画面を開き直すと採点されます。"} 選んだ馬はこのブラウザに保存されます。</p>
    ${score}
    <div class="scroll"><table>
      <thead><tr><th>レース</th><th>あなたの本命</th><th class="mid">着順</th>
        <th>AIの本命</th><th class="mid">着順</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p><button class="btn" id="reset-game">選び直す（この日の選択を消す）</button></p>`;

  main.querySelectorAll("select[data-race]").forEach((s) =>
    s.addEventListener("change", () => {
      const p = loadPicks(date);
      if (s.value) p[s.dataset.race] = Number(s.value); else delete p[s.dataset.race];
      savePicks(date, p);
      viewGame(date);
    }));
  document.getElementById("reset-game").addEventListener("click", () => {
    savePicks(date, {});
    viewGame(date);
  });
}

/* ---------- ルーティング ---------- */

async function route() {
  const [page, a, b, c] = location.hash.replace(/^#\/?/, "").split("/")
    .map((s) => decodeURIComponent(s || ""));
  const tabIds = TABS.map((t) => t.id);
  renderTabs(tabIds.includes(page) ? page : page === "race" ? "" : "top");
  main.innerHTML = `<div class="msg">読み込み中...</div>`;
  try {
    if (page === "race") return await viewRace(a, b, c || "ai");
    const date = a || defaultDate();
    if (!date) throw new Error("表示できる開催日がありません。");
    if (page === "manken") return await viewMillion(date);
    if (page === "nouryoku") return await viewIndex(date, b);
    if (page === "ketto") return await viewSires(date, b || "n");
    if (page === "game") return await viewGame(date);
    if (page === "kekka") return await viewResults(date);
    if (page === "yoso") return await viewDay(date, { route: "yoso", title: "予想" });
    return await viewDay(date, { route: "top" });
  } catch (e) {
    main.innerHTML = `<div class="msg">${esc(e.message)}</div>`;
  }
}

function renderTabs(active) {
  const d = INDEX ? defaultDate() || "" : "";
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
    await loadIndex();
    await loadMillion();
  } catch (e) {
    main.innerHTML = `<div class="msg">${esc(e.message)}</div>`;
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}

start();
