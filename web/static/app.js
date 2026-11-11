"use strict";

// データは web/export.py が書き出した静的 JSON。1開催日=1ファイルで、
// その日のトップ・予想・結果・レース詳細を全部まかなう。

const TABS = [
  { id: "top", label: "トップ" },
  { id: "yoso", label: "予想" },
  { id: "kekka", label: "結果" },
  { id: "seiseki", label: "成績" },
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
// 「荒れ」側として扱う荒れ度のラベル (keiba/upset.py の label と対応)
const HOT = ["特大荒れ", "大荒れ警戒", "荒れ気味"];
const hotClass = (l) => (l === "特大荒れ" ? "f0" : l === "大荒れ警戒" ? "f1" : "f2");
const num = (v, d = 1) => (v == null ? "" : Number(v).toFixed(d));
const yen = (v) => (v == null ? "" : Number(v).toLocaleString());

async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} が読めませんでした (${res.status})`);
  // 無い URL には index.html が 200 で返ってくる (SPA のフォールバック) ので、
  // JSON として読めなかったら「その日のデータが無い」として扱う。
  // そのまま res.json() を投げると生の構文エラーが画面に出てしまう。
  try {
    return await res.json();
  } catch {
    throw new Error("その日のデータはありません（開催が無い日かもしれません）");
  }
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
  if (stage === "当日") return '<span class="badge star">馬体重発表後</span>';
  if (stage === "直前") return '<span class="badge rec">直前更新</span>';
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
        const cell = (e) => e ? `${hnoBox(r, e.horse_no)}<b>${esc(e.horse_name)}</b>${
          e.finish != null ? ` <small>(${e.finish}着)</small>` : ""}` : "";
        return `<tr class="click" data-href="${raceHref(day.date, r)}">
          <td class="num"><a href="${raceHref(day.date, r)}">${r.race_no}</a></td>
          <td class="nw">${esc(r.post_time || "")}</td>
          <td class="nw"><a href="${raceHref(day.date, r)}">${esc(r.race_name || "")}</a>
            ${gradeBadge(r.grade)} ${r.is_rec ? '<span class="badge rec">堅軸</span>' : ""}
            ${r.stage === "直前" && r.winner_no == null ? '<span class="badge rec small">直前</span>' : ""}</td>
          <td class="nw"><span class="dist">${esc(r.surface || "")}${r.distance || ""}m</span>
            ${r.field_size}頭</td>
          <td class="nw horse">${markSpan(m[0] || {})} ${cell(m[0])}</td>
          <td class="nw horse">${cell(m[1])}</td>
          <td class="nw horse">${cell(m[2])}</td>
          <td class="nw horse">${[m[3], m[4]].filter(Boolean).map((e) => `<span class="sub">${cell(e)}</span>`).join("")}</td>
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
  const cls = { "特大荒れ": "f0", "大荒れ警戒": "f1", "荒れ気味": "f2", "標準": "fx", "堅い": "rec" };
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

/** オッズ発表前の荒れ度が検証期間でどれだけ当たっていたか */
function upsetAiNote() {
  const a = INDEX?.upset_report?.paths?.ai;
  if (!a?.quintiles?.length) return "";
  const top = a.quintiles[a.quintiles.length - 1];
  return `検証期間${a.n_valid.toLocaleString()}レースでは、オッズ前の荒れ度が最も高い
    5分の1（${top.n}レース）で100万超が実際に${pct(top.act, 1)}出ました
    （推定${pct(top.est, 1)}）。`;
}

/** 過去の100万超レースから分かった発生条件 (index.json の upset_report から作る) */
function upsetConditions() {
  const c = INDEX?.upset_report?.conditions;
  if (!c?.axes?.length) return "";
  const cell = (it) =>
    it.hits === 0 ? `0件 / ${it.n.toLocaleString()}R` : pctLo(it.rate);
  const body = c.axes.map((ax) => ax.items.map((it, i) => `
    <tr class="${ax.kind === "field" ? "fieldrow" : ""}">
      ${i === 0 ? `<td rowspan="${ax.items.length}" class="nw"><b>${esc(ax.name)}</b>
        ${ax.kind === "field" ? '<br><span class="badge star small">顔ぶれ</span>' : ""}
        ${ax.spread ? `<div class="small muted">差 ${ax.spread.toFixed(1)}倍</div>` : ""}</td>` : ""}
      <td class="nw">${esc(it.label)}</td>
      <td class="num">${cell(it)}</td>
      <td class="num">${it.n.toLocaleString()}R</td>
      <td class="num">${it.lift == null ? "" : it.lift.toFixed(2) + "倍"}</td>
    </tr>`).join("")).join("");
  const top = c.axes[0];
  return `<details class="cond">
    <summary>どんなレースが荒れるのか（配当の分かる${c.n.toLocaleString()}レースの実績）</summary>
    <div class="scroll"><table>
      <thead><tr><th>条件</th><th></th><th class="num">100万超の発生率</th>
        <th class="num">レース数</th><th class="num">全体比</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <p class="note">全体の発生率${pct(c.base, 1)}に対する倍率です。条件は<b>効き目の大きい順</b>
      （いちばん荒れる区分と、いちばん堅い区分の発生率の比＝「差」）に並べています。
      いちばん効くのは「${esc(top.name)}」です。</p>
    <p class="note"><span class="badge star small">顔ぶれ</span> は出走馬の成績・タイムから見た条件で、どれも出走前に分かるものです。
      能力指数80以上＝AIが見た実力上位（全出走馬の上位2割）、走破タイム＝近走の走破タイムを競馬場・距離・馬場でそろえた速さ、
      逃げ馬＝近5走の1コーナーで先頭集団（前15%）にいた馬。実力馬が少ない・タイムが拮抗・前走好走馬が多い・逃げ馬が多い（先行争い）ほど荒れます。
      頭数をそろえても（16頭以上だけで見ても）この向きは変わりません。
      「能力指数80以上の馬」「走破タイム上位3頭の差」「前走3着以内の馬」「逃げ馬」の4つは、荒れ度の確率にも足しています
      （「AIの本命の勝率」「1番人気」は元から荒れ度の計算に入っています）。足したことで、確認期間（2026/5/12以降の1,728レース）では
      荒れ度が上位2割のレースの100万超が4.0%→6.6%に、2024年以降の8,709レースで確かめた別の検証では4.1%→4.3%になりました。</p>
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
    HOT.includes(r.upset_label)
      ? `<span class="badge ${hotClass(r.upset_label)}">${esc(r.upset_label)}</span>`
      : "",
    millionBadge(r),
  ].filter(Boolean).join(" ");
  const m = marksOf(r, 3);
  const subs = m.slice(1).map((e) =>
    `${markSpan(e)}${noOf(r, e.horse_no)}${esc(e.horse_name)}`).join("　");
  return `
    <a class="rrow card" href="${raceHref(date, r)}">
      <div class="rno">${r.race_no}<small>R</small></div>
      <div class="rbody">
        <div class="rhead">${r.race_no}R ${esc(r.race_name || "")} ${gradeBadge(r.grade)}</div>
        <div class="rmeta">${meta}</div>
        <div class="rpick">
          <span class="name"><span class="mark m0">◎</span>
            ${noOf(r, r.top_horse_no)}${esc(r.top_horse_name)}</span>
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
  if (route === "top") await loadStats();
  const fav = route === "top" ? await favBox() : "";
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
    ${route === "top" ? `${valueDayBox(day)}<div class="topboxes">${recentBox()}${fav}</div>` : ""}
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
    ${recommendTable(day.date, recs, { withResult: true })}
    ${missReview(day)}`;
}

/** 本命・堅軸・指数1位が4着以下だったときの敗因 (回顧から) */
function missReview(day) {
  const rows = [];
  for (const r of day.races) {
    if (r.top_finish == null) continue;
    const maxIdx = Math.max(...r.entries.map((e) => e.ability_idx ?? -1));
    for (const e of r.entries) {
      if (e.finish == null || e.finish <= 3) continue;
      const kinds = [];
      if (e.rank === 1) kinds.push("本命");
      if (e.rank === 1 && r.is_rec) kinds.push("堅軸");
      if (e.ability_idx != null && e.ability_idx === maxIdx) kinds.push("指数1位");
      if (!kinds.length) continue;
      rows.push({ r, e, kinds, cause: e.rv?.cause || "その他" });
    }
  }
  if (!rows.length) return "";
  const counts = {};
  rows.forEach((x) => { counts[x.cause] = (counts[x.cause] || 0) + 1; });
  const body = rows.map(({ r, e, kinds, cause }) => {
    const rv = e.rv || {};
    const run = [rv.pos != null ? `序盤${rv.pos}番手` : "", rv.ar != null ? `上がり${rv.ar}位` : "",
      rv.tw != null ? `勝ち馬と${rv.tw.toFixed(1)}秒差` : ""].filter(Boolean).join("・");
    return `<tr><td class="nw">${esc(cause)}</td><td class="nw">${kinds.join("・")}</td>
      <td class="nw"><a href="${raceHref(day.date, r)}/review">${raceLabel(r)}</a></td>
      <td class="nw">${e.horse_no} ${esc(e.horse_name)}</td>
      <td class="num">${e.finish}</td><td class="num">${e.popularity ?? ""}</td>
      <td class="nw">${esc(run)}</td><td class="mid">${memoBadge(rv.memo)}</td></tr>`;
  }).join("");
  return `<h2 class="section">着外の回顧（本命・堅軸・指数1位）</h2>
    <p class="note">AIの上位の馬が4着以下だったときの主な敗因です：
      ${Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} ${v}頭`).join("／")}</p>
    <div class="scroll"><table><thead><tr><th>敗因</th><th>区分</th><th>レース</th><th>馬</th>
      <th class="num">着順</th><th class="num">人気</th><th>中身</th><th class="mid">次走へのメモ</th></tr></thead>
      <tbody>${body}</tbody></table></div>
    <p class="note">敗因は通過順・上がり3F・3着とのタイム差から、出遅れ・位置取り（いつもより大きく後ろ）→
      前残りで届かず → 惜敗（3着と0.1秒以内）→ 先行して失速（前で運んで上がり下位）→ 伸びず の順で当てはめています。
      レース名を押すと回顧へ移動します。</p>`;
}

const SUBTABS = [["shutuba", "出馬表"], ["past", "近走"], ["ai", "AI予想"], ["sim", "シミュレーション"], ["index", "能力指数"], ["same", "同じレース"], ["review", "回顧"]];

/* ---------- オッズの動き (前日最終 → 直前・確定) ---------- */

/** 馬番ごとの [基準, 最新] のオッズ。基準は前日最終 (無ければ最初に取れた時点) */
function oddsPair(race, no) {
  const pts = race.odds_mv?.pts;
  if (!pts?.length) return null;
  const at = (p) => p.w?.[no - 1] ?? null;
  const base = pts.find((p) => at(p) != null);
  const last = [...pts].reverse().find((p) => at(p) != null);
  if (!base || !last || base === last) return null;
  return { from: at(base), to: at(last), fromLabel: base.t, toLabel: last.t };
}

/** 単勝オッズの横の矢印。↓=売れている (オッズが下がった)、↑=人気が落ちた */
function oddsArrow(race, e) {
  const p = oddsPair(race, e.horse_no);
  if (!p) return "";
  const r = p.to / p.from;
  const tip = `${p.fromLabel} ${p.from} → ${p.toLabel} ${p.to}`;
  if (r <= 0.67) return `<span class="oa dn2" title="${esc(tip)}">⇓</span>`;
  if (r <= 0.8) return `<span class="oa dn" title="${esc(tip)}">↓</span>`;
  if (r >= 1.5) return `<span class="oa up2" title="${esc(tip)}">⇑</span>`;
  if (r >= 1.25) return `<span class="oa up" title="${esc(tip)}">↑</span>`;
  return "";
}

/** 表示するオッズ: 当日の速報が新しければそちら */
function liveOdds(race, e) {
  const mv = race.odds_mv;
  if (mv?.live && mv.pts?.length) {
    const v = mv.pts[mv.pts.length - 1].w?.[e.horse_no - 1];
    if (v != null) return v;
  }
  return e.odds;
}

function oddsStatsNote() {
  const st = INDEX?.odds_stats;
  if (!st?.rows?.length) return "";
  const rows = st.rows.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${r.n.toLocaleString()}</td>
    <td class="num">${pct(r.top3, 1)}</td><td class="num">${pct(r.expected, 1)}</td>
    <td class="num ${r.top3 - r.expected >= 0 ? "plus" : "minus"}">${((r.top3 - r.expected) * 100).toFixed(1)}pt</td>
    <td class="num">${r.roi_win != null ? pct(r.roi_win) : ""}</td></tr>`).join("");
  return `<details class="help"><summary>オッズの動きの実績（${esc(st.from || "")}〜 ${(st.races || 0).toLocaleString()}レース）</summary>
    <p>前日最終から発走40分前までのオッズの動き別に、その馬の3着内率を、40分前の人気が同じ馬の平均と比べました。</p>
    <div class="scroll"><table><thead><tr><th>動き</th><th class="num">頭数</th><th class="num">3着内</th>
      <th class="num">同じ人気の平均</th><th class="num">差</th><th class="num">単勝回収率</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">${esc(st.note || "")}</p></details>`;
}

function oddsMoveTable(race) {
  const mv = race.odds_mv;
  if (!mv?.pts?.length) return "";
  // 列が多すぎると読めないので最大8列 (最初と最後は残す)
  let pts = mv.pts;
  if (pts.length > 8) {
    const step = (pts.length - 1) / 7;
    pts = Array.from({ length: 8 }, (_, i) => mv.pts[Math.round(i * step)]);
  }
  const es = [...race.entries].sort((a, b) => (liveOdds(race, a) ?? 1e9) - (liveOdds(race, b) ?? 1e9));
  const rows = es.map((e) => {
    const vals = pts.map((p) => p.w?.[e.horse_no - 1] ?? null);
    const first = vals.find((v) => v != null);
    const cells = vals.map((v) => {
      if (v == null) return '<td class="num"></td>';
      const r = first ? v / first : 1;
      const cls = r <= 0.8 ? "odn" : r >= 1.25 ? "oup" : "";
      return `<td class="num ${cls}">${v >= 100 ? v.toFixed(0) : v.toFixed(1)}</td>`;
    }).join("");
    return `<tr><td class="num">${e.horse_no}</td><td class="nw">${esc(e.horse_name)}</td>
      ${cells}<td class="mid">${oddsArrow(race, e)}</td></tr>`;
  }).join("");
  return `<h3>オッズの動き（単勝）</h3>
    <div class="scroll"><table class="oddsmv">
      <thead><tr><th class="num">馬番</th><th>馬名</th>
        ${pts.map((p) => `<th class="num" title="${esc(p.at || "")}">${esc(p.t)}</th>`).join("")}
        <th class="mid">動き</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">赤＝最初の時点から2割以上下がった（売れている）、青＝2.5割以上上がった（人気が落ちた）。
      矢印 ↓⇓ は売れている、↑⇑ は人気が落ちた馬（二重は3分の2以下・1.5倍以上）。
      ${mv.live ? `最新は ${esc((mv.at || "").slice(11, 16))} 時点のJRA発表オッズです。` : ""}</p>
    ${oddsStatsNote()}`;
}

/* ---------- 馬場 (クッション値・含水率) と直前更新の表示 ---------- */

function cushionLabel(v) {
  if (v == null) return "";
  return v >= 12 ? "硬め" : v >= 10 ? "やや硬め" : v >= 8 ? "標準" : v >= 7 ? "やや軟らかめ" : "軟らかめ";
}

function babaLine(race) {
  const b = race.baba;
  if (!b) return "";
  const bits = [];
  if (b.cushion != null) bits.push(`クッション値 <b>${b.cushion}</b>（${cushionLabel(b.cushion)}）`);
  if (b.moist != null) bits.push(`含水率 ゴール前 ${b.moist}%${b.moist_4c != null ? `・4角 ${b.moist_4c}%` : ""}`);
  if (b.course) bits.push(`${esc(b.course)}コース`);
  const when = b.baba_date && b.baba_date !== race.date ? `（${b.baba_date.slice(5).replace("-", "/")} 測定）` : "";
  return `<div class="rmeta baba">${race.surface === "ダ" ? "ダート" : "芝"}の馬場: ${bits.join(" ／ ")} ${when}</div>`;
}

function liveNote(race) {
  if (race.winner_no != null) return "";
  const bits = [];
  if (race.stage === "直前") {
    bits.push(`<span class="badge rec">直前更新</span> 発走前の最新オッズ${race.model === "当日モデル" ? "・馬体重" : ""}で予想し直しました`);
  } else if (race.stage === "当日") {
    bits.push('<span class="badge star">馬体重発表</span> 馬体重が出たので当日モデルで予想し直しました');
  } else if (race.model === "前日モデル" && race.stage !== "登録") {
    bits.push('<span class="badge fx">前日モデル</span> 馬体重の発表前です（発表後と発走40分前に自動で予想し直します）');
  }
  if (!bits.length) return "";
  return `<p class="note live">${bits.join(" ")}${race.updated_at ? `（${esc(race.updated_at.slice(5).replace("-", "/"))} 更新）` : ""}</p>`;
}

/* ---------- 展開シミュレーション (1レースを1万回) ---------- */

const PACES = ["ハイ", "ミドル", "スロー"];
const SIM_N = 10000;
const WAKU_BG = ["", "#ffffff", "#222222", "#d6333a", "#2457b5", "#f2cf1d", "#2f8a4e", "#f28d1a", "#f17fb4"];
const WAKU_FG = ["", "#222", "#fff", "#fff", "#fff", "#222", "#fff", "#fff", "#fff"];

function simSetup(race, paceMode) {
  const P = INDEX?.sim_params;
  const es = race.entries.filter((e) => e.p_win != null && e.horse_no != null)
    .sort((a, b) => a.horse_no - b.horse_no);
  if (!P || es.length < 3) return null;
  const tot = es.reduce((s, e) => s + e.p_win, 0);
  const w = es.map((e) => Math.max(e.p_win / tot, 1e-5));
  const surf = race.surface === "ダ" ? "ダ" : "芝";
  let pace = P.pace?.[surf]?.[race.pace || "ミドル"] || { "ハイ": 1 / 3, "ミドル": 1 / 3, "スロー": 1 / 3 };
  // ペースを決めて走らせるときは、実際のペース別に測った脚質の向き不向きをそのまま効かせる
  const k = paceMode === "auto" ? (P.k ?? 0) : 1;
  if (paceMode !== "auto") pace = { "ハイ": 0, "ミドル": 0, "スロー": 0, [paceMode]: 1 };
  const byPace = PACES.map((pc) => {
    const a = es.map((e, i) => w[i] * Math.exp(k * (P.shift?.[e.style]?.[pc] ?? 0)));
    const s = a.reduce((x, y) => x + y, 0);
    const w1 = a.map((v) => v / s);
    return { w1, w2: w1.map((v) => v ** P.lam2), w3: w1.map((v) => v ** P.lam3) };
  });
  const cum = [];
  let c = 0;
  PACES.forEach((pc) => { c += pace[pc] || 0; cum.push(c); });
  return { es, byPace, cum, pace, P };
}

/** 1回走らせる。full=true なら全頭の着順、false なら上位3頭 */
function simOnce(S, full) {
  const u = Math.random() * S.cum[2];
  const pi = u < S.cum[0] ? 0 : u < S.cum[1] ? 1 : 2;
  const { w1, w2, w3 } = S.byPace[pi];
  const n = w1.length;
  const taken = new Uint8Array(n);
  const order = [];
  const upto = full ? n : 3;
  for (let pos = 0; pos < upto; pos++) {
    const ww = pos === 0 ? w1 : pos === 1 ? w2 : w3;
    let s = 0;
    for (let i = 0; i < n; i++) if (!taken[i]) s += ww[i];
    let r = Math.random() * s, pick = -1;
    for (let i = 0; i < n; i++) {
      if (taken[i]) continue;
      r -= ww[i];
      pick = i;
      if (r <= 0) break;
    }
    taken[pick] = 1;
    order.push(pick);
  }
  return { pace: pi, order };
}

function simCount(S, n) {
  const H = S.es.length;
  const c = { n: 0, p1: new Array(H).fill(0), p2: new Array(H).fill(0), p3: new Array(H).fill(0),
    pace: [0, 0, 0], umaren: new Map(), wide: new Map(), umatan: new Map(), trio: new Map(), tri: new Map() };
  const add = (m, k) => m.set(k, (m.get(k) || 0) + 1);
  const no = S.es.map((e) => pad2(e.horse_no));
  return {
    c,
    step(k) {
      for (let t = 0; t < k && c.n < n; t++) {
        const { pace, order: [a, b, d] } = simOnce(S, false);
        c.n++;
        c.pace[pace]++;
        c.p1[a]++; c.p2[a]++; c.p2[b]++; c.p3[a]++; c.p3[b]++; c.p3[d]++;
        const s2 = [no[a], no[b]].sort();
        const s3 = [no[a], no[b], no[d]].sort();
        add(c.umaren, s2.join(""));
        add(c.umatan, no[a] + no[b]);
        add(c.trio, s3.join(""));
        add(c.tri, no[a] + no[b] + no[d]);
        add(c.wide, s2.join(""));
        add(c.wide, [no[a], no[d]].sort().join(""));
        add(c.wide, [no[b], no[d]].sort().join(""));
      }
      return c.n >= n;
    },
  };
}

/** 終わったレースの実際の組 (当たりの印に使う) */
function actualCombos(race) {
  const fin = race.entries.filter((e) => e.finish != null && e.finish <= 3)
    .sort((a, b) => a.finish - b.finish).map((e) => pad2(e.horse_no));
  if (fin.length < 3) return null;
  const [a, b, d] = fin;
  return {
    "馬連": new Set([[a, b].sort().join("")]),
    "ワイド": new Set([[a, b].sort().join(""), [a, d].sort().join(""), [b, d].sort().join("")]),
    "馬単": new Set([a + b]),
    "三連複": new Set([[a, b, d].sort().join("")]),
    "三連単": new Set([a + b + d]),
  };
}

const SIM_BETS = [["馬連", "umaren", 2, " - "], ["ワイド", "wide", 2, " - "], ["馬単", "umatan", 2, " → "],
  ["三連複", "trio", 3, " - "], ["三連単", "tri", 3, " → "]];

function simComboTable(race, c, type, key, sep, nameOf) {
  const m = c[key];
  const odds = race.combos?.[type] || null;
  const act = actualCombos(race);
  const list = [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
  const rows = list.map(([k, cnt]) => {
    const p = cnt / c.n;
    const parts = k.match(/.{2}/g).map(Number);
    const o = odds ? odds[k] : null;
    const ev = o != null ? p * o : null;
    const hit = act?.[type]?.has(k);
    return `<tr class="${hit ? "hitrow" : ""}"><td class="nw">${parts.join(sep)}</td>
      <td class="nw small">${parts.map(nameOf).join("・")}</td>
      <td class="num">${pct(p, 1)}</td><td class="num">${num(1 / p, 1)}</td>
      ${odds ? `<td class="num">${o != null ? num(o, 1) : "—"}</td>
        <td class="num ${ev >= 1 ? "plus" : ""}">${ev != null ? ev.toFixed(2) : ""}</td>` : ""}
      ${act ? `<td class="mid">${hit ? '<span class="badge rec">的中</span>' : ""}</td>` : ""}</tr>`;
  }).join("");
  return `<div class="scroll"><table class="simtab">
    <thead><tr><th>${type}</th><th>馬名</th><th class="num">出た割合</th><th class="num">適正オッズ</th>
      ${odds ? '<th class="num">現在のオッズ</th><th class="num">期待値</th>' : ""}
      ${act ? '<th class="mid">結果</th>' : ""}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function simResults(race, S, c, bet) {
  const H = S.es.length;
  const idx = [...Array(H).keys()].sort((a, b) => c.p1[b] - c.p1[a] || c.p3[b] - c.p3[a]);
  const nameOf = (no) => esc(S.es.find((e) => e.horse_no === no)?.horse_name || "");
  const rows = idx.map((i) => {
    const e = S.es[i];
    const p1 = c.p1[i] / c.n;
    return `<tr><td class="num">${e.horse_no}</td><td class="nw">${markSpan(e)} ${esc(e.horse_name)}</td>
      <td class="mid">${esc(e.style || "")}</td>
      <td><div class="barcell"><span class="bar" style="width:80px"><i style="width:${Math.min(100, p1 * 250)}%"></i></span>
        <span>${pct(p1, 1)}</span></div></td>
      <td class="num">${pct(c.p2[i] / c.n, 1)}</td><td class="num">${pct(c.p3[i] / c.n, 1)}</td>
      <td class="num muted">${pct(e.p_win, 1)}</td>
      ${race.winner_no != null ? `<td class="mid">${finishBadge(e.finish)}</td>` : ""}</tr>`;
  }).join("");
  const paceTxt = PACES.map((p, i) => `${p} ${pct(c.pace[i] / c.n)}`).join(" ／ ");
  const tabs = SIM_BETS.map(([t]) => `<button data-simbet="${t}" class="${t === bet ? "on" : ""}">${t}</button>`).join("");
  const [type, key, , sep] = SIM_BETS.find(([t]) => t === bet);
  const oddsAt = race.combos?.at ? `（オッズは ${esc(race.combos.at.slice(11, 16))} 時点）` : "";
  // 終わったレース: 実際の組がシミュレーションで何回出ていたか
  const act = actualCombos(race);
  let actLine = "";
  if (act) {
    const [k] = [...act[type]];
    const cnt = key === "wide" ? null : c[key].get(k) || 0;
    if (cnt != null) {
      const parts = k.match(/.{2}/g).map(Number).join(sep);
      const rank = [...c[key].values()].filter((v) => v > cnt).length + 1;
      actLine = `<p class="rmeta">実際の${type} <b>${parts}</b> は ${c.n.toLocaleString()}回中 ${cnt.toLocaleString()}回
        （${pct(cnt / c.n, 2)}・多い順で${rank}番目）</p>`;
    }
  }
  return `<p class="rmeta">${c.n.toLocaleString()}回のうち、ペースは ${paceTxt}</p>
    <div class="scroll"><table>
      <thead><tr><th class="num">馬番</th><th>馬名</th><th class="mid">脚質</th><th>1着</th>
        <th class="num">2着以内</th><th class="num">3着以内</th><th class="num">AI勝率</th>
        ${race.winner_no != null ? '<th class="mid">着順</th>' : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    <h3>よく出た組み合わせ ${oddsAt}</h3>
    <div class="subtabs small">${tabs}</div>
    ${actLine}
    ${simComboTable(race, c, type, key, sep, nameOf)}
    <p class="note">適正オッズ＝1÷出た割合（控除前）。${race.combos ? "期待値＝出た割合×現在のオッズで、1.00を超えると理屈の上ではプラスです。ただしシミュレーションはAIの勝率が正しい前提なので、期待値は高めに出やすい点に注意してください。" : "全券種のオッズは発走90分前から入ります。"}</p>`;
}

function simTrackHtml(S) {
  return S.es.map((e, i) => `<div class="lane"><span class="chip" id="chip${i}"
      style="background:${WAKU_BG[e.waku] || "#888"};color:${WAKU_FG[e.waku] || "#fff"}"
      title="${esc(e.horse_name)}">${e.horse_no}</span></div>`).join("");
}

/** 代表の1回を走らせる (序盤は脚質どおりの位置、直線で着順どおりに並び替わる) */
function simAnimate(S, box, onDone) {
  const { pace, order } = simOnce(S, true);
  const H = S.es.length;
  const STYLE_POS = { "逃げ": 0.05, "先行": 0.25, "差し": 0.55, "追込": 0.85 };
  const early = S.es.map((e) => (STYLE_POS[e.style] ?? 0.5) + Math.random() * 0.15);
  const earlyRank = [...early.keys()].sort((a, b) => early[a] - early[b]);
  const er = new Array(H), fr = new Array(H);
  earlyRank.forEach((h, r) => { er[h] = r; });
  order.forEach((h, r) => { fr[h] = r; });
  const W = box.querySelector(".track").clientWidth - 30;
  const label = box.querySelector(".simlabel");
  const T = 6500;
  const t0 = performance.now();
  const chips = S.es.map((_, i) => box.querySelector(`#chip${i}`));
  const frame = (now) => {
    const s = Math.min(1, (now - t0) / T);
    // 道中は隊列を保ち、最後の4割で着順へ
    const mix = s < 0.6 ? 0.08 * (s / 0.6) : 0.08 + 0.92 * ((s - 0.6) / 0.4) ** 1.3;
    const spread = 0.01 + 0.02 * s;
    for (let i = 0; i < H; i++) {
      const gap = ((1 - mix) * er[i] + mix * fr[i]) * spread;
      const x = Math.max(0, s - gap) * W;
      chips[i].style.transform = `translateX(${x}px)`;
    }
    const remain = Math.round((1 - s) * 10) * 200;
    label.textContent = s < 1 ? `ペース ${PACES[pace]} ／ ${remain > 0 ? `残り約${remain}m相当` : "ゴール"}` : `ペース ${PACES[pace]} ／ ゴール`;
    if (s < 1) requestAnimationFrame(frame);
    else onDone(order, pace);
  };
  requestAnimationFrame(frame);
}

function simView(race) {
  if (!INDEX?.sim_params) return '<p class="note">シミュレーションの物差しがまだありません。</p>';
  const ck = INDEX.sim_params.check;
  return `<div class="simbox">
    <div class="simctl">
      <label>ペース <select id="simpace">
        <option value="auto">おまかせ（想定ペースから引く）</option>
        ${PACES.map((p) => `<option value="${p}">${p}に決める</option>`).join("")}
      </select></label>
      <button id="simgo" class="btn">1万回走らせる</button>
      <button id="simone" class="btn sub">もう1回見る</button>
      <span id="simprog" class="rmeta"></span>
    </div>
    <div class="simlabel rmeta"></div>
    <div class="track"></div>
    <div id="simfinish" class="rmeta"></div>
    <div id="simres"></div>
    <details class="help"><summary>シミュレーションのしくみと実績</summary>
      <p>1回ごとに、まずペース（ハイ・ミドル・スロー）を想定ペースの過去の当たり方から引き、
      1着をAIの勝率に比例して選びます。2着・3着は勝率を ${INDEX.sim_params.lam2}乗・${INDEX.sim_params.lam3}乗してから選びます
      （そのまま比例させると人気馬が2・3着にも来すぎるため。過去レースの2・3着から決めた値です）。これを1万回くり返して数えます。</p>
      <p>「ペースを決める」と、実際にそのペースになったレースで測った脚質の向き不向き
      （スローなら逃げ・先行が上積み、ハイなら差し・追込が上積み）を強さに掛けます。
      ${(INDEX.sim_params.k ?? 0) > 0
        ? "「おまかせ」でも想定ペースから引いたペースに応じて掛けていますが、想定ペースの当たりが弱いので、答え合わせでの差はごくわずかです。"
        : "「おまかせ」では想定ペースの当たりが弱く、向き不向きを掛けても答え合わせが良くならなかったので掛けていません。"}</p>
      ${ck ? `<p><b>答え合わせ</b>（${esc(String(ck.races))}レース・2026/5/19以降、発走40分前のオッズでの予想）：シミュレーションで一番多く出た三連複の組は、
        出た割合の平均 ${pct(ck.trio_top.p, 1)} に対して実際の的中 ${pct(ck.trio_top.hit, 1)}、
        馬連は ${pct(ck.quinella_top.p, 1)} に対して ${pct(ck.quinella_top.hit, 1)} でした。
        3着以内の確率の当てはまり（小さいほど良い）は シミュレーション ${ck.logloss_top3.sim} ／
        補正なし ${ck.logloss_top3.harville} ／ AIの複勝率 ${ck.logloss_top3.ai}。</p>` : ""}
      <p class="note">走る様子の絵は「1回分」の例で、位置は脚質からの目安です（実際の位置取りの予測ではありません）。</p>
    </details>
  </div>`;
}

function simMount(race) {
  const box = document.querySelector(".simbox");
  if (!box) return;
  let S = null, bet = "三連複", busy = false, last = null;
  const track = box.querySelector(".track");
  const prog = box.querySelector("#simprog");
  const res = box.querySelector("#simres");
  const fin = box.querySelector("#simfinish");
  const setup = () => {
    S = simSetup(race, box.querySelector("#simpace").value);
    if (!S) { res.innerHTML = '<p class="note">AIの勝率が無いため走らせられません。</p>'; return false; }
    track.innerHTML = simTrackHtml(S);
    return true;
  };
  const showFinish = (order, pace) => {
    fin.innerHTML = "この1回の着順: " + order.slice(0, 5).map((i, r) =>
      `${r + 1}着 <b>${S.es[i].horse_no}</b> ${esc(S.es[i].horse_name)}`).join(" ／ ");
  };
  const renderRes = () => { if (last) res.innerHTML = simResults(race, S, last, bet); bind(); };
  const bind = () => res.querySelectorAll("[data-simbet]").forEach((b) =>
    b.addEventListener("click", () => { bet = b.dataset.simbet; renderRes(); }));
  const run = () => {
    if (busy || !setup()) return;
    busy = true;
    fin.textContent = "";
    const counter = simCount(S, SIM_N);
    simAnimate(S, box, (order, pace) => { showFinish(order, pace); });
    const tick = () => {
      const done = counter.step(250);
      prog.textContent = `${counter.c.n.toLocaleString()} / ${SIM_N.toLocaleString()}回`;
      last = counter.c;
      if (counter.c.n % 1000 === 0 || done) renderRes();
      if (!done) requestAnimationFrame(tick);
      else busy = false;
    };
    requestAnimationFrame(tick);
  };
  box.querySelector("#simgo").addEventListener("click", run);
  box.querySelector("#simone").addEventListener("click", () => {
    if (busy) return;
    if (!S) setup();
    fin.textContent = "";
    simAnimate(S, box, showFinish);
  });
  box.querySelector("#simpace").addEventListener("change", run);
  run();
}



/* ---------- 成績ページ (月別・年別。検証期間と学習期間を分ける) ---------- */

let STATS = null;
async function loadStats() {
  if (!STATS) STATS = await getJSON("data/stats.json").catch(() => null);
  return STATS;
}

const GROUP_HELP = {
  "本命": "AIの勝率1位の馬（全レース）",
  "堅軸": "本命が抜けている推奨レースの本命",
  "本命★": "本命の複勝期待値が1.00以上のとき",
  "1番人気": "発走40分前の1番人気（比べる相手。AIの本命と同じ時点）",
};

function validBadge(share) {
  if (share == null) return "";
  if (share >= 0.99) return '<span class="badge rec small">検証</span>';
  if (share <= 0.01) return '<span class="badge warn small">学習</span>';
  return '<span class="badge fx small">一部検証</span>';
}

function statCells(v, base) {
  if (!v) return '<td class="num" colspan="5">—</td>';
  const diff = base && v !== base ? v.top3 - base.top3 : null;
  const roiCls = (x) => (x >= 1 ? "plus" : "");
  return `<td class="num">${v.n.toLocaleString()}</td>
    <td class="num">${pct(v.win, 1)}</td>
    <td class="num">${pct(v.top3, 1)}${diff != null ? ` <small class="${diff >= 0 ? "plus" : "minus"}">${diff >= 0 ? "+" : ""}${(diff * 100).toFixed(1)}</small>` : ""}</td>
    <td class="num ${roiCls(v.tan)}">${pct(v.tan)}</td>
    <td class="num ${roiCls(v.fuku)}">${pct(v.fuku)}</td>`;
}

async function viewStats(group = "本命") {
  const st = await loadStats();
  if (!st) throw new Error("成績のデータがまだありません。");
  if (!st.groups.includes(group)) group = st.groups[0];
  const head = `<tr><th>期間</th><th></th><th class="num">件数</th><th class="num">1着</th>
    <th class="num">3着以内 <small>(1番人気との差)</small></th><th class="num">単勝回収</th><th class="num">複勝回収</th></tr>`;
  const rows = (list) => list.map((r) => `<tr>
      <td class="nw">${esc(r.period)}</td><td>${validBadge(r.valid_share)}</td>
      ${statCells(r[group], group === "1番人気" ? null : r["1番人気"])}</tr>`).join("");
  const sum = st.periods.map((r) => `<tr><td class="nw"><b>${esc(r.period)}</b></td><td></td>
      ${statCells(r[group], group === "1番人気" ? null : r["1番人気"])}</tr>`).join("");
  const valid = st.periods.find((r) => r.period === "検証期間") || {};
  const cmp = st.groups.filter((g) => valid[g]).map((g) => `<tr><td class="nw">${esc(g)}</td>
      <td class="small">${esc(GROUP_HELP[g] || "")}</td>${statCells(valid[g], null)}</tr>`).join("");
  main.innerHTML = `
    <h1 class="page">成績</h1>
    <p class="note">AIの予想を、出走前に出していたとおりに答え合わせしたものです（オッズは発走40分前の時点で予想）。
      回収率は100円ずつ買った場合で、単勝は確定オッズ・複勝は実際の払戻です。
      <b>学習期間</b>はAIがその答えを見て学んだ期間なので実力より良く出ます。<b>検証期間</b>（学習に使っていない期間）の数字を見てください。</p>
    <h2 class="section">検証期間のまとめ</h2>
    <div class="scroll"><table>
      <thead><tr><th>買い方</th><th>中身</th><th class="num">件数</th><th class="num">1着</th>
        <th class="num">3着以内</th><th class="num">単勝回収</th><th class="num">複勝回収</th></tr></thead>
      <tbody>${cmp}</tbody></table></div>
    <p class="note">AIの本命と40分前の1番人気は、当たる割合がほぼ同じです（オッズの情報を使っているため）。
      差が出るのは「堅軸」（本命が抜けているレースに絞る）と「本命★」（期待値で絞る）で、件数が少ないものは偶然の幅も大きい点に注意してください。</p>
    <div class="subtabs">${st.groups.map((g) => `<button data-g="${esc(g)}" class="${g === group ? "on" : ""}">${esc(g)}</button>`).join("")}</div>
    <p class="rmeta">${esc(GROUP_HELP[group] || "")}</p>
    <div class="scroll"><table><thead>${head}</thead><tbody>${sum}</tbody></table></div>
    ${valueStats()}
    <h2 class="section">年別</h2>
    <div class="scroll"><table><thead>${head}</thead><tbody>${rows(st.years)}</tbody></table></div>
    <h2 class="section">月別（直近24か月）</h2>
    <div class="scroll"><table><thead>${head}</thead><tbody>${rows(st.months.slice(0, 24))}</tbody></table></div>`;
  main.querySelectorAll("[data-g]").forEach((b) =>
    b.addEventListener("click", () => { location.hash = `#/seiseki/${encodeURIComponent(b.dataset.g)}`; }));
}

/** トップに出す「先週の成績」 */
function recentBox() {
  const r = STATS?.recent;
  if (!r || !r["本命"]) return "";
  const line = (g) => r[g] ? `<tr><td class="nw">${esc(g)}</td><td class="num">${r[g].n}</td>
    <td class="num">${pct(r[g].win)}</td><td class="num">${pct(r[g].top3)}</td>
    <td class="num">${pct(r[g].tan)}</td><td class="num">${pct(r[g].fuku)}</td></tr>` : "";
  return `<div class="recentbox">
    <h3>直近の成績 <small>${r.dates.map(fmtDate).join("・")}</small></h3>
    <div class="scroll"><table><thead><tr><th></th><th class="num">件数</th><th class="num">1着</th>
      <th class="num">3着以内</th><th class="num">単勝回収</th><th class="num">複勝回収</th></tr></thead>
      <tbody>${["本命", "堅軸", "1番人気"].map(line).join("")}</tbody></table></div>
    <p class="note"><a href="#/seiseki/">月別・年別の成績 ›</a></p></div>`;
}

/* ---------- 近走 (馬柱) ---------- */

const PAST_CACHE = new Map();
async function loadPast(date) {
  if (!PAST_CACHE.has(date)) PAST_CACHE.set(date, getJSON(`data/past/${date}.json`).catch(() => null));
  return PAST_CACHE.get(date);
}

function runCell(run) {
  if (!run) return '<td class="run empty"></td>';
  const [d, place, name, course, fs, fin, pop, ag, agr, margin, c1] = run;
  const cls = fin === 1 ? "r1" : fin != null && fin <= 3 ? "r3" : fin != null && fin <= 5 ? "r5" : "";
  const md = d ? `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}` : "";
  const tip = [d, place, name, course, fs && `${fs}頭`, c1 && `1角${c1}番手`, ag && `上がり${ag}${agr ? `(${agr}位)` : ""}`,
    margin != null && `勝ち馬と${margin}秒`].filter(Boolean).join(" ");
  return `<td class="run ${cls}" title="${esc(tip)}">
    <div class="rd">${md} ${esc(place || "")} ${esc(course || "")}</div>
    <div class="rf"><b>${fin ?? "-"}</b><small>/${fs ?? ""}着</small> <small>${pop ? pop + "人" : ""}</small></div>
    <div class="rx">${agr ? `上${agr}位` : ""}${margin != null && fin !== 1 ? ` ${margin.toFixed(1)}差` : ""}</div></td>`;
}

async function pastView(race, date, mode) {
  const past = (await loadPast(date))?.[race.race_id];
  if (!past) return `<p class="note">近走は、これからの開催と直近の開催日だけ載せています（データの量を抑えるため）。</p>`;
  const es = sortEntries(race, mode);
  const rows = es.map((e) => {
    const runs = past[e.horse_no] || [];
    return `<tr><td class="num">${race.stage === "登録" ? "" : e.horse_no}</td>
      <td class="nw">${favStar(e.horse_name)} ${markSpan(e)} ${esc(e.horse_name)}
        <div class="ped">${esc(e.sex || "")}${e.age ?? ""} ${esc(e.jockey_name || "")}</div></td>
      ${[0, 1, 2, 3, 4].map((i) => runCell(runs[i])).join("")}</tr>`;
  }).join("");
  return `<div class="scroll"><table class="pasttab">
    <thead><tr><th class="num">馬番</th><th>馬名</th><th>前走</th><th>2走前</th><th>3走前</th><th>4走前</th><th>5走前</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="note">着順/頭数・人気・上がり3Fの順位・勝ち馬との差（秒）。赤＝1着、青＝2〜3着、薄い青＝4〜5着。マスに触れると詳細が出ます。</p>`;
}

/* ---------- お気に入りの馬 (このブラウザに保存) ---------- */

function loadFavs() {
  try { return new Set(JSON.parse(localStorage.getItem("favs") || "[]")); } catch { return new Set(); }
}
function saveFavs(s) {
  try { localStorage.setItem("favs", JSON.stringify([...s])); } catch {}
}
let FAVS = loadFavs();

function favStar(name) {
  if (!name) return "";
  const on = FAVS.has(name);
  return `<button class="fav ${on ? "on" : ""}" data-fav="${esc(name)}" title="${on ? "お気に入りから外す" : "お気に入りに入れる"}">${on ? "★" : "☆"}</button>`;
}

/** お気に入りボタン (どの画面でも効くよう、main への委譲で受ける) */
main.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-fav]");
  if (!b) return;
  ev.preventDefault();
  ev.stopPropagation();
  const name = b.dataset.fav;
  if (FAVS.has(name)) FAVS.delete(name); else FAVS.add(name);
  saveFavs(FAVS);
  main.querySelectorAll(`[data-fav="${CSS.escape(name)}"]`).forEach((x) => {
    const on = FAVS.has(name);
    x.classList.toggle("on", on);
    x.textContent = on ? "★" : "☆";
  });
});

/** トップに出す「お気に入りの馬の出走予定」 */
async function favBox() {
  if (!FAVS.size) return "";
  const hits = [];
  for (const d of upcomingDays().slice(0, 3)) {
    const day = await loadDay(d.date).catch(() => null);
    for (const r of day?.races || []) {
      for (const e of r.entries) {
        if (FAVS.has(e.horse_name)) hits.push({ d: d.date, r, e });
      }
    }
  }
  const items = hits.map(({ d, r, e }) => `<li><a href="${raceHref(d, r)}">${fmtDate(d)} ${raceLabel(r)} ${esc(r.race_name || "")}</a>
    ${markSpan(e)} <b>${esc(e.horse_name)}</b> <small>AI勝率${pct(e.p_win, 1)}${r.stage === "登録" ? "・登録段階" : ""}</small></li>`).join("");
  return `<div class="favbox"><h3>お気に入りの馬の出走予定 <small>${FAVS.size}頭を登録中</small></h3>
    ${hits.length ? `<ul>${items}</ul>` : '<p class="note">これからの開催に、お気に入りの馬はいません。</p>'}</div>`;
}

/** 特別登録段階は「馬番」が登録順なので出さない */
const noOf = (r, no) => (r.stage === "登録" ? "" : `${no} `);

/** 馬番から枠番 (8頭以下は馬番＝枠番。多いときは外の枠から2頭ずつ) */
function wakuOf(no, n) {
  if (!no || !n) return 0;
  if (n <= 8) return no;
  const base = Math.floor(n / 8), extra = n % 8, single = 8 - extra;
  return no <= single * base ? Math.ceil(no / base) : single + Math.ceil((no - single * base) / (base + 1));
}

/** 馬番を枠の色の四角で (特別登録の段階は馬番が無いので出さない) */
function hnoBox(r, no) {
  if (r.stage === "登録" || no == null) return "";
  const w = wakuOf(no, Math.max(r.field_size || 0, ...r.entries.map((e) => e.horse_no || 0)));
  return `<span class="hno" style="background:${WAKU_BG[w] || "var(--chip)"};color:${WAKU_FG[w] || "var(--text)"}">${no}</span>`;
}


/* ---------- 直前の買い候補 (発走8分前の判定。keiba/value.py) ---------- */

function valueResult(race, pk) {
  const e = race.entries.find((x) => x.horse_no === pk.no);
  if (e?.finish == null) return "";
  return e.finish === 1
    ? `<span class="badge rec">的中 ${e.odds ? yen(Math.round(e.odds * 100)) + "円" : ""}</span>`
    : `<span class="badge fx">${e.finish}着</span>`;
}

/** レースページ: このレースの判定結果 */
function valueNote(race) {
  const v = race.value;
  if (!v) return "";
  if (!v.picks.length) {
    return `<p class="note">直前の判定（${esc((v.at || "").slice(11, 16))}）：このレースに買い候補はありませんでした。</p>`;
  }
  return `<div class="valuebox"><b>直前の買い候補（単勝）</b>
    <small>発走8分前の判定 ${esc((v.at || "").slice(11, 16))}</small>
    <ul>${v.picks.map((pk) => `<li><b>${pk.no} ${esc(pk.name)}</b>
      判定時のオッズ ${num(pk.o5)}倍 ／ 勝つ確率 ${pct(pk.p, 1)} ／ 期待値 ${pk.ev.toFixed(2)}
      ${pk.why ? `<small>（${esc(pk.why)}）</small>` : ""} ${valueResult(race, pk)}</li>`).join("")}</ul>
    <p class="note">市場の確率（オッズ）に、直前のオッズの動き・当日の馬場・近走などで市場がずれやすい点を足して出した確率です。
      期待値＝確率×オッズが1.00以上の馬です。過去の検証では回収率100%を超えた年が多い一方、外れが続く年もあります。
      <a href="#/seiseki/">実績 ›</a></p></div>`;
}

/** トップ: その日の直前の買い候補の一覧 */
function valueDayBox(day) {
  const list = [];
  let checked = 0;
  for (const r of day.races) {
    if (!r.value) continue;
    checked++;
    for (const pk of r.value.picks) list.push({ r, pk });
  }
  const vr = INDEX?.value;
  const wf = vr?.total;
  const head = `<h3>直前の買い候補（単勝） <small>発走8分前に判定</small></h3>`;
  if (!checked) {
    const upcoming = day.date >= todayStr() && day.races.some((r) => r.winner_no == null);
    return upcoming ? `<div class="valuebox">${head}
      <p class="note">各レースの発走8分前に、最新のオッズで判定してここに出します（土日の開催中、自動）。
      ${wf ? `過去の前向き検証では ${wf.bets.toLocaleString()}点で回収率 ${pct(wf.roi)}。` : ""}</p></div>` : "";
  }
  const items = list.map(({ r, pk }) => `<li><a href="${raceHref(day.date, r)}">${raceLabel(r)} ${esc(r.race_name || "")}</a>
    ${esc(r.post_time || "")}発走 ／ <b>${pk.no} ${esc(pk.name)}</b> ${num(pk.o5)}倍・期待値${pk.ev.toFixed(2)}
    ${valueResult(r, pk)}</li>`).join("");
  return `<div class="valuebox">${head}
    ${list.length ? `<ul>${items}</ul>` : '<p class="note">判定したレースに候補はまだありません。</p>'}
    <p class="note">判定済み ${checked}レース。オッズは発走まで動くので、買うときのオッズで期待値が1を下回ることもあります。
      ${wf ? `過去の前向き検証: ${wf.bets.toLocaleString()}点・回収率 ${pct(wf.roi)}。` : ""}<a href="#/seiseki/">実績 ›</a></p></div>`;
}

/** 成績ページ: 直前の買い候補の検証と実績 */
function valueStats() {
  const v = INDEX?.value;
  if (!v?.walk_forward) return "";
  const rows = v.walk_forward.map((r) => `<tr><td>${r.year}年</td><td class="num">${r.bets.toLocaleString()}</td>
    <td class="num">${r.hits}</td><td class="num">${r.avg_odds ?? ""}</td>
    <td class="num ${r.roi >= 1 ? "plus" : "minus"}">${pct(r.roi, 1)}</td>
    <td class="num small">±${pct(2 * (r.se || 0))}</td><td class="num muted">${pct(r.all_roi, 1)}</td></tr>`).join("");
  const lv = v.live;
  const worst = v.walk_forward.filter((r) => r.roi < 1).map((r) => `${r.year}年 ${pct(r.roi)}`).join("・");
  return `<h2 class="section">直前の買い候補（単勝）</h2>
    <p class="note">発走8分前のオッズに、市場がずれやすい点（直前のオッズの動き・当日の前残り・近走や騎手の人気の偏り）を足して勝つ確率を出し、
      期待値（確率×オッズ）が${v.ev_min?.toFixed(2) ?? "1.00"}以上・オッズ${v.odds_max ?? 30}倍以下の馬の単勝を100円ずつ買った場合です。
      <b>前向き検証</b>＝その年より前のデータだけで学んで、その年を買ったもの（後出しなし）。払戻は確定オッズです。</p>
    <div class="scroll"><table>
      <thead><tr><th>年</th><th class="num">買った点数</th><th class="num">的中</th><th class="num">平均オッズ</th>
        <th class="num">回収率</th><th class="num">偶然の幅</th><th class="num">全頭買い</th></tr></thead>
      <tbody>${rows}
        <tr><td><b>通算</b></td><td class="num"><b>${v.total.bets.toLocaleString()}</b></td><td></td><td></td>
          <td class="num ${v.total.roi >= 1 ? "plus" : "minus"}"><b>${pct(v.total.roi, 1)}</b></td><td></td><td></td></tr>
      </tbody></table></div>
    <p class="note">偶然の幅は、同じ実力でもくじ運でこれくらい上下するという目安（約95%の範囲）です。通算では100%を少し超えていますが、
      幅を考えると「確実に勝てる」とまでは言えません。${worst ? `回収率が100%を下回る年（${worst}）もあります。` : ""}サイトでの実際の判定の記録を
      ${lv?.bets ? `<b>${lv.bets}点・的中${lv.hits}・回収率${pct(lv.roi, 1)}</b>（${esc(lv.from || "")}〜）` : "これから積み上げます"}。
      お金を賭ける前に、まずは記録で確かめることをおすすめします。</p>`;
}


/* ---------- 人気どおりに決まらない (荒れやすい) レース。web/same_race.py の upset_score ---------- */

const UPSET_CLS = { "荒れやすい": "f1", "ふつう": "fx", "堅い": "star" };

function upsetBadge(u) {
  if (!u?.label || u.label === "ふつう") return "";
  return `<span class="badge ${UPSET_CLS[u.label]}">${esc(u.label)}${u.group === "重賞" ? "重賞" : "レース"}</span>`;
}

function upsetBox(u) {
  if (!u) return "";
  const tone = u.label === "荒れやすい" ? "人気どおりに決まりにくい" : u.label === "堅い" ? "人気どおりに決まりやすい" : "人気どおり度はふつう";
  return `<div class="upsetbox ${u.label === "荒れやすい" ? "hot" : u.label === "堅い" ? "cold" : ""}">
    <b>${esc(u.label)}</b>（${tone}${esc(u.group)}）
    <span class="small muted">見込み：三連単が同じ頭数の平均の <b>${u.mult.toFixed(2)}倍</b></span>
    <ul class="small">
      <li>過去${u.n}回の三連単：中央値 ${yen(u.tri_med)}円・最高 ${yen(u.tri_max)}円・10万超 ${u.tri_100k}回・100万超 ${u.tri_1m}回</li>
      <li>1番人気が3着以内：${u.fav_in}/${u.n_fav}回 ／ 10番人気以下が3着以内に来た回：${u.big_runs}回</li>
    </ul>
    <p class="note">同じレースでも年ごとのばらつきが大きいので、過去の平均（${(10 ** u.raw).toFixed(2)}倍）を
      回数に応じて全体の平均（1倍）へ寄せた控えめな見込みです（過去${u.n}回なので、過去の平均を${Math.round(u.w * 100)}%だけ反映）。
      上位2割を「荒れやすい」、下位2割を「堅い」としています。<a href="#/manken/">重賞の一覧 ›</a></p></div>`;
}

let JUSHOU;
async function loadJushou() {
  if (JUSHOU === undefined) {
    try { JUSHOU = await getJSON("data/jushou.json"); } catch { JUSHOU = null; }
  }
  return JUSHOU;
}

/** 100万馬券ページ: 人気どおりに決まらない重賞の一覧 */
function jushouSection(J) {
  if (!J?.items?.length) return "";
  const b = J.base;
  const row = (x) => `<tr>
      <td class="nw"><span class="badge ${UPSET_CLS[x.label]}">${esc(x.label)}</span></td>
      <td class="nw">${esc(x.name)} <small class="muted">${esc(x.grade.replace("GIII", "G3").replace("GII", "G2").replace("GI", "G1"))}</small></td>
      <td class="nw small">${esc(x.place)} ${esc(x.course)}m</td>
      <td class="num">${x.n}</td>
      <td class="num"><b>${x.mult.toFixed(2)}倍</b></td>
      <td class="num">${payCell(x.tri_med)}</td>
      <td class="num">${x.tri_100k}</td>
      <td class="num">${x.fav_in}/${x.n_fav}</td>
      <td class="num">${x.big_runs}</td>
      <td class="num">${payCell(x.tri_max)}</td></tr>`;
  const head = `<thead><tr><th></th><th>重賞</th><th>コース</th><th class="num">回数</th><th class="num">見込み</th>
      <th class="num">三連単の中央値</th><th class="num">10万超</th><th class="num">1番人気3着内</th>
      <th class="num">10番人気以下が3着内</th><th class="num">最高</th></tr></thead>`;
  const hot = J.items.filter((x) => x.label === "荒れやすい");
  const cold = J.items.filter((x) => x.label === "堅い").reverse();
  const up = (J.upcoming || []).map((r) => `<li><a href="${raceHref(r.date, r)}">${fmtDate(r.date)} ${raceLabel(r)} ${esc(r.race_name || "")}</a>
      <span class="badge ${UPSET_CLS[r.label]}">${esc(r.label)}</span> <small>見込み ${r.mult.toFixed(2)}倍（過去${r.n}回）</small></li>`).join("");
  const c = J.check;
  const checkRows = c ? c.groups.map((g) => `<tr><td class="nw">${esc(g.label)}</td><td class="num">${g.n}</td>
      <td class="num">${yen(g.med)}円</td><td class="num">${pct(g.r100k, 1)}</td><td class="num">${pct(g.r1m, 1)}</td></tr>`).join("") : "";
  return `
    <h2 class="section">人気どおりに決まらない重賞（荒れやすい重賞・堅い重賞）</h2>
    <p class="note">${esc(J.from.slice(0, 4))}年〜の重賞${yen(b.n)}レースで、三連単の払戻（＝1〜3着の組み合わせがどれだけ人気薄だったか）を
      頭数をそろえて比べ、重賞ごとに平均しました。重賞全体では三連単の中央値 ${yen(b.tri_med)}円・
      1番人気が3着以内 ${pct(b.fav_in)}・3着以内に10番人気以下が来たレース ${pct(b.big)}です。
      <b>見込み</b>＝そのレースの三連単が全体の平均の何倍になりやすいか（回数が少ないほど1倍へ寄せた控えめな値）。</p>
    ${up ? `<h3>これからの重賞</h3><ul class="jlist">${up}</ul>` : ""}
    <h3>荒れやすい重賞（上位2割・${hot.length}レース）</h3>
    <div class="scroll"><table class="mtable">${head}<tbody>${hot.map(row).join("")}</tbody></table></div>
    <h3>堅い重賞（下位2割・${cold.length}レース）</h3>
    <div class="scroll"><table class="mtable">${head}<tbody>${cold.map(row).join("")}</tbody></table></div>
    <details class="cond"><summary>すべての重賞（${J.items.length}レース）</summary>
      <div class="scroll"><table class="mtable">${head}<tbody>${J.items.map(row).join("")}</tbody></table></div></details>
    ${c ? `<h3>この見込みは次の年にも当たるか（前向き検証）</h3>
    <p class="note">その年より前の回だけで見込みを出して3つに分け、その年の実際の三連単を見たものです（${c.n}レース）。</p>
    <div class="scroll"><table><thead><tr><th>見込み</th><th class="num">レース</th><th class="num">三連単の中央値</th>
      <th class="num">10万超</th><th class="num">100万超</th></tr></thead><tbody>${checkRows}</tbody></table></div>
    <p class="note"><b>読み方の注意：</b>同じ重賞でも年ごとのばらつきがとても大きく、レースそのものの「荒れ癖」で説明できるのは
      ばらつきの1割弱です（${J.var?.jushou ? `レース間の差 ${(J.var.jushou.t2 / (J.var.jushou.t2 + J.var.jushou.s2) * 100).toFixed(0)}%` : ""}）。
      検証では「堅い」側ははっきり配当が低めでしたが、「荒れやすい」側が翌年も荒れるとは限りませんでした。
      その年の出走馬の顔ぶれ（レースページの「荒れ度」）と合わせて見てください。</p>` : ""}`;
}

/* ---------- 過去の同じレース: 1〜3着に何番人気が来たか ---------- */

const POP_BANDS = ["1番人気", "2-3番人気", "4-5番人気", "6-9番人気", "10番人気以下"];

function popCell(p) {
  if (p == null) return '<td class="num">-</td>';
  const cls = p === 1 ? "pp1" : p <= 3 ? "pp3" : p <= 5 ? "pp5" : p <= 9 ? "pp9" : "pp10";
  return `<td class="num"><span class="pop ${cls}">${p}</span></td>`;
}

/** レースの要約に出す1行 */
function sameLine(race) {
  const s = race.same;
  if (!s?.summary?.["1"]) return "";
  const f = (k) => {
    const v = s.summary[k];
    return v ? `${k}着は${v.min}〜${v.max}番人気` : "";
  };
  const big = s.summary.top3?.bands?.[4] || 0;
  return `<p class="why"><b>${s.mode === "name" ? "過去の同じレース" : "過去の同じ条件"}</b>
    ${upsetBadge(s.upset)}（${s.n}回）${[f("1"), f("2"), f("3")].filter(Boolean).join("・")}${big ? `・3着以内に10番人気以下が${big}回` : ""}
    <a href="${location.hash.replace(/\/(ai|shutuba|past|sim|index|review|same)$/, "")}/same">くわしく ›</a></p>`;
}

function sameView(race) {
  const s = race.same;
  if (!s) return '<p class="note">過去の同じレース（同じ条件のレース）が見つかりませんでした。</p>';
  const head = s.mode === "name"
    ? `「${esc(s.label)}」の過去${s.n}回（${esc(s.from.slice(0, 4))}年〜）`
    : `同じ条件（${esc(s.label)}）の過去${s.n}レース（${esc(s.from.slice(0, 4))}年〜）`;
  const bandRow = (k, label) => {
    const v = k === "top3" ? s.summary.top3 : s.summary[k];
    if (!v) return "";
    const tot = v.bands.reduce((a, b) => a + b, 0) || 1;
    return `<tr><td class="nw"><b>${label}</b></td>
      ${k === "top3" ? '<td class="num">—</td>' : `<td class="num">${v.min}〜${v.max}<small>（中央${v.median}）</small></td>`}
      ${v.bands.map((n) => `<td class="num">${n ? `${n}<small>（${Math.round((n / tot) * 100)}%）</small>` : "0"}</td>`).join("")}</tr>`;
  };
  const rows = s.rows.map((r) => `<tr>
      <td class="nw">${esc(r.date)}</td><td class="nw">${esc(r.place)} ${esc(r.course)}m</td>
      <td class="num">${r.fs ?? ""}</td>
      ${r.pops.map(popCell).join("")}
      <td class="nw small">${r.names.map((n) => esc(n || "")).join(" → ")}</td>
      <td class="num">${r.tri != null ? payCell(r.tri) : ""}</td></tr>`).join("");
  return `<h3>${head}</h3>
    ${upsetBox(s.upset)}
    <div class="scroll"><table>
      <thead><tr><th></th><th class="num">範囲</th>${POP_BANDS.map((b) => `<th class="num">${b}</th>`).join("")}</tr></thead>
      <tbody>${bandRow("1", "1着")}${bandRow("2", "2着")}${bandRow("3", "3着")}${bandRow("top3", "3着以内の合計")}</tbody>
    </table></div>
    <h3>直近${s.rows.length}回の1〜3着の人気</h3>
    <div class="scroll"><table>
      <thead><tr><th>日付</th><th>コース</th><th class="num">頭数</th><th class="num">1着</th><th class="num">2着</th>
        <th class="num">3着</th><th>1〜3着の馬</th><th class="num">三連単</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">${s.mode === "name"
      ? "名前で同じレースを探しています（第○回・(GI)・「ステークス/S」などの表記の違いはそろえています）。年によって競馬場や距離が変わったレースもあるので、コースの欄も見てください。"
      : "名前の無いレースは、同じ競馬場・芝ダ・距離・クラスのレースを「同じ条件」として集めています。"}
      人気は確定オッズの人気です。過去の傾向は参考で、毎年の出走馬の顔ぶれで大きく変わります。</p>`;
}

/* ---------- 回顧 (ペース・レースの質・各馬の流れ・次走へのメモ) ---------- */

const MEMO_CLASS = { "巻き返し": "rec", "見直し": "rec", "信頼": "star", "割引": "warn", "力負け": "fx" };
const memoBadge = (m) => (m ? `<span class="badge ${MEMO_CLASS[m] || "fx"}">${esc(m)}</span>` : "");
const FLOW_LABEL = { "不利": "合わなかった", "有利": "合った", "ふつう": "ふつう" };

/** 出馬表の馬名の下に出す「前走」の一行 */
function prevLine(e) {
  const p = e.rv?.prev;
  if (!p || (!p.memo && !p.cause && p.flow !== "不利")) return "";
  const bits = [`前走${p.finish}着`];
  if (p.flow === "不利") bits.push("流れ不向き");
  if (p.cause && p.finish > 3) bits.push(p.cause);
  return `<div class="ped prev">${memoBadge(p.memo)} ${esc(bits.join("・"))}</div>`;
}

const BET_ORDER = ["単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "三連複", "三連単"];
const BET_SEP = { "馬単": " → ", "三連単": " → " };

function comboText(type, c) {
  if (type === "枠連" && c.length === 2) return `${c[0]} - ${c[1]}`;
  const parts = c.match(/.{2}/g) || [c];
  return parts.map((x) => String(Number(x))).join(BET_SEP[type] || " - ");
}

function payoutTable(race) {
  const pays = race.payouts;
  if (!pays) return "";
  const rows = BET_ORDER.filter((t) => pays[t]).map((t) => {
    const list = [...pays[t]].sort((a, b) => a[1] - b[1]);
    return `<tr><td class="nw">${t}</td>
      <td class="nw">${list.map(([c]) => esc(comboText(t, c))).join("<br>")}</td>
      <td class="num">${list.map(([, v]) => payCell(v)).join("<br>")}</td></tr>`;
  }).join("");
  return `<h3>払戻金</h3><div class="scroll"><table class="paytable">
    <thead><tr><th>券種</th><th>組み合わせ</th><th class="num">払戻(100円)</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function lapTable(race) {
  const rv = race.review;
  if (!rv?.laps?.length) return "";
  const laps = rv.laps;
  const dist = race.distance || 0;
  // 最初の区間は距離が200mで割り切れないとき100mになる
  const first = dist % 200 === 0 ? 200 : dist % 200;
  let at = 0, cum = 0;
  const head = [], lap = [], pass = [];
  laps.forEach((v, i) => {
    at += i === 0 ? first : 200;
    cum += v;
    head.push(`<th class="num">${at}m</th>`);
    const cls = i === 0 ? "" : v < 11.5 ? "fastlap" : v >= 12.5 ? "slowlap" : "";
    lap.push(`<td class="num ${cls}">${v.toFixed(1)}</td>`);
    pass.push(`<td class="num">${cum.toFixed(1)}</td>`);
  });
  return `<h3>ラップタイム</h3>
    <p class="rmeta">前半3F ${num(rv.s3)} ／ 上がり3F ${num(rv.l3)}
      ／ ペース <b>${esc(rv.pace || "")}</b>${rv.quality ? ` ／ レースの質 <b>${esc(rv.quality)}</b>` : ""}</p>
    <div class="scroll"><table class="laps">
      <thead><tr><th></th>${head.join("")}</tr></thead>
      <tbody><tr><th>ラップ</th>${lap.join("")}</tr><tr><th>通過</th>${pass.join("")}</tr></tbody>
    </table></div>
    <p class="note">赤＝11.5秒未満の速いラップ、青＝12.5秒以上の遅いラップ（最初の区間は除く）。</p>`;
}

function reviewStatsNote() {
  const st = INDEX?.review_stats;
  if (!st) return "";
  const rows = ["巻き返し", "見直し", "信頼", "割引", "力負け"].filter((k) => st[k]).map((k) => {
    const v = st[k];
    const d = v.lift * 100;
    return `<tr><td>${memoBadge(k)}</td><td class="num">${v.n.toLocaleString()}</td>
      <td class="num">${pct(v.next_top3, 1)}</td><td class="num">${pct(v.expected, 1)}</td>
      <td class="num ${d >= 0 ? "plus" : "minus"}">${d >= 0 ? "+" : ""}${d.toFixed(1)}pt</td></tr>`;
  }).join("");
  return `<details class="help"><summary>次走へのメモの実績</summary>
    <p>2021年以降の中央で、メモが付いた馬の<b>次走</b>の3着内率を、次走で同じ人気だった馬の平均と比べました。</p>
    <div class="scroll"><table><thead><tr><th>メモ</th><th class="num">頭数</th>
      <th class="num">次走3着内</th><th class="num">同じ人気の平均</th><th class="num">差</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">向きは合っていますが差は小さく、ほとんどはオッズ（人気）に織り込まれています。
      買い目の根拠ではなく、見方の参考にしてください。なお、前走の上がり順位・勝ち馬との差・位置取りの乱れは
      AIの特徴量にも入っています（入れる前より確認期間の複勝AUCが 0.746→0.752 に改善）。</p></details>`;
}

function reviewView(race) {
  const rv = race.review;
  const done = race.winner_no != null || race.entries.some((e) => e.finish != null);
  if (!done) {
    return `<p class="note">回顧はレースが終わると表示されます。出馬表の馬名の下に、各馬の<b>前走の回顧</b>（次走へのメモ）を出しています。</p>
      ${reviewStatsNote()}`;
  }
  const t = rv?.text || {};
  const lines = [
    t.pace && ["ペース", t.pace],
    t.quality && ["レースの質", t.quality],
    t.settle && ["展開", t.settle],
  ].filter(Boolean);
  let check = "";
  if (race.pace && rv?.pace) {
    const ok = race.pace === rv.pace;
    check = `<p class="rmeta">答え合わせ：想定ペース ${esc(race.pace)} → 実際 ${esc(rv.pace)}
      ${ok ? '<span class="badge rec">的中</span>' : '<span class="badge fx">はずれ</span>'}</p>`;
  }
  const es = [...race.entries].sort((a, b) => (a.finish ?? 99) - (b.finish ?? 99));
  const rows = es.map((e) => {
    const r = e.rv || {};
    const run = r.pos != null ? `序盤${r.pos}番手${r.ar != null ? `・上がり${r.ar}位` : ""}` : "";
    return `<tr>
      <td class="num">${e.finish ?? "取消"}</td>
      <td class="num">${e.horse_no}</td>
      <td class="nw">${esc(e.horse_name)}</td>
      <td class="num">${e.rank}</td>
      <td class="nw">${run}</td>
      <td class="num">${r.tw != null && e.finish !== 1 ? r.tw.toFixed(1) : ""}</td>
      <td class="mid">${esc(FLOW_LABEL[r.flow] || "")}</td>
      <td class="nw">${esc(r.cause || "")}</td>
      <td class="mid">${memoBadge(r.memo)}</td>
    </tr>`;
  }).join("");
  return `<div class="reviewbox">
      ${lines.map(([k, v]) => `<p><b>${k}</b> ${esc(v)}</p>`).join("")}
      ${check}
    </div>
    <div class="scroll"><table>
      <thead><tr><th class="num">着</th><th class="num">馬番</th><th>馬名</th><th class="num">AI順</th>
        <th>走り</th><th class="num">勝ち馬との差</th><th class="mid">流れ</th><th>敗因</th><th class="mid">次走へのメモ</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <details class="help"><summary>回顧の見方</summary>
      <p><b>ペース</b>は前半3F−上がり3Fの差を同じ芝ダ×距離の過去と比べたもの（前傾＝ハイ・後傾＝スロー）。
      <b>レースの質</b>はラップの形（前半3F・中盤・上がり3F・最後の1Fの失速）を同じ競馬場×芝ダ×距離の平均と比べ、
      2020〜2024年のレースを3つの型に分けて決めています。<b>展開</b>は上位3頭の序盤の位置（頭数に対する割合）で、
      前2割5分以内なら前残り、後ろ半分なら差し・追込決着です。</p>
      <p><b>流れ</b>は決着とペースに対する位置取り（前残りで後方＝合わなかった、差し決着で前＝合わなかった など）。
      <b>次走へのメモ</b>：巻き返し（出遅れ・展開不利で上がり3位以内、または3着と0.2秒以内）／
      見直し（展開不利で勝ち馬と1秒以内）／信頼（展開不利をはね返した好走・0.3秒以上の完勝）／
      割引（展開に恵まれた好走で上がりは下位）／力負け（勝ち馬と1.5秒以上の大敗）。</p></details>
    ${reviewStatsNote()}
    ${lapTable(race)}
    ${payoutTable(race)}`;
}

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
  if (HOT.includes(race.upset_label)) {
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
  const upsets = day.races.filter((r) => HOT.includes(r.upset_label))
    .sort((a, b) => (b.p_hit_1m || 0) - (a.p_hit_1m || 0)).slice(0, 6);
  const aiBasis = day.races.some((r) => r.upset_basis === "ai");
  const line = (r, extra) => `<li><a href="${raceHref(day.date, r)}">${raceLabel(r)}
      ${esc(r.race_name || "")}</a> ${gradeBadge(r.grade)} ${extra}
      <div class="bets">${betsInline(r)}</div></li>`;
  const recList = recs.length
    ? recs.map((r) => line(r, `◎${noOf(r, r.top_horse_no)}${esc(r.top_horse_name)}
        <small>複勝率${pct(r.top_p_top3)}</small>${r.top_finish != null ? " " + finishBadge(r.top_finish) : ""}`)).join("")
    : '<li class="none">この日は条件を満たす堅軸がありません（本命が抜けたレースが無い）</li>';
  const upList = upsets.length
    ? upsets.map((r) => line(r, `<span class="badge ${hotClass(r.upset_label)}">${esc(r.upset_label)}</span>
        <small>100万超 ${pctLo(r.p_hit_1m)}・${r.stage === "登録" ? "登録" : ""}${r.field_size}頭</small>
        ${r.trifecta_pay != null ? `<small>（実際の三連単 ${yen(r.trifecta_pay)}円）</small>` : ""}`)).join("")
    : '<li class="none">大荒れが警戒されるレースはありません</li>';
  return `<div class="hl">
    <div class="hlcol"><h3>この日の堅軸 <small>${recs.length}レース</small></h3><ul>${recList}</ul></div>
    <div class="hlcol"><h3>大荒れしそうなレース</h3><ul>${upList}</ul>
      ${aiBasis ? `<p class="note">オッズ発表前のレースは、AIの勝率から荒れ度を見積もっています
        （勝率・頭数・支持の集中度を実配当に合わせて較正）。${upsetAiNote()}
        10万超の確率と想定配当はオッズが出てから出します。</p>` : ""}</div>
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
  const [m, J] = await Promise.all([loadMillion(), loadJushou()]);
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
        <tbody>${raceRows(past, true)}</tbody></table></div></details>
    ${jushouSection(J)}`;
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
/** そのレースの出走馬の顔ぶれと、それが過去どれだけ荒れたか */
function fieldLine(race) {
  if (!race.field_json) return "";
  let f;
  try { f = JSON.parse(race.field_json); } catch { return ""; }
  const c = INDEX?.upset_report?.conditions;
  const axis = (name) => c?.axes?.find((a) => a.name === name);
  const lift = (name, label) => axis(name)?.items.find((it) => it.label === label)?.lift;
  const bin = (v, edges, labels) => labels[edges.findIndex((e) => v <= e)] ?? labels[labels.length - 1];
  const parts = [];
  const add = (name, text, label) => {
    const l = lift(name, label);
    const tag = l == null ? "" : l >= 1.2 ? ` <span class="up-lift">荒れやすい${l.toFixed(1)}倍</span>`
      : l <= 0.8 ? ` <span class="dn-lift">堅め${l.toFixed(1)}倍</span>` : "";
    parts.push(`${esc(text)}${tag}`);
  };
  if (f.strong != null) add("能力指数80以上の馬", `実力上位（指数80以上）${f.strong}頭`,
    bin(f.strong, [1, 3, 5], ["0-1頭", "2-3頭", "4-5頭", "6頭以上"]));
  if (f.gap != null && c?.gap_q) {
    const lab = f.gap <= c.gap_q[0] ? "拮抗（差が小さい25%）" : f.gap > c.gap_q[1] ? "抜けた馬あり（差が大きい25%）" : "ふつう";
    add("走破タイム上位3頭の差", `走破タイム上位3頭は${lab.replace(/（.*/, "")}`, lab);
  }
  if (f.good != null) add("前走3着以内の馬", `前走3着以内 ${f.good}頭`,
    bin(f.good, [2, 4, 6], ["0-2頭", "3-4頭", "5-6頭", "7頭以上"]));
  if (f.nige != null) add("逃げ馬", `逃げ馬 ${f.nige}頭`, bin(f.nige, [0, 1, 2], ["0頭", "1頭", "2頭", "3頭以上"]));
  return parts.length ? `<p class="why"><b>顔ぶれ</b> ${parts.join(" ／ ")}</p>` : "";
}

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
      <td class="num nw">${num(liveOdds(race, e))}${oddsArrow(race, e)}</td>
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
      ${fieldLine(race)}
      ${sameLine(race)}
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
        ${babaLine(race)}
      </div>
    </div>
    <div class="rlinks between"><span>${others}</span>
      <span><a href="#/yoso/${date}">この日の予想一覧</a>
        <a href="#/kekka/${date}">この日の結果</a></span></div>
    ${sampleNote(race.in_sample)}
    ${race.stage === "登録" ? `<p class="note"><span class="badge warn">特別登録段階</span>
      登録馬（${race.field_size}頭）全体での予想です。除外・回避で出走しない馬も含み、
      枠・馬番・騎手は未確定です。出馬表が出ると自動で更新されます。</p>` : ""}
    ${liveNote(race)}
    ${valueNote(race)}
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
        : sub === "index" ? indexTable(race, mode)
        : sub === "review" ? reviewView(race)
        : sub === "sim" ? simView(race)
        : sub === "same" ? sameView(race)
        : sub === "past" ? "" : shutubaTable(race, mode);
    if (sub === "sim") simMount(race);
    if (sub === "past") {
      const body = document.getElementById("subbody");
      body.innerHTML = '<div class="msg">近走を読み込み中...</div>';
      pastView(race, date, mode).then((h) => { body.innerHTML = h; });
    }
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
      <td class="nw">${favStar(e.horse_name)} ${esc(e.horse_name)}
        ${e.sire ? `<div class="ped">父 ${esc(e.sire)}${
          e.dam_sire ? ` ／ 母父 ${esc(e.dam_sire)}` : ""}</div>` : ""}${prevLine(e)}</td>
      <td class="mid">${esc(e.sex || "")}${e.age ?? ""}</td>
      <td class="num">${num(e.kinryo)}</td>
      <td class="nw">${esc(e.jockey_name || "")}
        ${e.trainer_name ? `<div class="ped">厩舎 ${esc(e.trainer_name)}</div>` : ""}</td>
      <td class="num nw">${num(liveOdds(race, e))}${oddsArrow(race, e)}</td>
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
    <p class="note">印＝AIの勝率順 ◎○▲△。AI勝率はレース内で合計100%になるよう正規化しています。
      馬名の下の「前走」は前走の回顧（次走へのメモ・敗因）です。見方は「回顧」タブにあります。</p>
    ${oddsMoveTable(race)}
    ${race.payouts ? payoutTable(race) : ""}`;
}

/** 終わったレースの予想は「発走40分前のオッズ」で出したもの */
function oddsBasisNote(race) {
  if (race.winner_no == null || !race.odds_mv?.pts?.some((p) => p.t === "40分前")) return "";
  return `<p class="note">このレースのAI予想は、実際に予想を出せる<b>発走40分前のオッズ</b>で計算しています
    （確定オッズで答え合わせをすると実際より良く見えるため）。表の単勝は確定オッズです。</p>`;
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
      複勝期待値は 複勝率×想定複勝オッズ で、1.00 が損益分岐です。</p>
    ${oddsBasisNote(race)}`;
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
    if (page === "seiseki") return await viewStats(a || "本命");
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
    // 成績ページは日付を持たない
    const href = t.id === "top" ? "#/" : t.id === "seiseki" ? "#/seiseki/" : `#/${t.id}/${d}`;
    return `<a href="${href}" class="${t.id === active ? "active" : ""}">${t.label}</a>`;
  }).join("");
  // スマホでタブが横にずれているとき、開いているタブを見える位置へ
  document.querySelector("#tabs a.active")?.scrollIntoView({ inline: "nearest", block: "nearest" });
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

/* ---------- 更新ボタン: サイトから PC に取り込み・更新を頼む (functions/api/update, scripts/update_poller.py) ---------- */

const UPD_API = "/api/update";
let UPD_TIMER = null;

function updKey() {
  try { return localStorage.getItem("updKey") || ""; } catch { return ""; }
}
function setUpdKey(v) {
  try { v ? localStorage.setItem("updKey", v) : localStorage.removeItem("updKey"); } catch { /* 保存できなくても続ける */ }
}
const hm = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const agoMin = (iso) => (iso ? Math.round((Date.now() - new Date(iso)) / 60000) : null);

function updStatusHtml(st) {
  if (!st) return '<p class="note">状態を読み込めませんでした（サイトの公開版でだけ使えます）。</p>';
  const mode = st.mode === "full" ? "全部" : "出馬表だけ";
  const seen = agoMin(st.pc_seen);
  const pc = seen == null ? '<span class="minus">PCの受付が一度も動いていません</span>'
    : seen > 15 ? `<span class="minus">PCの受付から${seen}分連絡がありません（PCの電源・「更新ボタンの受付」を確認）</span>`
      : `PCの受付：${seen}分前に確認`;
  let now = "";
  if (st.status === "pending") now = `<b>依頼済み</b>（${hm(st.requested_at)}・${mode}）PCが受け取るのを待っています…`;
  else if (st.status === "running") now = `<b>更新中</b>（${hm(st.started_at)}に開始・${mode}）。出馬表だけなら3分ほどです…`;
  else if (st.status === "done") now = `前回：<span class="plus">完了</span> ${hm(st.finished_at)}（${mode}）`;
  else if (st.status === "failed") now = `前回：<span class="minus">失敗</span> ${hm(st.finished_at)}（${mode}）`;
  else now = "まだ更新の依頼はありません";
  return `<p>${now}</p>${st.message && (st.status === "done" || st.status === "failed")
    ? `<p class="small muted">${esc(st.message)}</p>` : ""}<p class="small">${pc}</p>`;
}

async function updFetchStatus() {
  try {
    const r = await fetch(UPD_API, { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

async function updRender(msg = "") {
  const box = document.getElementById("updpanel");
  if (!box) return;
  const st = await updFetchStatus();
  const busy = st && (st.status === "pending" || st.status === "running");
  const key = updKey();
  box.innerHTML = `
    <div class="updhead"><b>サイトを更新</b><button type="button" class="updx" aria-label="閉じる">×</button></div>
    ${updStatusHtml(st)}
    ${msg ? `<p class="updmsg">${msg}</p>` : ""}
    ${key ? "" : `<label class="small">更新キー（PCの data フォルダの update_token.txt）
      <input type="password" id="updkey" autocomplete="off" spellcheck="false"></label>`}
    <div class="updbtns">
      <button type="button" data-mode="quick" ${busy ? "disabled" : ""}>出馬表だけ（約3分）</button>
      <button type="button" data-mode="full" ${busy ? "disabled" : ""}>全部（30分以上）</button>
    </div>
    ${st?.status === "done" ? '<button type="button" class="updreload">新しい表示に切り替える</button>' : ""}
    <p class="note">JRA-VANから出馬表・馬体重・オッズなどを取り込み、予想を作り直して公開します。PCで
      「更新ボタンの受付」が動いている必要があります。出馬表の発表は木曜16時すぎ（馬番なし）・前日10時すぎ（馬番あり）です。
      ${key ? '<a href="#" class="updforget">この端末の更新キーを消す</a>' : ""}</p>`;
  box.querySelector(".updx").onclick = toggleUpdatePanel;
  box.querySelector(".updreload")?.addEventListener("click", () => location.reload());
  box.querySelector(".updforget")?.addEventListener("click", (e) => { e.preventDefault(); setUpdKey(""); updRender(); });
  box.querySelectorAll(".updbtns button").forEach((b) => b.addEventListener("click", () => updRequest(b.dataset.mode)));
  clearTimeout(UPD_TIMER);
  if (busy) UPD_TIMER = setTimeout(() => updRender(), 10000);
}

async function updRequest(mode) {
  const input = document.getElementById("updkey");
  const key = input ? input.value.trim() : updKey();
  if (!key) return updRender("更新キーを入れてください。");
  let r, body = {};
  try {
    r = await fetch(UPD_API, { method: "POST", cache: "no-store",
      headers: { "content-type": "application/json", authorization: `Bearer ${key}` },
      body: JSON.stringify({ mode }) });
    body = await r.json().catch(() => ({}));
  } catch {
    return updRender("送れませんでした。通信を確認してください。");
  }
  if (r.status === 401) { setUpdKey(""); return updRender("更新キーが違います。"); }
  if (input) setUpdKey(key);
  updRender(r.ok ? "依頼しました。PCが受け取ると「更新中」に変わります。" : esc(body.error || `エラー ${r.status}`));
}

function toggleUpdatePanel() {
  let box = document.getElementById("updpanel");
  const btn = document.getElementById("updbtn");
  if (box) {
    box.remove();
    clearTimeout(UPD_TIMER);
    btn?.setAttribute("aria-expanded", "false");
    return;
  }
  box = document.createElement("div");
  box.id = "updpanel";
  box.className = "updpanel";
  document.body.appendChild(box);
  btn?.setAttribute("aria-expanded", "true");
  box.innerHTML = '<p class="note">読み込み中…</p>';
  updRender();
}

/* ---------- 明るい表示 / 暗い表示 (既定は暗い表示。index.html の先頭で先に当てている) ---------- */
function setupThemeButton() {
  const btn = document.getElementById("themebtn");
  if (!btn) return;
  const paint = () => {
    const dark = document.documentElement.dataset.theme === "dark";
    btn.textContent = dark ? "☀ 明るく" : "☾ 暗く";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#12161c" : "#1b365d");
  };
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch {}
    paint();
  });
  paint();
}

async function start() {
  setupViewToggle();
  try {
    await loadIndex();
    await loadMillion();
    const g = INDEX?.generated;
    if (g) {
      document.querySelector(".titlebar").innerHTML =
        `競馬予想 <small class="upd">データ更新 ${Number(g.slice(5, 7))}/${Number(g.slice(8, 10))} ${g.slice(11, 16)}</small>
        <button type="button" class="updbtn" id="updbtn" aria-expanded="false">↻ 更新</button>
        <button type="button" class="themebtn" id="themebtn"></button>`;
      setupThemeButton();
      document.getElementById("updbtn").addEventListener("click", toggleUpdatePanel);
    }
  } catch (e) {
    main.innerHTML = `<div class="msg">${esc(e.message)}</div>`;
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}

start();
