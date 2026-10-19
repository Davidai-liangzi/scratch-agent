/**
 * ScratchGenerator 反馈中转 · Cloudflare Worker
 * ================================================
 * 作用：客户端只认这一层地址，真正的飞书群机器人地址**只存在服务端**（环境变量里），
 * 永远不落到客户端，也不进公开仓库；同时在这里做限流，所以这一层地址即使被人逆出来
 * 也刷不爆你的群 —— 泄露后仍然可控，这是它比「关键词」强的地方。
 *
 * 部署后要在 Cloudflare 后台配两样（见 v 1.1.2版本更新/信息反馈计划书.md §3.7）：
 *   1. 变量    FEISHU_HOOK = https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
 *   2. KV 绑定 RATE        = 一个 KV 命名空间（限流计数用）
 *
 * 客户端约定（core/feedback.py 的 `_payload()` 对非 IM 域名就是发这个形状）：
 *   请求  {"text": "<整段消息>", "context": {...}}
 *   成功  HTTP 200 + {"code": 0}
 *   失败  HTTP 非 200（客户端会转而把反馈存本地文件，不会丢）
 */

const IP_LIMIT = 3;             // 同一 IP 每分钟最多几条
const IP_WINDOW = 60;           // 秒
const GLOBAL_LIMIT = 200;       // 全体用户每天合计上限，保证被刷也不会刷爆群
const GLOBAL_WINDOW = 86400;    // 秒
const MAX_BODY_BYTES = 18000;   // 与客户端一致；飞书那边硬限 20 KB
const MAX_TEXT_CHARS = 4500;    // 正文再兜一道

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors() },
  });
}

function cors() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

/**
 * 计数并判断是否超限。
 * KV 是最终一致的，并发下计数可能偏小 —— 对「防刷」够用，不必上 Durable Objects。
 * 没绑 RATE 时不报错、直接放行，并在响应里如实说明（见 GET 健康检查）。
 */
async function hit(env, key, limit, ttl) {
  const kv = env.RATE;
  if (!kv) return { ok: true, counted: false };
  const n = parseInt((await kv.get(key)) || "0", 10) || 0;
  if (n >= limit) return { ok: false, counted: true, n };
  await kv.put(key, String(n + 1), { expirationTtl: ttl });
  return { ok: true, counted: true, n: n + 1 };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors() });

    // 健康检查：浏览器打开 Worker 地址就能看配置齐不齐
    if (request.method !== "POST") {
      return json({
        code: 0,
        service: "scratchgenerator-feedback",
        hook_configured: Boolean(env.FEISHU_HOOK),
        rate_limit: Boolean(env.RATE),
      });
    }

    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) {
      return json({ code: 2, msg: "payload too large" }, 413);
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return json({ code: 3, msg: "bad json" }, 400);
    }

    const text = typeof data?.text === "string" ? data.text.trim() : "";
    if (!text) return json({ code: 4, msg: "empty text" }, 400);
    if (text.length > MAX_TEXT_CHARS) return json({ code: 5, msg: "text too long" }, 413);

    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const ipHit = await hit(env, `ip:${ip}`, IP_LIMIT, IP_WINDOW);
    if (!ipHit.ok) return json({ code: 6, msg: "rate limited by ip" }, 429);

    const day = new Date().toISOString().slice(0, 10);
    const allHit = await hit(env, `all:${day}`, GLOBAL_LIMIT, GLOBAL_WINDOW);
    if (!allHit.ok) return json({ code: 7, msg: "rate limited globally" }, 429);

    if (!env.FEISHU_HOOK) return json({ code: 8, msg: "FEISHU_HOOK not set" }, 500);

    let upstream;
    try {
      upstream = await fetch(env.FEISHU_HOOK, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ msg_type: "text", content: { text } }),
      });
    } catch (err) {
      return json({ code: 9, msg: `upstream fetch failed: ${err}` }, 502);
    }

    const out = await upstream.json().catch(() => ({}));
    const code = out.code ?? out.errcode;
    if (code !== 0 && code !== undefined) {
      return json({ code: 10, msg: `feishu rejected: code=${code}` }, 502);
    }
    return json({ code: 0, msg: "ok", counted: ipHit.counted });
  },
};
