/* PrestaUnion / PrestamYpe — embeddable floating chat widget (Vox light theme).
 *
 * TWO WAYS to use it:
 *   1. Same-origin demo landing — include <script src="widget.js"></script>.
 *      The widget auto-mounts: it creates its OWN shadow-host + Shadow DOM and
 *      renders inside it (config read from the <script> data-* / ?query).
 *   2. External embed (Intercom/Drift style) — the loader `embed.js` creates a
 *      host element, attaches a Shadow DOM, loads THIS file, and calls
 *      `window.PubotWidget.mount({ shadowRoot, api, tenant, ct })`. When a
 *      mount target is provided the auto-mount is skipped.
 *
 * SHADOW DOM ISOLATION (key): the FAB + panel + ALL their styles live inside a
 * shadow root, never in the host page's document. The client's CSS can't bleed
 * into the widget and the widget's CSS can't touch the client's page. The only
 * thing that still goes on the host document is the Inter @font-face <link>
 * (fonts must be reachable from the document to apply; harmless and global-safe)
 * and a global Escape keydown listener (acts only while the panel is open).
 *
 * Talks to the cobranza backend: security handshake (session + CSRF), then
 * POST /api/v1/chat with the demo campaign_token read from ?ct=. The hard
 * identity gate lives server-side; this is just the chat surface.
 *
 * Config via the script tag's data-* attributes / query params (or mount opts):
 *   data-api   | ?api=  → backend base URL override (default: auto)
 *   data-ct    | ?ct=   → demo token (e.g. demo-juan / demo-1)
 *   data-tenant| ?tenant=→ tenant slug (default "prestaunion")
 *
 * BASE PATH (reverse proxy): all API URLs are built against a base derived
 * from the URL of THIS script. Served at /widget.js → base "" (local); served
 * at /pubot-gj5w2a0p/widget.js (behind Traefik strip-prefix) → base
 * "https://host/pubot-gj5w2a0p". Works in both without code changes. An
 * explicit api / ?api= still wins (e.g. external embed on another origin).
 */
(() => {
  "use strict";

  // ── Config resolution ──
  const scriptEl = document.currentScript;
  const qs = new URLSearchParams(location.search);

  function _deriveApiBase(override) {
    // 1) explicit override (external embed / mount opts)
    override = override || (scriptEl && scriptEl.dataset.api) || qs.get("api");
    if (override) return override.replace(/\/$/, "");
    // 2) derive from this script's own URL: origin + everything up to /widget.js
    try {
      const src = scriptEl && scriptEl.src;
      if (src) {
        const u = new URL(src, location.href);
        const prefix = u.pathname.replace(/\/widget\.js$/, "");  // "" or "/pubot-gj5w2a0p"
        return (u.origin + prefix).replace(/\/$/, "");
      }
    } catch (_e) { /* fall through */ }
    // 3) fallback: current origin (no prefix)
    return location.origin.replace(/\/$/, "");
  }

  // Resolved at mount() time so the loader (embed.js) can override api/ct/tenant
  // without depending on document.currentScript (which is null when injected).
  let API, CT, TENANT;
  // The shadow root the widget renders into. ALL DOM queries go through it.
  let shadow = null;

  function _resolveConfig(opts) {
    opts = opts || {};
    API = _deriveApiBase(opts.api);
    CT = opts.ct || (scriptEl && scriptEl.dataset.ct) || qs.get("ct") || null;
    // Tenant-aware: selects which tenant the widget talks to and skins for.
    // Default "prestaunion" keeps the original Vox theme untouched.
    TENANT = (opts.tenant || (scriptEl && scriptEl.dataset.tenant) || qs.get("tenant") || "prestaunion")
      .replace(/[^a-z0-9_-]/gi, "");
  }

  // Branding (fetched for non-default tenants). Drives header name/logo, brand
  // color CSS vars, and footer. Null until the fetch resolves.
  let branding = null;

  const PROFILES = {
    "demo-juan":   { who: "Juan Pérez Rojas",    biz: "Bodega Don Juan E.I.R.L.",      first: "Juan" },
    "demo-carlos": { who: "Carlos Huamán Flores", biz: "Ferretería El Tornillo S.A.C.", first: "Carlos" },
    "demo-maria":  { who: "María Quispe Mamani",   biz: "Textiles María E.I.R.L.",       first: "María" },
  };
  const CHIPS = {
    "demo-juan":   ["¿Cuánto debo?", "¿Cuándo vence mi próxima cuota?", "Quiero poner un reclamo"],
    "demo-carlos": ["¿Cuál es mi saldo?", "Estoy en mora, ¿qué hago?", "¿Cómo pago?"],
    "demo-maria":  ["¿Tengo deuda pendiente?", "Quiero mi certificado de no adeudo", "Poner un reclamo"],
    "cold":        ["Consultar mi préstamo", "¿Cómo pago mi cuota?", "¿Qué es la TCEA?"],
  };
  // PrestamYpe is scoped to TWO capabilities only: debt query + payment-voucher
  // upload. Its chips never offer negociación/refi/plan/certificado/reclamo.
  // Tenant-aware: split by identity state (verified vs cold).
  const PRESTAMYPE_CHIPS = {
    identified: ["Ver mi deuda", "Subir comprobante de pago", "¿A qué cuenta pago?"],
    cold:       ["Consultar mi deuda", "Subir comprobante de pago"],
  };

  // PrestamYpe demo tokens → DNI (from the seeded fixture). Used to prefill the
  // comprobante upload DNI when entering via a demo card. Other tenants resolve
  // the DNI server-side on the first chat turn.
  const PRESTAMYPE_TOKEN_DNI = { "demo-1": "44218903", "demo-2": "08642195", "demo-3": "45893017" };

  // ── Styles (scoped under #pu-widget-root + #pu-fab; Vox light tokens) ──
  // Brand colors live in CSS vars so a tenant fetch can re-skin the widget by
  // overriding --pu-brand / --pu-brand-hover / --pu-brand-soft at runtime.
  const CSS = `
  #pu-fab, #pu-widget-root {
    --pu-brand: #0083E0;        /* fills: FAB, user bubble, send btn */
    --pu-brand-hover: #0070bf;  /* text/icons/accents on white (>=4.5:1) */
    --pu-brand-soft: #C5E4F9;   /* soft tint backgrounds */
  }
  #pu-fab, #pu-widget-root, #pu-widget-root * { box-sizing: border-box; font-family: 'Inter', system-ui, -apple-system, sans-serif; }
  #pu-fab {
    position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
    width: 60px; height: 60px; border-radius: 9999px; border: 0; cursor: pointer;
    background: var(--pu-brand); color: #fff;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 10px 30px -6px rgba(10,13,18,0.20), 0 2px 8px rgba(19,53,77,0.12);
    transition: transform .2s ease, background .2s ease, box-shadow .2s ease;
  }
  #pu-fab:hover { background: var(--pu-brand-hover); transform: translateY(-2px) scale(1.04); box-shadow: 0 14px 36px -6px rgba(10,13,18,0.28); }
  #pu-fab svg { width: 26px; height: 26px; transition: transform .2s ease; }
  #pu-fab .pu-ico-close { display: none; }
  #pu-fab.pu-open .pu-ico-chat { display: none; }
  #pu-fab.pu-open .pu-ico-close { display: block; }
  #pu-fab .pu-badge {
    position: absolute; top: -2px; right: -2px; width: 14px; height: 14px; border-radius: 50%;
    background: #10b981; border: 2px solid #fff;
  }

  #pu-widget-root {
    position: fixed; right: 24px; bottom: 96px; z-index: 2147483000;
    width: 384px; height: 600px; max-height: calc(100vh - 120px);
    background: #ffffff; border: 1px solid #D7D8DB; border-radius: 18px;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 24px 60px -12px rgba(19,53,77,0.22), 0 6px 18px rgba(19,53,77,0.08);
    opacity: 0; transform: translateY(16px) scale(0.98); pointer-events: none;
    transition: opacity .25s ease, transform .25s ease;
    color: #1A1A1C;
  }
  #pu-widget-root.pu-open { opacity: 1; transform: translateY(0) scale(1); pointer-events: auto; }

  /* Header — darker brand (var(--pu-brand-hover) ~5:1 with white) so the small header text
     ("En línea ahora", name, icon buttons) meets WCAG AA on the fill. */
  #pu-widget-root .pu-header {
    display: flex; align-items: center; gap: 12px; padding: 14px 16px;
    background: var(--pu-brand-hover); color: #fff; flex-shrink: 0;
  }
  #pu-widget-root .pu-avatar {
    width: 40px; height: 40px; border-radius: 12px; background: rgba(255,255,255,0.18);
    display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 17px; flex-shrink: 0;
  }
  #pu-widget-root .pu-htext { flex: 1; min-width: 0; }
  #pu-widget-root .pu-hname { font-weight: 700; font-size: 15px; line-height: 1.2; }
  #pu-widget-root .pu-hstatus { font-size: 12px; display: flex; align-items: center; gap: 6px; margin-top: 2px; }
  #pu-widget-root .pu-dot { width: 7px; height: 7px; border-radius: 50%; background: #10b981; box-shadow: 0 0 0 2px rgba(255,255,255,0.35); }
  #pu-widget-root .pu-hbtns { display: flex; gap: 4px; }
  /* Visual ~30px chip, but a transparent ::before extends the tap target to
     >=44px (WCAG 2.5.5) without changing the dense header layout. */
  #pu-widget-root .pu-hbtn {
    position: relative; width: 30px; height: 30px; border-radius: 8px; border: 0; cursor: pointer;
    background: rgba(255,255,255,0.12); color: #fff; display: flex; align-items: center; justify-content: center; transition: background .15s;
  }
  #pu-widget-root .pu-hbtn::before { content: ""; position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%); width: 44px; height: 44px; }
  #pu-widget-root .pu-hbtn:hover { background: rgba(255,255,255,0.26); }
  #pu-widget-root .pu-hbtn svg { width: 16px; height: 16px; position: relative; }

  /* Identity strip */
  #pu-widget-root .pu-ident {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 10px 16px; background: #f7f8fa; border-bottom: 1px solid #eef0f3; flex-shrink: 0;
  }
  #pu-widget-root .pu-ident .who { font-weight: 700; font-size: 12.5px; }
  #pu-widget-root .pu-ident .biz { font-size: 11px; color: #4a5568; }
  #pu-widget-root .pu-ibadge { font-size: 10.5px; font-weight: 700; padding: 4px 9px; border-radius: 9999px; white-space: nowrap; }
  #pu-widget-root .pu-ibadge.aldia { background: rgba(16,185,129,0.12); color: #059669; }
  #pu-widget-root .pu-ibadge.mora  { background: rgba(239,68,68,0.12);  color: #dc2626; }
  #pu-widget-root .pu-ibadge.libre { background: var(--pu-brand-soft); color: var(--pu-brand-hover); }
  #pu-widget-root .pu-ibadge.cold  { background: rgba(239,68,68,0.12);  color: #dc2626; }

  /* Messages */
  #pu-widget-root .pu-messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; background: #ffffff; }
  #pu-widget-root .pu-messages::-webkit-scrollbar { width: 6px; }
  #pu-widget-root .pu-messages::-webkit-scrollbar-thumb { background: #D7D8DB; border-radius: 3px; }

  #pu-widget-root .pu-welcome { text-align: center; padding: 16px 6px 6px; animation: pu-fade .4s ease-out; }
  #pu-widget-root .pu-welcome .pu-wava { width: 52px; height: 52px; border-radius: 15px; background: var(--pu-brand-soft); color: var(--pu-brand-hover);
    display: inline-flex; align-items: center; justify-content: center; font-weight: 800; font-size: 22px; margin-bottom: 10px; }
  #pu-widget-root .pu-welcome h4 { margin: 0 0 6px; font-size: 17px; font-weight: 800; color: #1A1A1C; }
  #pu-widget-root .pu-welcome h4 .ac { color: var(--pu-brand-hover); }
  #pu-widget-root .pu-welcome p { margin: 0; font-size: 13px; color: #4a5568; line-height: 1.5; }

  #pu-widget-root .pu-msg.user { display: flex; justify-content: flex-end; animation: pu-slide .25s ease-out; }
  #pu-widget-root .pu-msg.user .pu-bubble { background: var(--pu-brand); color: #fff; padding: 10px 14px; border-radius: 16px 16px 4px 16px; max-width: 82%; font-size: 14px; line-height: 1.45; font-weight: 500; }
  #pu-widget-root .pu-msg.agent { display: flex; gap: 9px; animation: pu-slide .25s ease-out; }
  #pu-widget-root .pu-msg.agent .pu-mava { width: 30px; height: 30px; border-radius: 9px; background: var(--pu-brand-soft); color: var(--pu-brand-hover);
    display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 12px; flex-shrink: 0; }
  #pu-widget-root .pu-msg.agent .pu-mbody { flex: 1; min-width: 0; }
  #pu-widget-root .pu-msg.agent .pu-reply { background: #f7f8fa; border: 1px solid #eef0f3; color: #1A1A1C;
    padding: 10px 14px; border-radius: 4px 16px 16px 16px; font-size: 14px; line-height: 1.55; white-space: pre-wrap; word-wrap: break-word; }
  #pu-widget-root .pu-msg.agent .pu-reply a { color: var(--pu-brand-hover); font-weight: 600; }
  #pu-widget-root .pu-msg.agent .pu-reply strong { color: var(--pu-brand-hover); }
  /* System / error notice: neutral, centered, NO Ada avatar (not her voice). */
  #pu-widget-root .pu-msg.system { justify-content: center; }
  #pu-widget-root .pu-sysmsg { display: inline-flex; align-items: center; gap: 8px; max-width: 88%;
    background: #f3f4f6; border: 1px solid #e3e5e9; border-radius: 12px; padding: 9px 13px;
    color: #5b6573; font-size: 12.5px; line-height: 1.4; text-align: left; }
  #pu-widget-root .pu-sys-ico { width: 16px; height: 16px; flex-shrink: 0; color: #b45309; }
  /* Certificate / document download chip — reads as a downloadable file. */
  #pu-widget-root .pu-doc-link { display: inline-flex; align-items: center; gap: 7px; margin-top: 8px;
    padding: 8px 12px; border: 1px solid var(--pu-brand-hover); background: var(--pu-brand-soft); border-radius: 10px;
    color: var(--pu-brand-hover); font-weight: 600; font-size: 13px; text-decoration: none; min-height: 40px; box-sizing: border-box; }
  #pu-widget-root .pu-doc-link:hover { border-color: var(--pu-brand-hover); }
  #pu-widget-root .pu-dl-ico { width: 16px; height: 16px; flex-shrink: 0; }
  #pu-widget-root .pu-typing { display: inline-flex; align-items: center; gap: 4px; padding: 11px 14px; background: #f7f8fa; border: 1px solid #eef0f3; border-radius: 4px 16px 16px 16px; }
  #pu-widget-root .pu-typing span { width: 6px; height: 6px; border-radius: 50%; background: #94a3b8; animation: pu-typing 1.4s infinite ease-in-out; }
  #pu-widget-root .pu-typing span:nth-child(2) { animation-delay: .2s; }
  #pu-widget-root .pu-typing span:nth-child(3) { animation-delay: .4s; }

  #pu-widget-root .pu-chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
  /* min-height 44px = WCAG 2.5.5 touch target; visual size kept via padding. */
  #pu-widget-root .pu-chips button { background: #fff; border: 1px solid #D7D8DB; color: #4a5568; font-size: 12.5px; font-weight: 500;
    padding: 10px 14px; min-height: 44px; border-radius: 9999px; cursor: pointer; transition: all .15s; }
  #pu-widget-root .pu-chips button:hover { border-color: var(--pu-brand-hover); color: var(--pu-brand-hover); background: #f7fbff; }

  /* Input */
  #pu-widget-root .pu-inputbar { padding: 12px; border-top: 1px solid #eef0f3; background: #ffffff; flex-shrink: 0; }
  /* Reset confirmation bar (mid-claim misclick protection). */
  #pu-widget-root .pu-confirm { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    background: var(--vox-surface-2, #f7f8fa); border: 1px solid #eef0f3; border-radius: 10px; padding: 8px 10px; margin-bottom: 10px; font-size: 12.5px; color: #1A1A1C; }
  #pu-widget-root .pu-confirm span { flex: 1; min-width: 120px; }
  #pu-widget-root .pu-confirm button { border: 1px solid #D7D8DB; background: #fff; border-radius: 8px; padding: 6px 12px; min-height: 36px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
  #pu-widget-root .pu-confirm .yes { color: #b91c1c; border-color: rgba(239,68,68,0.4); }
  #pu-widget-root .pu-confirm .no { color: var(--pu-brand-hover); border-color: var(--pu-brand-hover); }
  /* Soft DNI-format hint (cold/unverified). */
  #pu-widget-root .pu-hint { font-size: 11.5px; color: #5b6573; margin-bottom: 8px; padding-left: 4px; }
  #pu-widget-root .pu-form { display: flex; gap: 8px; align-items: flex-end; background: #f7f8fa; border: 1px solid #D7D8DB; border-radius: 14px; padding: 6px 6px 6px 14px; transition: border-color .2s, box-shadow .2s; }
  #pu-widget-root .pu-form:focus-within { border-color: var(--pu-brand); box-shadow: 0 0 0 3px rgba(0,131,224,0.12); background: #fff; }
  #pu-widget-root .pu-form textarea { flex: 1; background: transparent; border: 0; outline: 0; resize: none; color: #1A1A1C; font-size: 14px; line-height: 1.45; padding: 6px 0; max-height: 110px; min-height: 22px; }
  #pu-widget-root .pu-form textarea::placeholder { color: #6b7480; }
  /* 44x44 touch target (WCAG 2.5.5); the blue circle stays visually ~34px via the gradient-free fill + centered icon. */
  #pu-widget-root .pu-sendbtn { width: 44px; height: 44px; border-radius: 50%; background: var(--pu-brand); border: 0; color: #fff; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all .15s; flex-shrink: 0; }
  #pu-widget-root .pu-sendbtn:hover { background: var(--pu-brand-hover); transform: scale(1.05); }
  #pu-widget-root .pu-sendbtn:disabled { opacity: .5; cursor: not-allowed; transform: none; }
  #pu-widget-root .pu-sendbtn svg { width: 18px; height: 18px; }
  #pu-widget-root .pu-footer { text-align: center; font-size: 10.5px; color: #5b6573; margin-top: 8px; }
  #pu-widget-root .pu-footer b { color: #4a5568; font-weight: 600; }

  @keyframes pu-fade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes pu-slide { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes pu-typing { 0%,60%,100% { transform: translateY(0); opacity: .4; } 30% { transform: translateY(-4px); opacity: 1; } }

  /* ── Comprobante upload: action bar button + slide-up panel ── */
  #pu-widget-root .pu-actionbar { display: flex; gap: 8px; padding: 0 12px 10px; flex-shrink: 0; }
  #pu-widget-root .pu-action {
    display: inline-flex; align-items: center; gap: 7px; flex: 1; justify-content: center;
    min-height: 44px; padding: 10px 12px; border-radius: 8px; cursor: pointer;
    border: 1px solid var(--pu-brand); background: var(--pu-brand-soft); color: var(--pu-brand-hover);
    font-size: 13px; font-weight: 600; transition: filter .15s; }
  #pu-widget-root .pu-action:hover { filter: brightness(0.97); }
  #pu-widget-root .pu-action svg { width: 16px; height: 16px; }

  /* ── Side panel (contextual): debt cards + comprobante upload ──
     Sits to the LEFT of the floating chat, same vertical band. It's its OWN
     fixed element inside the shadow root (a sibling of the chat panel), so the
     chat layout is untouched. Hidden until content is shown; collapsible. */
  #pu-sidepanel {
    --pu-brand: #0083E0; --pu-brand-hover: #0070bf; --pu-brand-soft: #C5E4F9;
    position: fixed; bottom: 96px; right: calc(24px + 384px + 12px); z-index: 2147482999;
    width: 360px; max-width: calc(100vw - 32px); height: 600px; max-height: calc(100vh - 120px);
    background: #ffffff; border: 1px solid #D7D8DB; border-radius: 18px;
    display: flex; flex-direction: column; overflow: hidden; color: #1A1A1C;
    box-shadow: 0 24px 60px -12px rgba(19,53,77,0.22), 0 6px 18px rgba(19,53,77,0.08);
    opacity: 0; transform: translateX(16px) scale(0.98); pointer-events: none;
    transition: opacity .25s ease, transform .25s ease;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
  }
  #pu-sidepanel, #pu-sidepanel * { box-sizing: border-box; }
  #pu-sidepanel.pu-open { opacity: 1; transform: translateX(0) scale(1); pointer-events: auto; }
  #pu-sidepanel .pu-sp-head {
    display: flex; align-items: center; gap: 10px; padding: 13px 14px; flex-shrink: 0;
    background: var(--pu-brand-hover); color: #fff; }
  #pu-sidepanel .pu-sp-title { flex: 1; min-width: 0; font-weight: 700; font-size: 14px; }
  #pu-sidepanel .pu-sp-close {
    position: relative; width: 30px; height: 30px; border-radius: 8px; border: 0; cursor: pointer;
    background: rgba(255,255,255,0.12); color: #fff; display: flex; align-items: center; justify-content: center; transition: background .15s; }
  #pu-sidepanel .pu-sp-close::before { content: ""; position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 44px; height: 44px; }
  #pu-sidepanel .pu-sp-close:hover { background: rgba(255,255,255,0.26); }
  #pu-sidepanel .pu-sp-close svg { width: 15px; height: 15px; }
  #pu-sidepanel .pu-sp-body { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }
  #pu-sidepanel .pu-sp-body::-webkit-scrollbar { width: 6px; }
  #pu-sidepanel .pu-sp-body::-webkit-scrollbar-thumb { background: #D7D8DB; border-radius: 3px; }

  /* Debt cards */
  #pu-sidepanel .pu-card { border: 1px solid #e4e7ec; border-radius: 14px; padding: 14px; background: #fff;
    box-shadow: 0 1px 2px rgba(16,24,40,0.04); animation: pu-fade .35s ease-out; }
  #pu-sidepanel .pu-card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
  #pu-sidepanel .pu-card-loan { font-size: 11px; font-weight: 700; color: #667085; letter-spacing: .02em; }
  #pu-sidepanel .pu-card-bank { font-size: 12px; color: #98a2b3; margin-top: 2px; }
  #pu-sidepanel .pu-card-badge { font-size: 10.5px; font-weight: 700; padding: 4px 9px; border-radius: 9999px; white-space: nowrap; }
  #pu-sidepanel .pu-card-badge.aldia { background: rgba(16,185,129,0.12); color: #059669; }
  #pu-sidepanel .pu-card-badge.mora  { background: rgba(239,68,68,0.12);  color: #dc2626; }
  #pu-sidepanel .pu-card-badge.libre { background: var(--pu-brand-soft); color: var(--pu-brand-hover); }
  #pu-sidepanel .pu-card-balance-lbl { font-size: 11px; color: #667085; }
  #pu-sidepanel .pu-card-balance { font-size: 26px; font-weight: 800; color: #181d27; line-height: 1.15; margin-top: 1px; }
  #pu-sidepanel .pu-card-rows { margin-top: 12px; display: flex; flex-direction: column; gap: 7px; border-top: 1px solid #f2f4f7; padding-top: 11px; }
  #pu-sidepanel .pu-card-row { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; font-size: 12.5px; }
  #pu-sidepanel .pu-card-row .k { color: #667085; }
  #pu-sidepanel .pu-card-row .v { font-weight: 700; color: #1a1a1c; text-align: right; }
  #pu-sidepanel .pu-card-grupal { margin-top: 11px; padding-top: 10px; border-top: 1px solid #f2f4f7; }
  #pu-sidepanel .pu-card-grupal .gtitle { font-size: 11px; font-weight: 700; color: var(--pu-brand-hover); margin-bottom: 5px; }
  #pu-sidepanel .pu-card-grupal .gco { font-size: 12px; color: #475467; display: flex; justify-content: space-between; gap: 8px; padding: 2px 0; }
  #pu-sidepanel .pu-card-grupal .gco .grol { color: #98a2b3; font-size: 11px; }

  /* Comprobante form intro line (now hosted in the side panel). */
  #pu-sidepanel .pu-msub { margin: 0 0 14px; font-size: 12.5px; color: #535862; line-height: 1.4; }
  /* Comprobante form styles are SHARED by the chat root and the side panel
     (the form was moved out of the modal into the side panel). */
  #pu-widget-root .pu-field, #pu-sidepanel .pu-field { margin-bottom: 11px; }
  #pu-widget-root .pu-field label, #pu-sidepanel .pu-field label { display: block; font-size: 12px; font-weight: 600; color: #414651; margin-bottom: 5px; }
  #pu-widget-root .pu-field input, #pu-sidepanel .pu-field input {
    width: 100%; font-size: 14px; color: #181d27; padding: 10px 12px; min-height: 44px;
    border: 1px solid #d5d7da; border-radius: 8px; background: #fff; outline: 0; transition: border-color .15s, box-shadow .15s; }
  #pu-widget-root .pu-field input:focus, #pu-sidepanel .pu-field input:focus { border-color: var(--pu-brand); box-shadow: 0 0 0 3px var(--pu-brand-soft); }
  #pu-widget-root .pu-field input[type=file], #pu-sidepanel .pu-field input[type=file] { padding: 9px 12px; font-size: 12.5px; }
  #pu-widget-root .pu-field .pu-row2, #pu-sidepanel .pu-field .pu-row2 { display: flex; gap: 10px; }
  #pu-widget-root .pu-field .pu-row2 > div, #pu-sidepanel .pu-field .pu-row2 > div { flex: 1; }
  #pu-widget-root .pu-modal-acts, #pu-sidepanel .pu-modal-acts { display: flex; gap: 8px; margin-top: 6px; }
  #pu-widget-root .pu-btn-primary, #pu-sidepanel .pu-btn-primary { flex: 1; min-height: 44px; border: 0; border-radius: 8px; cursor: pointer;
    background: var(--pu-brand); color: #fff; font-size: 14px; font-weight: 600; box-shadow: 0 1px 2px 0 rgba(10,13,18,.05); transition: filter .15s; }
  #pu-widget-root .pu-btn-primary:hover, #pu-sidepanel .pu-btn-primary:hover { filter: brightness(0.96); }
  #pu-widget-root .pu-btn-primary:disabled, #pu-sidepanel .pu-btn-primary:disabled { opacity: .55; cursor: not-allowed; }
  #pu-widget-root .pu-btn-ghost, #pu-sidepanel .pu-btn-ghost { min-height: 44px; padding: 0 16px; border: 1px solid #d5d7da; border-radius: 8px;
    background: #fff; color: #414651; font-size: 14px; font-weight: 600; cursor: pointer; }
  #pu-widget-root .pu-cb-err, #pu-sidepanel .pu-cb-err { font-size: 12px; color: #d92d20; margin: 4px 0 8px; min-height: 0; }
  /* Inline per-field format hints (turn red on invalid, green when valid). */
  #pu-widget-root .pu-cb-fieldhint, #pu-sidepanel .pu-cb-fieldhint { font-size: 11.5px; margin-top: 4px; min-height: 0; color: #535862; }
  #pu-widget-root .pu-cb-fieldhint.err, #pu-sidepanel .pu-cb-fieldhint.err { color: #d92d20; }
  #pu-widget-root .pu-cb-fieldhint.ok, #pu-sidepanel .pu-cb-fieldhint.ok  { color: #079455; }
  #pu-widget-root .pu-field input.pu-invalid, #pu-sidepanel .pu-field input.pu-invalid { border-color: #f04438; box-shadow: 0 0 0 3px rgba(240,68,56,.12); }
  /* Inline result chips (success / warn / error) shown inside the form. */
  #pu-widget-root .pu-cb-result, #pu-sidepanel .pu-cb-result { font-size: 13px; line-height: 1.5; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }
  #pu-widget-root .pu-cb-result.ok, #pu-sidepanel .pu-cb-result.ok   { background: #ecfff6; border: 1px solid #b3ffdf; color: #079455; }
  #pu-widget-root .pu-cb-result.warn, #pu-sidepanel .pu-cb-result.warn { background: #fffaeb; border: 1px solid #fdb022; color: #b54708; }
  #pu-widget-root .pu-cb-result.err, #pu-sidepanel .pu-cb-result.err  { background: #fef3f2; border: 1px solid #f04438; color: #d92d20; }
  #pu-widget-root .pu-cb-result strong, #pu-sidepanel .pu-cb-result strong { font-weight: 800; }
  /* Account-type selector (cuenta / CCI). */
  #pu-widget-root .pu-cb-segch, #pu-sidepanel .pu-cb-segch { display: flex; flex-direction: column; gap: 6px; }
  #pu-widget-root .pu-cb-seg, #pu-sidepanel .pu-cb-seg { display: flex; align-items: center; gap: 8px; padding: 9px 11px; border: 1px solid #d5d7da; border-radius: 8px;
    cursor: pointer; font-size: 13px; color: #414651; font-weight: 600; transition: border-color .15s, background .15s; }
  #pu-widget-root .pu-cb-seg:hover, #pu-sidepanel .pu-cb-seg:hover { border-color: var(--pu-brand-hover); }
  #pu-widget-root .pu-cb-seg input, #pu-sidepanel .pu-cb-seg input { width: 16px; height: 16px; min-height: 0; accent-color: var(--pu-brand); margin: 0; flex: 0 0 auto; }
  #pu-widget-root .pu-cb-seg:has(input:checked), #pu-sidepanel .pu-cb-seg:has(input:checked) { border-color: var(--pu-brand); background: var(--pu-brand-soft); color: var(--pu-brand-hover); }
  /* Helper + example. */
  #pu-widget-root .pu-cb-help, #pu-sidepanel .pu-cb-help { font-size: 11.5px; color: #535862; margin-top: 5px; line-height: 1.4; }
  #pu-widget-root .pu-cb-help b, #pu-sidepanel .pu-cb-help b { color: #181d27; font-weight: 700; }
  #pu-widget-root .pu-cb-egtoggle, #pu-sidepanel .pu-cb-egtoggle { margin-top: 7px; background: none; border: 0; padding: 0; cursor: pointer;
    font-size: 12px; font-weight: 700; color: var(--pu-brand-hover); text-decoration: underline; }
  #pu-widget-root .pu-cb-egfig, #pu-sidepanel .pu-cb-egfig { margin: 9px 0 0; padding: 8px; border: 1px solid #eef0f3; border-radius: 10px; background: #fbfdfc; }
  #pu-widget-root .pu-cb-egcap, #pu-sidepanel .pu-cb-egcap { font-size: 10.5px; color: #667085; margin-top: 6px; line-height: 1.4; }
  #pu-widget-root .pu-cb-egcap b, #pu-sidepanel .pu-cb-egcap b { color: var(--pu-brand-hover); font-weight: 700; }

  /* ── Responsive: tablet — side panel can't fit left of the chat. Dock it
     to the same right edge, stacked ABOVE the chat (so both are reachable). */
  @media (max-width: 900px) {
    #pu-sidepanel { right: 24px; bottom: calc(96px + 600px + 12px);
      bottom: min(calc(96px + 600px + 12px), calc(100vh - 280px)); height: auto; max-height: 46vh; }
  }
  /* Mobile: chat + side panel both near-fullscreen overlays. The side panel
     covers the chat (full-width) and its close button returns to the chat. */
  @media (max-width: 480px) {
    #pu-widget-root { right: 0; bottom: 0; width: 100vw; height: 100dvh; max-height: 100dvh; border-radius: 16px 16px 0 0; border-bottom: 0; }
    #pu-fab { right: 16px; bottom: 16px; }
    #pu-sidepanel { right: 0; left: 0; bottom: 0; top: 0; width: 100vw; max-width: 100vw;
      height: 100dvh; max-height: 100dvh; border-radius: 0; border: 0; z-index: 2147483001; }
  }
  `;

  // ── State ──
  const visitorId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  let conversationId = null;
  let csrfToken = null;
  let sessionToken = null;
  let busy = false;
  let open = false;
  let started = false; // welcome shown until first message

  // ── DOM build ──
  function ensureInterFont() {
    if (document.getElementById("pu-inter-font")) return;
    const link = document.createElement("link");
    link.id = "pu-inter-font";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap";
    document.head.appendChild(link);
  }

  function injectStyles() {
    // Styles live INSIDE the shadow root (scoped), never in the host document.
    if (shadow.getElementById && shadow.getElementById("pu-widget-styles")) return;
    if (shadow.querySelector("#pu-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "pu-widget-styles";
    style.textContent = CSS;
    shadow.appendChild(style);
  }

  const ICONS = {
    chat: '<svg class="pu-ico-chat" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.86 9.86 0 01-4-.84L3 20l1.05-3.5A7.6 7.6 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>',
    close: '<svg class="pu-ico-close" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>',
    minimize: '<svg fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14"/></svg>',
    reset: '<svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h5M20 20v-5h-5M5 9a7 7 0 0111-3.5M19 15a7 7 0 01-11 3.5"/></svg>',
    send: '<svg fill="none" stroke="currentColor" stroke-width="2.4" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14m0 0l-7-7m7 7l-7 7"/></svg>',
    download: '<svg class="pu-dl-ico" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14"/></svg>',
    alert: '<svg class="pu-sys-ico" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.86l-8.1 14A1 1 0 003 19.5h18a1 1 0 00.86-1.5l-8.1-14a1 1 0 00-1.72 0z"/></svg>',
    upload: '<svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 16V4m0 0L8 8m4-4l4 4M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>',
    close2: '<svg fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>',
  };

  // Comprobante upload form (lives in the side panel, NOT a modal). Same fields,
  // same ids, same validation as before — only the host container changed.
  // Built lazily by a function (not a const) so it can reference
  // VOUCHER_EXAMPLE_SVG, which is declared further down (avoids a TDZ error).
  const comprobanteFormHtml = () => `
    <p class="pu-msub">Sube tu constancia de transferencia y dinos a qué cuenta pagaste. La clasificamos al instante; queda en revisión.</p>
    <div class="pu-cb-result" id="pu-cb-result" style="display:none;"></div>
    <form id="pu-cb-form">
      <div class="pu-field">
        <label for="pu-cb-file">Imagen o PDF del comprobante</label>
        <input type="file" id="pu-cb-file" accept="image/jpeg,image/png,application/pdf" required />
      </div>
      <div class="pu-field">
        <label id="pu-cb-acct-lbl">¿Qué dato de la cuenta destino vas a ingresar?</label>
        <div class="pu-cb-segch" role="radiogroup" aria-labelledby="pu-cb-acct-lbl">
          <label class="pu-cb-seg">
            <input type="radio" name="pu-cb-acct" value="cuenta" checked />
            <span>Número de cuenta</span>
          </label>
          <label class="pu-cb-seg">
            <input type="radio" name="pu-cb-acct" value="cci" />
            <span>Código de Cuenta Interbancario (CCI)</span>
          </label>
        </div>
      </div>
      <div class="pu-field">
        <label for="pu-cb-cci" id="pu-cb-cci-lbl">Número de cuenta destino</label>
        <input type="text" id="pu-cb-cci" inputmode="numeric" autocomplete="off" maxlength="20" placeholder="Ej. 1320268376" required aria-describedby="pu-cb-cci-hint" />
        <div class="pu-cb-help" id="pu-cb-cci-help">Es la cuenta del <b>destinatario del depósito</b> (a quién le pagaste / PrestamYpe), no tu propia cuenta.</div>
        <div class="pu-cb-fieldhint" id="pu-cb-cci-hint"></div>
        <button type="button" class="pu-cb-egtoggle" id="pu-cb-eg-toggle" aria-expanded="false" aria-controls="pu-cb-eg">Ver ejemplo de voucher</button>
        <div class="pu-cb-eg" id="pu-cb-eg" hidden>${VOUCHER_EXAMPLE_SVG}</div>
      </div>
      <div class="pu-field">
        <div class="pu-row2">
          <div>
            <label for="pu-cb-monto" id="pu-cb-monto-lbl">Monto</label>
            <input type="number" id="pu-cb-monto" inputmode="decimal" step="0.01" min="0.01" placeholder="0.00" required />
          </div>
          <div>
            <label for="pu-cb-op">Nº de operación</label>
            <input type="text" id="pu-cb-op" inputmode="text" autocomplete="off" maxlength="30" placeholder="Ej. 0012345" required />
          </div>
        </div>
      </div>
      <div class="pu-field">
        <label for="pu-cb-fecha">Fecha de pago (opcional)</label>
        <input type="date" id="pu-cb-fecha" />
      </div>
      <div class="pu-cb-err" id="pu-cb-err"></div>
      <div class="pu-modal-acts">
        <button type="button" class="pu-btn-ghost" id="pu-cb-cancel">Cancelar</button>
        <button type="submit" class="pu-btn-primary" id="pu-cb-submit">Enviar comprobante</button>
      </div>
    </form>`;

  // Illustrative voucher mockup (FICTITIOUS data) shown via "Ver ejemplo". It
  // points to where the "Número de cuenta" and the "CCI" appear on a typical
  // Peruvian transfer receipt. Accent color follows the tenant brand (var(--pu-brand)).
  const VOUCHER_EXAMPLE_SVG = `
    <figure class="pu-cb-egfig">
      <svg viewBox="0 0 320 232" width="100%" role="img" aria-label="Ejemplo ilustrativo de un voucher de transferencia señalando el número de cuenta y el CCI" xmlns="http://www.w3.org/2000/svg">
        <rect x="6" y="6" width="308" height="220" rx="12" fill="#ffffff" stroke="#e4e7ec"/>
        <rect x="6" y="6" width="308" height="34" rx="12" fill="var(--pu-brand-soft)"/>
        <rect x="6" y="28" width="308" height="12" fill="var(--pu-brand-soft)"/>
        <circle cx="26" cy="23" r="8" fill="var(--pu-brand)"/>
        <text x="42" y="27" font-family="system-ui,Arial" font-size="12" font-weight="700" fill="var(--pu-brand-hover)">Constancia de transferencia</text>
        <text x="20" y="60" font-family="system-ui,Arial" font-size="9" fill="#667085">Operación exitosa · Banco Demo</text>
        <text x="20" y="84" font-family="system-ui,Arial" font-size="9" fill="#98a2b3">Destinatario</text>
        <text x="20" y="98" font-family="system-ui,Arial" font-size="11" font-weight="600" fill="#1a1a1c">PRESTAMYPE S.A.</text>
        <!-- Número de cuenta (highlighted) -->
        <rect x="16" y="112" width="200" height="30" rx="6" fill="#f6fefb"/>
        <rect x="16" y="112" width="200" height="30" rx="6" fill="none" stroke="var(--pu-brand)" stroke-width="2" stroke-dasharray="4 3"/>
        <text x="24" y="125" font-family="system-ui,Arial" font-size="8" fill="#667085">Número de cuenta</text>
        <text x="24" y="138" font-family="ui-monospace,monospace" font-size="12" font-weight="700" fill="#1a1a1c">132-0268376</text>
        <rect x="232" y="116" width="74" height="18" rx="9" fill="var(--pu-brand)"/>
        <text x="269" y="129" font-family="system-ui,Arial" font-size="8.5" font-weight="700" fill="#ffffff" text-anchor="middle">N° de cuenta</text>
        <!-- CCI (highlighted) -->
        <rect x="16" y="156" width="288" height="32" rx="6" fill="none" stroke="var(--pu-brand-hover)" stroke-width="2" stroke-dasharray="4 3"/>
        <text x="24" y="169" font-family="system-ui,Arial" font-size="8" fill="#667085">Código de Cuenta Interbancario (CCI)</text>
        <text x="24" y="183" font-family="ui-monospace,monospace" font-size="11" font-weight="700" fill="#1a1a1c">002-193-001320268376-58</text>
        <rect x="224" y="194" width="82" height="18" rx="9" fill="var(--pu-brand-hover)"/>
        <text x="265" y="207" font-family="system-ui,Arial" font-size="8.5" font-weight="700" fill="#ffffff" text-anchor="middle">CCI · 20 dígitos</text>
        <text x="20" y="208" font-family="system-ui,Arial" font-size="8" fill="#98a2b3">Monto S/ 462.14 · Op. 0012345</text>
      </svg>
      <figcaption class="pu-cb-egcap">Ejemplo ilustrativo (datos ficticios). El <b>Número de cuenta</b> es más corto; el <b>CCI</b> tiene 20 dígitos.</figcaption>
    </figure>`;

  // Display name for header/welcome. Defaults match the static prestaunion look;
  // a branding fetch (non-default tenant) overrides AGENT + COMPANY at runtime.
  let AGENT = "Ada";
  let COMPANY = "PrestaUnion";

  // Verified DNI for the comprobante upload. Set from the token profile or from
  // a backend identity response (DNI typed mid-chat). Null = upload locked.
  let verifiedDni = null;

  let fab, root, $messages, $form, $input, $send;
  // Side panel (debt cards / comprobante form) + its body/title nodes.
  let sidePanel, $spBody, $spTitle;
  // What the side panel currently shows: null | "debt" | "comprobante".
  let spMode = null;

  function build() {
    // FAB
    fab = document.createElement("button");
    fab.id = "pu-fab";
    fab.setAttribute("aria-label", "Abrir chat de PrestaUnion");
    fab.innerHTML = `${ICONS.chat}${ICONS.close}<span class="pu-badge"></span>`;
    fab.addEventListener("click", toggle);
    shadow.appendChild(fab);

    // Panel
    root = document.createElement("div");
    root.id = "pu-widget-root";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", `Chat con ${AGENT} de ${COMPANY}`);
    root.innerHTML = `
      <div class="pu-header">
        <div class="pu-avatar">${AGENT.charAt(0).toUpperCase()}</div>
        <div class="pu-htext">
          <div class="pu-hname" id="pu-hname">${AGENT} · ${COMPANY}</div>
          <div class="pu-hstatus"><span class="pu-dot"></span> En línea ahora</div>
        </div>
        <div class="pu-hbtns">
          <button class="pu-hbtn" id="pu-reset" title="Reiniciar conversación" aria-label="Reiniciar">${ICONS.reset}</button>
          <button class="pu-hbtn" id="pu-min" title="Minimizar" aria-label="Minimizar">${ICONS.minimize}</button>
        </div>
      </div>
      <div class="pu-ident" id="pu-ident"></div>
      <div class="pu-messages" id="pu-messages" role="log" aria-live="polite" aria-atomic="false" aria-label="Conversación con ${AGENT}"></div>
      <div class="pu-actionbar" id="pu-actionbar" style="display:none;">
        <button type="button" class="pu-action" id="pu-comprobante-btn">${ICONS.upload} Subir comprobante de pago</button>
      </div>
      <div class="pu-inputbar">
        <form class="pu-form" id="pu-form">
          <textarea id="pu-input" placeholder="Escribe tu mensaje..." rows="1"></textarea>
          <button type="submit" class="pu-sendbtn" id="pu-send" aria-label="Enviar">${ICONS.send}</button>
        </form>
        <div class="pu-footer">Powered by <b>Onbotgo</b> · demo con datos ficticios</div>
      </div>`;
    shadow.appendChild(root);

    // ── Side panel (contextual): sibling of the chat, NOT a child of the chat
    // root. Hosts the debt cards OR the comprobante form (moved out of a modal).
    sidePanel = document.createElement("div");
    sidePanel.id = "pu-sidepanel";
    sidePanel.setAttribute("role", "complementary");
    sidePanel.setAttribute("aria-label", "Panel de información");
    sidePanel.innerHTML = `
      <div class="pu-sp-head">
        <div class="pu-sp-title" id="pu-sp-title">Tu crédito</div>
        <button type="button" class="pu-sp-close" id="pu-sp-close" aria-label="Cerrar panel">${ICONS.close2}</button>
      </div>
      <div class="pu-sp-body" id="pu-sp-body"></div>`;
    shadow.appendChild(sidePanel);
    sidePanel.querySelector("#pu-sp-close").addEventListener("click", closeSidePanel);
    $spBody = sidePanel.querySelector("#pu-sp-body");
    $spTitle = sidePanel.querySelector("#pu-sp-title");

    $messages = root.querySelector("#pu-messages");
    $form = root.querySelector("#pu-form");
    $input = root.querySelector("#pu-input");
    $send = root.querySelector("#pu-send");

    root.querySelector("#pu-min").addEventListener("click", () => setOpen(false));
    root.querySelector("#pu-reset").addEventListener("click", requestReset);
    root.querySelector("#pu-comprobante-btn").addEventListener("click", openComprobante);
    $form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
    $input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } });
    $input.addEventListener("input", () => {
      $input.style.height = "auto"; $input.style.height = Math.min($input.scrollHeight, 110) + "px";
      maybeDniHint();
    });
    // Esc closes the panel (keyboard-discoverable, not mouse-only).
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        // Escape closes the side panel first (if open), then the chat.
        if (sidePanel && sidePanel.classList.contains("pu-open")) { closeSidePanel(); return; }
        if (open) setOpen(false);
      }
    });

    renderIdentity();
    renderWelcome();
  }

  // ── Identity strip ──
  function _badgeFor(statusLabel) {
    const s = (statusLabel || "").toLowerCase();
    if (s.includes("mora")) return ["mora", statusLabel];
    if (s.includes("sin deuda") || s.includes("cancelado")) return ["libre", statusLabel || "Sin deuda"];
    return ["aldia", statusLabel || "Al día"];
  }

  function _renderIdentified(name, statusLabel, biz) {
    const $i = root.querySelector("#pu-ident");
    const [cls, label] = _badgeFor(statusLabel);
    const sub = biz ? `<div class="biz">${biz}</div>` : "";
    $i.innerHTML = `<div><div class="who">${name}</div>${sub}</div><span class="pu-ibadge ${cls}">${label}</span>`;
  }

  function renderIdentity() {
    const p = CT && PROFILES[CT];
    if (p) {
      // prestaunion demo tokens: known profile + label.
      const label = CT === "demo-juan" ? "Al día" : CT === "demo-carlos" ? "En mora" : "Sin deuda";
      _renderIdentified(p.who, label, p.biz);
    } else if (CT) {
      // Token from another tenant (e.g. prestamype demo-1/2/3): the chat resolves
      // the full profile server-side; show a verified placeholder until it does.
      _renderIdentified("Cliente verificado", "", "");
      if (PRESTAMYPE_TOKEN_DNI[CT]) verifiedDni = PRESTAMYPE_TOKEN_DNI[CT];
    } else {
      const $i = root.querySelector("#pu-ident");
      $i.innerHTML = `<div><div class="who">Sesión no identificada</div><div class="biz">Indícame tu DNI para ayudarte.</div></div><span class="pu-ibadge cold">Sin verificar</span>`;
    }
    // Comprobante upload (PrestamYpe) is offered once we have a verified DNI.
    revealComprobanteAction();
  }

  // The "Subir comprobante" action is PrestamYpe-only and only after identity.
  function revealComprobanteAction() {
    const bar = root.querySelector("#pu-actionbar");
    if (!bar) return;
    bar.style.display = (TENANT === "prestamype" && verifiedDni) ? "flex" : "none";
  }

  // Refresh the strip from the backend identity state (after DNI verification).
  // Shows business name as subline, consistent with the token (pre-verified) strip.
  function updateIdentityFromResponse(identity) {
    if (identity && identity.verified) {
      _renderIdentified(
        identity.display_name || "Cliente verificado",
        identity.status_label || "",
        identity.business_name || "",
      );
      // Capture the DNI if the backend exposes it (DNI typed mid-chat).
      if (identity.dni) verifiedDni = String(identity.dni).replace(/\D/g, "");
      revealComprobanteAction();
    }
  }

  function renderWelcome() {
    started = false;
    const p = CT && PROFILES[CT];
    const ini = AGENT.charAt(0).toUpperCase();
    let sub;
    if (TENANT === "prestamype") {
      sub = CT
        ? `Soy ${AGENT}, de ${COMPANY}. Puedo consultar tu crédito o validar tu comprobante de pago.`
        : `Soy ${AGENT}, de ${COMPANY}. Para ayudarte con tu crédito, indícame tu número de DNI.`;
    } else if (p) {
      sub = `Hola ${p.first}, soy ${AGENT}. Puedo consultar tu préstamo, registrar un reclamo o gestionar tu certificado.`;
    } else {
      sub = `Soy ${AGENT}, de ${COMPANY}. Para ayudarte con tu préstamo, indícame tu número de DNI.`;
    }
    const wrap = document.createElement("div");
    wrap.className = "pu-welcome";
    wrap.id = "pu-welcome";
    wrap.innerHTML = `<div class="pu-wava">${ini}</div><h4>Hola, soy <span class="ac">${AGENT}</span></h4><p>${sub}</p>`;
    const chips = document.createElement("div");
    chips.className = "pu-chips";
    chips.style.justifyContent = "center";
    // PrestamYpe: acotado a sus 2 capacidades, tenant-aware por identidad.
    // (CT o verifiedDni = identificado). NUNCA usa CHIPS[CT] de prestaunion.
    const welcomeChips = TENANT === "prestamype"
      ? ((CT || verifiedDni) ? PRESTAMYPE_CHIPS.identified : PRESTAMYPE_CHIPS.cold)
      : (CHIPS[CT] || CHIPS.cold);
    welcomeChips.forEach((c) => {
      const b = document.createElement("button");
      b.textContent = c;
      b.onclick = () => { $input.value = c; submit(); };
      chips.appendChild(b);
    });
    wrap.appendChild(chips);
    $messages.innerHTML = "";
    $messages.appendChild(wrap);
  }

  // Reset needs confirmation: a misclick mid-claim shouldn't wipe the context.
  let _resetBar = null;
  function requestReset() {
    if (_resetBar) return;  // already asking
    _resetBar = document.createElement("div");
    _resetBar.className = "pu-confirm";
    _resetBar.innerHTML = `<span>¿Seguro? Se borrará esta conversación.</span>
      <button type="button" class="yes">Sí, reiniciar</button>
      <button type="button" class="no">No</button>`;
    const dismiss = () => { if (_resetBar) { _resetBar.remove(); _resetBar = null; } };
    _resetBar.querySelector(".yes").onclick = () => { dismiss(); resetConversation(); };
    _resetBar.querySelector(".no").onclick = dismiss;
    root.querySelector(".pu-inputbar").prepend(_resetBar);
  }

  function resetConversation() {
    conversationId = null;
    renderWelcome();
    $input.focus();
  }

  // Soft DNI-format hint while cold (unverified) and the user types something
  // that looks like a DNI attempt but isn't 8 digits.
  function maybeDniHint() {
    if (CT) return;  // pre-identified by token → no hint needed
    const v = $input.value;
    // Natural language friendly: find the LONGEST run of digits anywhere in the
    // text ("Mi DNI es 417..." → "417"). Hint when that run is 1-7 digits; if a
    // full 8-digit run is present, don't nag.
    const runs = v.match(/\d+/g) || [];
    const longest = runs.reduce((m, r) => Math.max(m, r.length), 0);
    const looksLikeDni = longest >= 1 && longest <= 7;
    const bar = root.querySelector(".pu-inputbar");
    let hint = root.querySelector("#pu-dni-hint");
    if (looksLikeDni) {
      if (!hint) {
        hint = document.createElement("div");
        hint.id = "pu-dni-hint";
        hint.className = "pu-hint";
        hint.textContent = "Tu DNI son 8 dígitos.";
        bar.insertBefore(hint, bar.firstChild);
      }
    } else if (hint) {
      hint.remove();
    }
  }

  // ── Open / close ──
  function setOpen(v) {
    open = v;
    root.classList.toggle("pu-open", v);
    fab.classList.toggle("pu-open", v);
    // Closing the chat also tucks away the side panel (they travel together).
    if (!v && sidePanel) sidePanel.classList.remove("pu-open");
    if (v) { setTimeout(() => $input && $input.focus(), 250); scroll(); }
  }
  function toggle() { setOpen(!open); }

  // ── Security handshake ──
  async function handshake() {
    try {
      const s = await fetch(`${API}/api/v1/security/session-token?visitor_id=${visitorId}`);
      sessionToken = (await s.json()).token;
      const c = await fetch(`${API}/api/v1/security/csrf-token`, { credentials: "include" });
      csrfToken = c.headers.get("X-CSRF-Token");
    } catch (e) {
      console.error("[pu-widget] handshake failed", e);
    }
  }

  // ── Rendering ──
  function hideWelcome() { if (!started) { const w = root.querySelector("#pu-welcome"); if (w) w.remove(); started = true; } }

  function addUser(text) {
    hideWelcome();
    const el = document.createElement("div");
    el.className = "pu-msg user";
    el.innerHTML = `<div class="pu-bubble"></div>`;
    el.querySelector(".pu-bubble").textContent = text;
    $messages.appendChild(el);
    scroll();
  }

  function addTyping() {
    const el = document.createElement("div");
    el.className = "pu-msg agent";
    el.innerHTML = `<div class="pu-mava">A</div><div class="pu-mbody"><div class="pu-typing"><span></span><span></span><span></span></div></div>`;
    $messages.appendChild(el);
    scroll();
    return el;
  }

  function linkify(text) {
    const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    let html = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(https?:\/\/[^\s)]+|\/api\/v1\/cobranza\/certificate\/[A-Za-z0-9_\-]+\.pdf)/g, (m) => {
      const href = m.startsWith("http") ? m : `${API}${m}`;
      const file = m.split("/").pop();
      // Certificate PDFs render as a clear "downloadable document" chip.
      if (/\.pdf$/i.test(file)) {
        return `<a class="pu-doc-link" href="${href}" target="_blank" rel="noopener" download>${ICONS.download}<span>Descargar documento (${file})</span></a>`;
      }
      return `<a href="${href}" target="_blank" rel="noopener">${file}</a>`;
    });
    return html.replace(/\n/g, "<br>");
  }

  function _docChip(doc) {
    const href = doc.download_url.startsWith("http") ? doc.download_url : `${API}${doc.download_url}`;
    return `<a class="pu-doc-link" href="${href}" target="_blank" rel="noopener" download>${ICONS.download}<span>Descargar documento (${doc.filename})</span></a>`;
  }

  function fillAgent(el, text, chips, doc) {
    const body = el.querySelector(".pu-mbody");
    let inner = `<div class="pu-reply">${linkify(text)}`;
    // Render the download chip from the structured `document` field when the
    // reply text didn't already include the link (don't depend on LLM wording).
    if (doc && doc.download_url && !/pu-doc-link/.test(inner)) {
      inner += _docChip(doc);
    }
    inner += `</div>`;
    body.innerHTML = inner;
    if (chips && chips.length) {
      const c = document.createElement("div");
      c.className = "pu-chips";
      chips.forEach((opt) => {
        const b = document.createElement("button");
        b.textContent = opt;
        b.onclick = () => { $input.value = opt; submit(); };
        c.appendChild(b);
      });
      body.appendChild(c);
    }
    scroll();
  }

  function scroll() { requestAnimationFrame(() => { if ($messages) $messages.scrollTop = $messages.scrollHeight; }); }

  // ── Send ──
  async function submit() {
    const text = $input.value.trim();
    if (!text || busy) return;
    busy = true; $send.disabled = true;
    addUser(text);
    $input.value = ""; $input.style.height = "auto";
    const typingEl = addTyping();

    const body = {
      channel: "web", tenant_id: TENANT, text,
      visitor_id: visitorId, conversation_id: conversationId,
      campaign_token: CT || undefined,
    };
    try {
      const r = await fetch(`${API}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken || "", "X-Session-Token": sessionToken || "" },
        credentials: "include",
        body: JSON.stringify(body),
      });
      // Distinct, clear error states (Peruvian Spanish, "tú"). System styling,
      // NOT Ada's voice — a network/rate-limit failure isn't something she said.
      if (!r.ok) {
        showSystemError(typingEl, errorMessageForStatus(r.status));
        return;
      }
      const data = await r.json();
      const msg = data.message || {};
      conversationId = msg.conversation_id || conversationId;
      // Refresh the identity strip if the backend verified the user this turn
      // (e.g. after they typed their DNI mid-conversation).
      updateIdentityFromResponse(msg.identity);
      const chips = (msg.quick_replies && msg.quick_replies.buttons)
        ? msg.quick_replies.buttons.map((b) => b.label)
        : (Array.isArray(msg.suggested_replies) ? msg.suggested_replies : []);
      fillAgent(typingEl, msg.content || "Disculpa, no pude procesar eso.", chips, msg.document);
      // Contextual side panel: debt cards (consultar_deuda → ui_actions.panel).
      // The chat text still carries the curated message; the panel is additive.
      const panel = msg.ui_actions && msg.ui_actions.panel;
      if (panel && panel.type === "debt") renderDebtPanel(panel);
    } catch (e) {
      console.error("[pu-widget] send failed", e);
      showSystemError(typingEl, "Tuve un problema de conexión con el servicio. Inténtalo de nuevo.");
    } finally {
      busy = false; $send.disabled = false; $input.focus();
    }
  }

  function errorMessageForStatus(status) {
    if (status === 429) return "Estás yendo muy rápido, espera un momento e intenta de nuevo.";
    if (status === 401 || status === 403) return "Tu sesión expiró, recarga la página para seguir.";
    return "Tuve un problema de conexión con el servicio. Inténtalo de nuevo.";
  }

  // System/error notice: neutral style, NO Ada avatar (not her voice). Replaces
  // the pending agent typing bubble in place.
  function showSystemError(typingEl, text) {
    typingEl.className = "pu-msg system";
    typingEl.innerHTML = `<div class="pu-sysmsg">${ICONS.alert}<span></span></div>`;
    typingEl.querySelector("span").textContent = text;
    scroll();
  }

  // ── Comprobante upload (deterministic form → POST /api/v1/comprobante) ──
  // NOT LLM-orchestrated: the widget builds the multipart request itself, gets a
  // structured classification back, renders it in PrestamYpe styling, and injects
  // a one-line summary into the chat so the conversation reflects the action.
  let _cbBusy = false;

  // Shadow-wide query: the comprobante form may live in the side panel, so its
  // helpers search the whole shadow root (not just the chat root).
  function $q(sel) { return shadow.querySelector(sel); }
  function $qa(sel) { return shadow.querySelectorAll(sel); }

  // ── Side panel open/close + content renderers ──
  function openSidePanel(mode, title) {
    spMode = mode;
    if ($spTitle) $spTitle.textContent = title || ($spTitle.textContent);
    sidePanel.classList.add("pu-open");
  }

  function closeSidePanel() {
    sidePanel.classList.remove("pu-open");
    spMode = null;
  }

  // Render the debt cards into the side panel from a `panel` payload (built by
  // the backend `consultar_deuda` → ui_actions.panel). Pure presentation.
  function renderDebtPanel(panel) {
    if (!panel || !Array.isArray(panel.cards) || !panel.cards.length) return;
    openSidePanel("debt", panel.title || "Tu crédito");
    $spBody.innerHTML = panel.cards.map(_debtCardHtml).join("");
    scrollSidePanel();
  }

  const _BADGE_CLS = { aldia: "aldia", mora: "mora", libre: "libre" };

  function _debtCardHtml(c) {
    const badge = c.badge || { kind: "aldia", label: "Al día" };
    const badgeCls = _BADGE_CLS[badge.kind] || "aldia";
    const bank = c.banco ? `<div class="pu-card-bank">${escapeHtml(c.banco)}</div>` : "";
    const rows = [];
    if (c.next_installment_formatted) {
      rows.push(`<div class="pu-card-row"><span class="k">Próxima cuota</span><span class="v">${escapeHtml(c.next_installment_formatted)}</span></div>`);
    }
    if (c.next_due_date) {
      rows.push(`<div class="pu-card-row"><span class="k">Vence</span><span class="v">${escapeHtml(c.next_due_date)}</span></div>`);
    }
    if (c.cci_masked) {
      const acct = c.banco
        ? `${escapeHtml(c.cci_masked)} · ${escapeHtml(c.banco)}`
        : escapeHtml(c.cci_masked);
      rows.push(`<div class="pu-card-row"><span class="k">Cuenta para realizar el pago</span><span class="v">${acct}</span></div>`);
    }
    let grupal = "";
    if (c.is_grupal && Array.isArray(c.codeudores) && c.codeudores.length) {
      const cos = c.codeudores.map((g) =>
        `<div class="gco"><span>${escapeHtml(g.borrower_name || "")}</span><span class="grol">${escapeHtml(g.rol || "codeudor")}</span></div>`
      ).join("");
      grupal = `<div class="pu-card-grupal"><div class="gtitle">Crédito grupal · codeudores</div>${cos}</div>`;
    }
    return `
      <div class="pu-card">
        <div class="pu-card-top">
          <div>
            <div class="pu-card-loan">Crédito ${escapeHtml(c.loan_number || "")}</div>
            ${bank}
          </div>
          <span class="pu-card-badge ${badgeCls}">${escapeHtml(badge.label || "")}</span>
        </div>
        <div class="pu-card-balance-lbl">Saldo pendiente</div>
        <div class="pu-card-balance">${escapeHtml(c.balance_formatted || "")}</div>
        ${rows.length ? `<div class="pu-card-rows">${rows.join("")}</div>` : ""}
        ${grupal}
      </div>`;
  }

  function scrollSidePanel() { requestAnimationFrame(() => { if ($spBody) $spBody.scrollTop = 0; }); }

  // Comprobante upload — now rendered INTO the side panel (not a modal).
  function openComprobante() {
    openSidePanel("comprobante", "Subir comprobante de pago");
    $spBody.innerHTML = comprobanteFormHtml();
    _wireComprobanteForm();
    // Collapse the example + sync the label to the (reset) default account type.
    $q("#pu-cb-eg").setAttribute("hidden", "");
    const egBtn = $q("#pu-cb-eg-toggle");
    egBtn.setAttribute("aria-expanded", "false");
    egBtn.textContent = "Ver ejemplo de voucher";
    _cbOnAcctChange();
    setTimeout(() => { const f = $q("#pu-cb-file"); if (f) f.focus(); }, 80);
  }

  // Attach the form listeners after the comprobante form is injected.
  function _wireComprobanteForm() {
    $q("#pu-cb-cancel").addEventListener("click", closeComprobante);
    $q("#pu-cb-form").addEventListener("submit", (e) => { e.preventDefault(); submitComprobante(); });
    ["#pu-cb-cci", "#pu-cb-monto", "#pu-cb-op", "#pu-cb-file"].forEach((sel) => {
      const el = $q(sel);
      el.addEventListener("input", _cbValidate);
      el.addEventListener("change", _cbValidate);
    });
    $qa('input[name="pu-cb-acct"]').forEach((r) => r.addEventListener("change", _cbOnAcctChange));
    $q("#pu-cb-eg-toggle").addEventListener("click", _cbToggleExample);
  }

  function closeComprobante() { closeSidePanel(); }

  // ── Account-type selector ──────────────────────────────────────────────
  // CCI = 20 dígitos exactos (entre bancos). Número de cuenta = más corto y
  // flexible (8–20 dígitos, mismo banco). El selector decide qué validar.
  function _cbAcctType() {
    const r = $q('input[name="pu-cb-acct"]:checked');
    return r ? r.value : "cuenta";
  }

  function _cbOnAcctChange() {
    const type = _cbAcctType();
    const lbl = $q("#pu-cb-cci-lbl");
    const inp = $q("#pu-cb-cci");
    if (type === "cci") {
      lbl.textContent = "CCI destino (20 dígitos)";
      inp.placeholder = "00000000000000000000";
      inp.maxLength = 20;
    } else {
      lbl.textContent = "Número de cuenta destino";
      inp.placeholder = "Ej. 1320268376";
      inp.maxLength = 20;
    }
    _cbValidate();
  }

  function _cbToggleExample() {
    const eg = $q("#pu-cb-eg");
    const btn = $q("#pu-cb-eg-toggle");
    const show = eg.hasAttribute("hidden");
    if (show) { eg.removeAttribute("hidden"); } else { eg.setAttribute("hidden", ""); }
    btn.setAttribute("aria-expanded", String(show));
    btn.textContent = show ? "Ocultar ejemplo" : "Ver ejemplo de voucher";
  }

  // ── Live FORMAT validation (no business logic): toggles the submit button
  // and renders inline format hints in PrestamYpe green/red. ──
  function _cbDigits(el, max) {
    // Keep only digits, cap at max.
    const cleaned = el.value.replace(/\D/g, "").slice(0, max);
    if (cleaned !== el.value) el.value = cleaned;
    return cleaned;
  }

  function _cbValidate() {
    const type = _cbAcctType();
    const cciEl = $q("#pu-cb-cci");
    const montoEl = $q("#pu-cb-monto");
    const opEl = $q("#pu-cb-op");
    const fileEl = $q("#pu-cb-file");
    const submitBtn = $q("#pu-cb-submit");
    const cciHint = $q("#pu-cb-cci-hint");

    const cuenta = _cbDigits(cciEl, 20);
    const monto = Number(montoEl.value);
    const op = opEl.value.trim();
    const file = fileEl.files && fileEl.files[0];

    // Length rule depends on the chosen account type.
    const cuentaOk = type === "cci"
      ? cuenta.length === 20
      : (cuenta.length >= 8 && cuenta.length <= 20);
    const montoOk = montoEl.value !== "" && monto > 0;
    const opOk = op.length > 0 && op.length <= 30;
    const fileOk = !!file && file.size <= 8 * 1024 * 1024;

    // Inline hint: only nag once the user has typed something.
    if (cuenta.length === 0) {
      cciHint.textContent = ""; cciHint.className = "pu-cb-fieldhint";
      cciEl.classList.remove("pu-invalid");
    } else if (cuentaOk) {
      cciHint.textContent = type === "cci" ? "Listo, 20 dígitos." : `Listo (${cuenta.length} dígitos).`;
      cciHint.className = "pu-cb-fieldhint ok";
      cciEl.classList.remove("pu-invalid");
    } else if (type === "cci") {
      cciHint.textContent = `El CCI necesita 20 dígitos (${cuenta.length}/20).`;
      cciHint.className = "pu-cb-fieldhint err";
      cciEl.classList.add("pu-invalid");
    } else {
      cciHint.textContent = `El número de cuenta debe tener entre 8 y 20 dígitos (${cuenta.length}).`;
      cciHint.className = "pu-cb-fieldhint err";
      cciEl.classList.add("pu-invalid");
    }

    submitBtn.disabled = !(cuentaOk && montoOk && opOk && fileOk);
    return cuentaOk && montoOk && opOk && fileOk;
  }

  function _cbError(text) {
    const e = $q("#pu-cb-err"); if (e) e.textContent = text;
  }

  function _cbShowResult(kind, html) {
    const res = $q("#pu-cb-result");
    if (!res) return;
    res.className = `pu-cb-result ${kind}`;
    res.innerHTML = html;
    res.style.display = "block";
  }

  const _TIPO_LABEL = { pago: "Pago", abono: "Abono", cancelacion: "Cancelación" };

  async function submitComprobante() {
    if (_cbBusy) return;
    _cbError("");
    if (!verifiedDni) { _cbError("Necesitamos identificarte antes de subir un comprobante."); return; }

    const fileEl = $q("#pu-cb-file");
    const acctType = _cbAcctType();
    const cuenta = $q("#pu-cb-cci").value.replace(/\D/g, "");
    const monto = $q("#pu-cb-monto").value;
    const op = $q("#pu-cb-op").value.trim();
    const file = fileEl.files && fileEl.files[0];

    if (!file) { _cbError("Adjunta la imagen o PDF del comprobante."); return; }
    if (file.size > 8 * 1024 * 1024) { _cbError("El archivo supera 8 MB."); return; }
    if (acctType === "cci") {
      if (cuenta.length !== 20) { _cbError("El CCI debe tener exactamente 20 dígitos."); return; }
    } else if (cuenta.length < 8 || cuenta.length > 20) {
      _cbError("El número de cuenta debe tener entre 8 y 20 dígitos."); return;
    }
    if (!monto || Number(monto) <= 0) { _cbError("Indica el monto transferido."); return; }
    if (!op) { _cbError("Indica el número de operación."); return; }
    if (op.length > 30) { _cbError("El número de operación es demasiado largo."); return; }

    const fd = new FormData();
    fd.append("tenant_id", TENANT);
    fd.append("dni", verifiedDni);
    fd.append("account_type", acctType);
    fd.append("cuenta_destino", cuenta);
    fd.append("monto", monto);
    fd.append("nro_operacion", op);
    fd.append("file", file);

    _cbBusy = true;
    const submitBtn = $q("#pu-cb-submit");
    submitBtn.disabled = true; submitBtn.textContent = "Enviando…";
    try {
      const r = await fetch(`${API}/api/v1/comprobante`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken || "", "X-Session-Token": sessionToken || "" },
        credentials: "include",
        body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        _cbShowResult("err", `<strong>No pudimos registrarlo.</strong> ${escapeHtml(data.detail || "Inténtalo de nuevo.")}`);
        return;
      }
      renderComprobanteResult(data, cuenta, acctType);
    } catch (e) {
      console.error("[pu-widget] comprobante upload failed", e);
      _cbShowResult("err", "<strong>Problema de conexión.</strong> Inténtalo de nuevo.");
    } finally {
      _cbBusy = false; submitBtn.disabled = false; submitBtn.textContent = "Enviar comprobante";
    }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  const _ACCT_LABEL = { cci: "CCI", cuenta: "Número de cuenta" };

  function renderComprobanteResult(data, cuenta, acctType) {
    const last4 = String(cuenta || "").slice(-4);
    const acctLabel = _ACCT_LABEL[data.account_type || acctType] || "cuenta";
    const tipo = _TIPO_LABEL[data.tipo] || (data.tipo || "").toUpperCase();
    const credito = escapeHtml(data.credito || "");
    if (data.dedup_ok === false) {
      // Duplicate: warn (orange), don't double-register.
      _cbShowResult("warn", `<strong>Comprobante duplicado.</strong> ${escapeHtml(data.mensaje || "Ya recibimos este comprobante antes.")}`);
      injectChatSummary(`Este comprobante ya lo teníamos registrado para tu crédito ${data.credito || ""}.`);
      return;
    }
    // Success: green, with the classification.
    _cbShowResult("ok",
      `✓ <strong>Recibimos tu comprobante de pago.</strong> Lo registramos como <strong>${escapeHtml(tipo)}</strong> ` +
      `sobre tu crédito ${credito}, ${escapeHtml(acctLabel)} ···${escapeHtml(last4)}. Será validado y, de estar conforme, se aplicará a tu cuenta.`);
    injectChatSummary(`Recibimos tu comprobante de pago. Lo registramos como ${tipo} sobre tu crédito ${data.credito || ""}, ${acctLabel} ···${last4}. Será validado y, de estar conforme, se aplicará a tu cuenta.`);
    setTimeout(closeComprobante, 2600);
  }

  // Inject a one-line agent summary into the conversation so the chat reflects
  // the deterministic upload (it didn't go through the LLM turn).
  function injectChatSummary(text) {
    hideWelcome();
    const el = document.createElement("div");
    el.className = "pu-msg agent";
    el.innerHTML = `<div class="pu-mava">${AGENT.charAt(0).toUpperCase()}</div><div class="pu-mbody"></div>`;
    $messages.appendChild(el);
    fillAgent(el, text, null);
  }

  // ── Tenant branding ── apply fetched brand to colors/name/logo/footer.
  function applyBranding(b) {
    branding = b;
    AGENT = b.agent_name || AGENT;
    COMPANY = b.name || COMPANY;
    const color = b.primary_color || "#0083E0";
    // Override the widget's brand CSS vars on the two scoped roots.
    [fab, root].forEach((node) => {
      if (!node) return;
      node.style.setProperty("--pu-brand", color);
      node.style.setProperty("--pu-brand-hover", color);
      node.style.setProperty("--pu-brand-soft", _tintToWhite(color, 0.86));
    });
    const hname = root && root.querySelector("#pu-hname");
    if (hname) hname.textContent = `${AGENT} · ${COMPANY}`;
    const av = root && root.querySelector(".pu-avatar");
    if (av) av.textContent = AGENT.charAt(0).toUpperCase();
    if (fab) fab.setAttribute("aria-label", `Abrir chat de ${COMPANY}`);
  }

  // Mix a hex color toward white (ratio 0..1 = how much white) for soft tints.
  function _tintToWhite(hex, ratio) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#C5E4F9";
    const n = parseInt(m[1], 16);
    const mix = (c) => Math.round(c + (255 - c) * ratio);
    return `rgb(${mix((n >> 16) & 255)},${mix((n >> 8) & 255)},${mix(n & 255)})`;
  }

  async function loadBranding() {
    if (TENANT === "prestaunion") return;  // native theme, no fetch
    try {
      const r = await fetch(`${API}/api/v1/tenant/${TENANT}/branding`);
      if (r.ok) applyBranding(await r.json());
    } catch (e) {
      console.error("[pu-widget] branding fetch failed", e);
    }
  }

  // ── Demo seeding (?demo=open) — opens the panel with sample messages so the
  //    widget can be previewed without an LLM key. Pure presentation. ──
  const SEEDS = {
    "demo-maria": [
      ["user", "¿Tengo deuda pendiente?"],
      ["agent", "Buenas noticias, María. Tu préstamo <strong>PYPE-2023-00088</strong> figura <strong>cancelado</strong> y tu saldo es <strong>S/ 0.00</strong>. No mantienes deuda con PrestaUnion.\n\n¿Quieres que te emita tu <strong>certificado de no adeudo</strong> en PDF?", ["Sí, emitir certificado", "Poner un reclamo"]],
    ],
    "demo-juan": [
      ["user", "¿Cuánto debo?"],
      ["agent", "Hola Juan. Tu préstamo <strong>PYPE-2024-00123</strong> está <strong>al día</strong>. Saldo pendiente: <strong>S/ 4,850.00</strong> (3 de 12 cuotas). Tu próxima cuota es de <strong>S/ 1,650.00</strong> el <strong>15/06/2026</strong>.", ["Ver medios de pago", "Poner un reclamo"]],
    ],
    "demo-carlos": [
      ["user", "¿Cuál es mi saldo?"],
      ["agent", "Carlos, tu préstamo <strong>PYPE-2024-00210</strong> está <strong>en mora</strong>. Saldo: <strong>S/ 2,300.00</strong>, con una cuota vencida hace <strong>8 días</strong> y un recargo de <strong>S/ 85.00</strong>. Regularizarla detiene el recargo. ¿Te muestro cómo pagar?", ["¿Cómo pago?", "Hablar con un asesor"]],
    ],
    "cold": [
      ["user", "¿Cuánto debo?"],
      ["agent", "Con gusto te ayudo. Para ver la información de tu préstamo, primero necesito identificarte. ¿Me indicas tu <strong>número de DNI</strong> (8 dígitos), por favor?", ["Ingresar mi DNI", "Hablar con un asesor"]],
    ],
  };

  function seedDemo() {
    setOpen(true);
    hideWelcome();
    const seeds = SEEDS[CT] || SEEDS.cold;
    seeds.forEach(([role, text, chips]) => {
      if (role === "user") {
        const el = document.createElement("div");
        el.className = "pu-msg user";
        el.innerHTML = `<div class="pu-bubble"></div>`;
        el.querySelector(".pu-bubble").textContent = text;
        $messages.appendChild(el);
      } else {
        const el = document.createElement("div");
        el.className = "pu-msg agent";
        el.innerHTML = `<div class="pu-mava">A</div><div class="pu-mbody"></div>`;
        $messages.appendChild(el);
        fillAgent(el, "", chips);
        el.querySelector(".pu-reply").innerHTML = text; // pre-formatted sample
      }
    });
    scroll();
  }

  // ── Boot ──
  function init(opts) {
    _resolveConfig(opts);
    ensureInterFont();
    injectStyles();
    build();
    handshake();
    // Non-default tenant: fetch branding, then refresh the welcome (header/avatar
    // were re-skinned by applyBranding; the welcome uses AGENT/COMPANY too).
    loadBranding().then(() => { if (TENANT !== "prestaunion" && !started) renderWelcome(); });
    const wantDemo = (opts && opts.demo === "open") || qs.get("demo") === "open";
    if (wantDemo) setTimeout(seedDemo, 60);
  }

  // ── Public mount API ──
  // mount(opts): render the widget into a Shadow DOM. Idempotent — a second
  // call is a no-op (so including the snippet twice never double-mounts).
  //   opts.shadowRoot — an existing ShadowRoot to render into (embed.js passes
  //     one created on a host the client controls). If absent, the widget makes
  //     its OWN host <div> + shadow root and appends it to <body> (landing demo).
  //   opts.api / opts.ct / opts.tenant / opts.demo — config overrides.
  let _mounted = false;
  function mount(opts) {
    if (_mounted) return;
    _mounted = true;
    opts = opts || {};
    if (opts.shadowRoot) {
      shadow = opts.shadowRoot;
    } else {
      const host = document.createElement("div");
      host.id = "pu-widget-host";
      // The host is a 0-size anchor; the FAB/panel inside are position:fixed.
      host.style.cssText = "position:fixed;z-index:2147483000;width:0;height:0;";
      document.body.appendChild(host);
      shadow = host.attachShadow({ mode: "open" });
    }
    init(opts);
  }

  // Expose the mount API for the external loader (embed.js).
  window.PubotWidget = window.PubotWidget || { mount };

  // ── Auto-mount (same-origin landing) ──
  // Skipped when data-no-automount is set (embed.js sets it: it owns the host +
  // shadow and calls mount() itself). The landing demo includes this file with
  // a plain <script src="widget.js"> → auto-mount with its own Shadow DOM.
  const _noAutomount = scriptEl && scriptEl.dataset && "noAutomount" in scriptEl.dataset;
  if (!_noAutomount) {
    const _auto = () => mount();
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", _auto);
    } else {
      _auto();
    }
  }
})();
