/* PrestaUnion — embeddable floating chat widget (Vox light theme).
 *
 * Drop-in: include <script src="widget.js"></script> on any page. It injects
 * its own styles + a Floating Action Button (FAB) bottom-right that opens a
 * floating chat panel (Nexo-style behavior, Vox light theming).
 *
 * Talks to the cobranza backend: security handshake (session + CSRF), then
 * POST /api/v1/chat with the demo campaign_token read from ?ct=. The hard
 * identity gate lives server-side; this is just the chat surface.
 *
 * Config via the script tag's data-* attributes or query params:
 *   data-api   | ?api=  → backend base URL override (default: auto)
 *   data-ct    | ?ct=   → demo token: demo-juan | demo-carlos | demo-maria
 *
 * BASE PATH (reverse proxy): all API URLs are built against a base derived
 * from the URL of THIS script. Served at /widget.js → base "" (local); served
 * at /pubot-gj5w2a0p/widget.js (behind Traefik strip-prefix) → base
 * "https://host/pubot-gj5w2a0p". Works in both without code changes. An
 * explicit data-api / ?api= still wins (e.g. external embed on another origin).
 */
(() => {
  "use strict";

  // ── Config resolution ──
  const scriptEl = document.currentScript;
  const qs = new URLSearchParams(location.search);

  function _deriveApiBase() {
    // 1) explicit override (external embed)
    const override = (scriptEl && scriptEl.dataset.api) || qs.get("api");
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

  const API = _deriveApiBase();
  const CT = (scriptEl && scriptEl.dataset.ct) || qs.get("ct") || null;
  // Tenant-aware: ?tenant=<slug> selects which tenant the widget talks to and
  // skins for. Default "prestaunion" keeps the original Vox theme untouched.
  const TENANT = ((scriptEl && scriptEl.dataset.tenant) || qs.get("tenant") || "prestaunion")
    .replace(/[^a-z0-9_-]/gi, "");

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
  const PRESTAMYPE_TOKEN_DNI = { "demo-1": "10052986", "demo-2": "04065803", "demo-3": "10061867" };

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

  /* Overlay + panel sit ABSOLUTE inside the widget root (not the page). */
  #pu-widget-root .pu-modal-ov { position: absolute; inset: 0; background: rgba(10,13,18,0.32);
    display: none; align-items: flex-end; z-index: 10; }
  #pu-widget-root .pu-modal-ov.show { display: flex; }
  #pu-widget-root .pu-modal { width: 100%; background: #fff; border-radius: 12px 12px 0 0;
    padding: 16px; box-shadow: 0 -8px 30px -10px rgba(10,13,18,0.25); max-height: 92%; overflow-y: auto;
    animation: pu-slideup .22s ease-out; }
  @keyframes pu-slideup { from { transform: translateY(24px); opacity: .6; } to { transform: translateY(0); opacity: 1; } }
  #pu-widget-root .pu-modal h3 { margin: 0 0 2px; font-size: 16px; font-weight: 800; color: #181d27; }
  #pu-widget-root .pu-modal .pu-msub { margin: 0 0 14px; font-size: 12.5px; color: #535862; line-height: 1.4; }
  #pu-widget-root .pu-field { margin-bottom: 11px; }
  #pu-widget-root .pu-field label { display: block; font-size: 12px; font-weight: 600; color: #414651; margin-bottom: 5px; }
  #pu-widget-root .pu-field input {
    width: 100%; font-size: 14px; color: #181d27; padding: 10px 12px; min-height: 44px;
    border: 1px solid #d5d7da; border-radius: 8px; background: #fff; outline: 0; transition: border-color .15s, box-shadow .15s; }
  #pu-widget-root .pu-field input:focus { border-color: var(--pu-brand); box-shadow: 0 0 0 3px var(--pu-brand-soft); }
  #pu-widget-root .pu-field input[type=file] { padding: 9px 12px; font-size: 12.5px; }
  #pu-widget-root .pu-field .pu-row2 { display: flex; gap: 10px; }
  #pu-widget-root .pu-field .pu-row2 > div { flex: 1; }
  #pu-widget-root .pu-modal-acts { display: flex; gap: 8px; margin-top: 6px; }
  #pu-widget-root .pu-btn-primary { flex: 1; min-height: 44px; border: 0; border-radius: 8px; cursor: pointer;
    background: var(--pu-brand); color: #fff; font-size: 14px; font-weight: 600; box-shadow: 0 1px 2px 0 rgba(10,13,18,.05); transition: filter .15s; }
  #pu-widget-root .pu-btn-primary:hover { filter: brightness(0.96); }
  #pu-widget-root .pu-btn-primary:disabled { opacity: .55; cursor: not-allowed; }
  #pu-widget-root .pu-btn-ghost { min-height: 44px; padding: 0 16px; border: 1px solid #d5d7da; border-radius: 8px;
    background: #fff; color: #414651; font-size: 14px; font-weight: 600; cursor: pointer; }
  #pu-widget-root .pu-cb-err { font-size: 12px; color: #d92d20; margin: 4px 0 8px; min-height: 0; }
  /* Inline result chips (success / warn / error) shown inside the panel. */
  #pu-widget-root .pu-cb-result { font-size: 13px; line-height: 1.5; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }
  #pu-widget-root .pu-cb-result.ok   { background: #ecfff6; border: 1px solid #b3ffdf; color: #079455; }
  #pu-widget-root .pu-cb-result.warn { background: #fffaeb; border: 1px solid #fdb022; color: #b54708; }
  #pu-widget-root .pu-cb-result.err  { background: #fef3f2; border: 1px solid #f04438; color: #d92d20; }
  #pu-widget-root .pu-cb-result strong { font-weight: 800; }

  /* Mobile: near fullscreen */
  @media (max-width: 480px) {
    #pu-widget-root { right: 0; bottom: 0; width: 100vw; height: 100dvh; max-height: 100dvh; border-radius: 16px 16px 0 0; border-bottom: 0; }
    #pu-fab { right: 16px; bottom: 16px; }
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
    if (document.getElementById("pu-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "pu-widget-styles";
    style.textContent = CSS;
    document.head.appendChild(style);
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
  };

  // Display name for header/welcome. Defaults match the static prestaunion look;
  // a branding fetch (non-default tenant) overrides AGENT + COMPANY at runtime.
  let AGENT = "Ada";
  let COMPANY = "PrestaUnion";

  // Verified DNI for the comprobante upload. Set from the token profile or from
  // a backend identity response (DNI typed mid-chat). Null = upload locked.
  let verifiedDni = null;

  let fab, root, $messages, $form, $input, $send;

  function build() {
    // FAB
    fab = document.createElement("button");
    fab.id = "pu-fab";
    fab.setAttribute("aria-label", "Abrir chat de PrestaUnion");
    fab.innerHTML = `${ICONS.chat}${ICONS.close}<span class="pu-badge"></span>`;
    fab.addEventListener("click", toggle);
    document.body.appendChild(fab);

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
      </div>
      <div class="pu-modal-ov" id="pu-modal-ov">
        <div class="pu-modal" role="dialog" aria-modal="true" aria-label="Subir comprobante de pago">
          <h3>Subir comprobante de pago</h3>
          <p class="pu-msub">Sube tu constancia de transferencia. Validamos la cuenta y la clasificamos al instante; queda en revisión.</p>
          <div class="pu-cb-result" id="pu-cb-result" style="display:none;"></div>
          <form id="pu-cb-form">
            <div class="pu-field">
              <label for="pu-cb-file">Imagen o PDF del comprobante</label>
              <input type="file" id="pu-cb-file" accept="image/jpeg,image/png,application/pdf" required />
            </div>
            <div class="pu-field">
              <label for="pu-cb-cci">CCI destino (20 dígitos)</label>
              <input type="text" id="pu-cb-cci" inputmode="numeric" autocomplete="off" placeholder="00000000000000000000" required />
            </div>
            <div class="pu-field">
              <div class="pu-row2">
                <div>
                  <label for="pu-cb-monto">Monto</label>
                  <input type="number" id="pu-cb-monto" step="0.01" min="0.01" placeholder="0.00" required />
                </div>
                <div>
                  <label for="pu-cb-op">Nº de operación</label>
                  <input type="text" id="pu-cb-op" autocomplete="off" placeholder="Ej. 0012345" required />
                </div>
              </div>
            </div>
            <div class="pu-field">
              <label for="pu-cb-fecha">Fecha (opcional)</label>
              <input type="date" id="pu-cb-fecha" />
            </div>
            <div class="pu-cb-err" id="pu-cb-err"></div>
            <div class="pu-modal-acts">
              <button type="button" class="pu-btn-ghost" id="pu-cb-cancel">Cancelar</button>
              <button type="submit" class="pu-btn-primary" id="pu-cb-submit">Enviar comprobante</button>
            </div>
          </form>
        </div>
      </div>`;
    document.body.appendChild(root);

    $messages = root.querySelector("#pu-messages");
    $form = root.querySelector("#pu-form");
    $input = root.querySelector("#pu-input");
    $send = root.querySelector("#pu-send");

    root.querySelector("#pu-min").addEventListener("click", () => setOpen(false));
    root.querySelector("#pu-reset").addEventListener("click", requestReset);
    root.querySelector("#pu-comprobante-btn").addEventListener("click", openComprobante);
    root.querySelector("#pu-cb-cancel").addEventListener("click", closeComprobante);
    root.querySelector("#pu-cb-form").addEventListener("submit", (e) => { e.preventDefault(); submitComprobante(); });
    root.querySelector("#pu-modal-ov").addEventListener("click", (e) => { if (e.target.id === "pu-modal-ov") closeComprobante(); });
    $form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
    $input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } });
    $input.addEventListener("input", () => {
      $input.style.height = "auto"; $input.style.height = Math.min($input.scrollHeight, 110) + "px";
      maybeDniHint();
    });
    // Esc closes the panel (keyboard-discoverable, not mouse-only).
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) setOpen(false); });

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

  function openComprobante() {
    const ov = root.querySelector("#pu-modal-ov");
    root.querySelector("#pu-cb-err").textContent = "";
    const res = root.querySelector("#pu-cb-result");
    res.style.display = "none"; res.textContent = "";
    ov.classList.add("show");
    setTimeout(() => root.querySelector("#pu-cb-file").focus(), 80);
  }

  function closeComprobante() {
    root.querySelector("#pu-modal-ov").classList.remove("show");
  }

  function _cbError(text) {
    root.querySelector("#pu-cb-err").textContent = text;
  }

  function _cbShowResult(kind, html) {
    const res = root.querySelector("#pu-cb-result");
    res.className = `pu-cb-result ${kind}`;
    res.innerHTML = html;
    res.style.display = "block";
  }

  const _TIPO_LABEL = { pago: "PAGO", abono: "ABONO", cancelacion: "CANCELACIÓN" };

  async function submitComprobante() {
    if (_cbBusy) return;
    _cbError("");
    if (!verifiedDni) { _cbError("Necesitamos identificarte antes de subir un comprobante."); return; }

    const fileEl = root.querySelector("#pu-cb-file");
    const cci = root.querySelector("#pu-cb-cci").value.replace(/\D/g, "");
    const monto = root.querySelector("#pu-cb-monto").value;
    const op = root.querySelector("#pu-cb-op").value.trim();
    const file = fileEl.files && fileEl.files[0];

    if (!file) { _cbError("Adjunta la imagen o PDF del comprobante."); return; }
    if (file.size > 8 * 1024 * 1024) { _cbError("El archivo supera 8 MB."); return; }
    if (cci.length < 10) { _cbError("El CCI debe tener 20 dígitos."); return; }
    if (!monto || Number(monto) <= 0) { _cbError("Indica el monto transferido."); return; }
    if (!op) { _cbError("Indica el número de operación."); return; }

    const fd = new FormData();
    fd.append("tenant_id", TENANT);
    fd.append("dni", verifiedDni);
    fd.append("cci", cci);
    fd.append("monto", monto);
    fd.append("nro_operacion", op);
    fd.append("file", file);

    _cbBusy = true;
    const submitBtn = root.querySelector("#pu-cb-submit");
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
      renderComprobanteResult(data, cci);
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

  function renderComprobanteResult(data, cci) {
    const last4 = cci.slice(-4);
    if (data.cuenta_valida === false) {
      // Wrong CCI (or no credit): red error with the backend message.
      _cbShowResult("err", `<strong>Cuenta no válida.</strong> ${escapeHtml(data.mensaje || "Esa cuenta no corresponde a tu crédito.")}`);
      injectChatSummary(`No pude validar tu comprobante: ${data.mensaje || "la cuenta no corresponde a tu crédito."}`);
      return;
    }
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
      `✓ <strong>Comprobante recibido.</strong> Clasificado como <strong>${escapeHtml(tipo)}</strong> ` +
      `sobre tu crédito ${credito}, cuenta CCI ···${escapeHtml(last4)}. Queda en revisión.`);
    injectChatSummary(`Recibí tu comprobante. Lo clasifiqué como ${tipo} sobre tu crédito ${data.credito || ""} (cuenta ···${last4}) y queda en revisión.`);
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
  function init() {
    ensureInterFont();
    injectStyles();
    build();
    handshake();
    // Non-default tenant: fetch branding, then refresh the welcome (header/avatar
    // were re-skinned by applyBranding; the welcome uses AGENT/COMPANY too).
    loadBranding().then(() => { if (TENANT !== "prestaunion" && !started) renderWelcome(); });
    if (qs.get("demo") === "open") setTimeout(seedDemo, 60);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
