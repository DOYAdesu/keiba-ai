// サイトの「更新」ボタンの受付 (Cloudflare Pages Functions)。
//
//   GET  /api/update        状態 (だれでも見られる。中身は時刻と結果だけ)
//   POST /api/update        更新の依頼 (要: 更新キー)          body {mode: "quick"|"full"}
//   GET  /api/update/poll   PC が依頼を見に来る (要: 更新キー。KV を読むだけ)
//   POST /api/update/claim  PC が依頼を引き受ける (要: 更新キー)
//   POST /api/update/done   PC が結果を返す (要: 更新キー)     body {ok, message}
//   POST /api/update/beat   PC が動いている印 (要: 更新キー。10分ごと)
//
// 更新キーは Pages のシークレット UPDATE_TOKEN。PC 側は data/update_token.txt に同じものを持つ。

const KEY = "state";
const BEAT = "beat";
const MIN_GAP_MS = 3 * 60 * 1000;      // 依頼どうしの最短の間隔
const STALE_MS = 2 * 60 * 60 * 1000;   // これより古い「実行中」は止まったとみなす

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

function authed(request, env) {
  const want = (env.UPDATE_TOKEN || "").trim();
  const got = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!want || got.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= want.charCodeAt(i) ^ got.charCodeAt(i);
  return diff === 0;
}

async function load(env) {
  const [s, b] = await Promise.all([env.UPD.get(KEY, "json"), env.UPD.get(BEAT)]);
  return { state: s || { status: "idle" }, beat: b };
}

export async function onRequest(context) {
  const { request, env, params } = context;
  const sub = (params.path || []).join("/");
  const now = new Date();
  const iso = now.toISOString();

  if (request.method === "GET" && sub === "") {
    const { state, beat } = await load(env);
    return json({ ...state, pc_seen: beat, now: iso });
  }
  if (!authed(request, env)) return json({ error: "更新キーが違います" }, 401);

  if (request.method === "POST" && sub === "") {
    let body = {};
    try { body = await request.json(); } catch { /* 空でもよい */ }
    const mode = body.mode === "full" ? "full" : "quick";
    const { state } = await load(env);
    const busy = (state.status === "pending" || state.status === "running")
      && now - new Date(state.requested_at || 0) < STALE_MS;
    if (busy) return json({ error: "すでに更新の依頼が入っています", ...state }, 409);
    if (state.requested_at && now - new Date(state.requested_at) < MIN_GAP_MS) {
      return json({ error: "少し前に更新したばかりです。3分ほど空けてください", ...state }, 429);
    }
    const next = { status: "pending", mode, requested_at: iso };
    await env.UPD.put(KEY, JSON.stringify(next));
    return json(next);
  }
  if (request.method === "GET" && sub === "poll") {
    const { state } = await load(env);
    return json(state);
  }
  if (request.method === "POST" && sub === "claim") {
    const { state } = await load(env);
    if (state.status !== "pending") return json({ job: null, ...state });
    const next = { ...state, status: "running", started_at: iso };
    await env.UPD.put(KEY, JSON.stringify(next));
    await env.UPD.put(BEAT, iso);
    return json({ job: next });
  }
  if (request.method === "POST" && sub === "done") {
    let body = {};
    try { body = await request.json(); } catch { /* 空でもよい */ }
    const { state } = await load(env);
    const next = { ...state, status: body.ok ? "done" : "failed", finished_at: iso,
                   message: String(body.message || "").slice(0, 500) };
    await env.UPD.put(KEY, JSON.stringify(next));
    await env.UPD.put(BEAT, iso);
    return json(next);
  }
  if (request.method === "POST" && sub === "beat") {
    await env.UPD.put(BEAT, iso);
    return json({ ok: true });
  }
  return json({ error: "not found" }, 404);
}
