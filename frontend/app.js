/* PrestaUnion demo chat widget — talks to the cobranza backend.
 * Handshake: GET session-token + csrf-token, then POST /api/v1/chat with the
 * demo campaign_token read from ?ct=. Dark+gold Vox theme. Datos ficticios. */
(() => {
  "use strict";

  // Backend base URL: same origin by default; override with ?api=http://host:port
  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || location.origin).replace(/\/$/, "");
  const CT = params.get("ct") || null;           // demo-juan | demo-carlos | demo-maria
  const TENANT = "prestaunion";

  const PROFILES = {
    "demo-juan":   { who: "Juan Pérez Rojas",     biz: "Bodega Don Juan E.I.R.L.",       badge: "aldia", label: "Al día" },
    "demo-carlos": { who: "Carlos Huamán Flores",  biz: "Ferretería El Tornillo S.A.C.",  badge: "mora",  label: "En mora" },
    "demo-maria":  { who: "María Quispe Mamani",    biz: "Textiles María E.I.R.L.",        badge: "libre", label: "Sin deuda" },
  };

  const CHIPS = {
    "demo-juan":   ["¿Cuánto debo?", "¿Cuándo vence mi próxima cuota?", "Quiero poner un reclamo"],
    "demo-carlos": ["¿Cuál es mi saldo?", "Estoy en mora, ¿qué hago?", "¿Cómo pago?"],
    "demo-maria":  ["¿Tengo deuda pendiente?", "Quiero mi certificado de no adeudo", "Poner un reclamo"],
    "cold":        ["¿Cómo pago mi cuota?", "¿Qué es la TCEA?", "Hablar con un asesor"],
  };

  const visitorId = crypto.randomUUID();
  let conversationId = null;
  let csrfToken = null;
  let sessionToken = null;
  let busy = false;

  const $messages = document.getElementById("messages");
  const $welcome = document.getElementById("welcome");
  const $welcomeSub = document.getElementById("welcome-sub");
  const $welcomeChips = document.getElementById("welcome-chips");
  const $identCard = document.getElementById("ident-card");
  const $form = document.getElementById("chat-form");
  const $input = document.getElementById("chat-input");
  const $send = document.getElementById("send-btn");
  const $wrap = document.getElementById("messages-wrap");

  // ── Identity card + welcome chips ──
  function renderIdentity() {
    const p = CT && PROFILES[CT];
    if (p) {
      $identCard.className = "ident-card";
      $identCard.innerHTML = `
        <div class="ident-left">
          <span class="who">${p.who}</span>
          <span class="biz">${p.biz}</span>
        </div>
        <span class="ident-badge ${p.badge}">${p.label}</span>`;
      $welcomeSub.textContent = `Estás identificado como ${p.who.split(" ")[0]}. Puedo consultar tu préstamo, registrar un reclamo o gestionar tu certificado.`;
    } else {
      $identCard.className = "ident-card cold";
      $identCard.innerHTML = `
        <div class="ident-left">
          <span class="who">Sesión no identificada</span>
          <span class="biz">Ingresá por tu enlace seguro para ver tu préstamo.</span>
        </div>
        <span class="ident-badge cold">Sin verificar</span>`;
    }
    const chips = CHIPS[CT] || CHIPS.cold;
    $welcomeChips.innerHTML = "";
    chips.forEach((c) => {
      const b = document.createElement("button");
      b.textContent = c;
      b.onclick = () => { $input.value = c; submit(); };
      $welcomeChips.appendChild(b);
    });
  }

  // ── Security handshake ──
  async function handshake() {
    try {
      const s = await fetch(`${API}/api/v1/security/session-token?visitor_id=${visitorId}`);
      sessionToken = (await s.json()).token;
      const c = await fetch(`${API}/api/v1/security/csrf-token`, { credentials: "include" });
      csrfToken = c.headers.get("X-CSRF-Token");
    } catch (e) {
      console.error("handshake failed", e);
    }
  }

  // ── Rendering ──
  function hideWelcome() { if ($welcome) $welcome.classList.add("hidden"); }

  function addUser(text) {
    hideWelcome();
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="bubble"></div>`;
    el.querySelector(".bubble").textContent = text;
    $messages.appendChild(el);
    scroll();
  }

  function addTyping() {
    const el = document.createElement("div");
    el.className = "msg agent";
    el.innerHTML = `<div class="ava">A</div><div class="body"><div class="typing"><span></span><span></span><span></span></div></div>`;
    $messages.appendChild(el);
    scroll();
    return el;
  }

  function linkify(text) {
    const escaped = text
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    // bold **x**
    let html = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // URLs (incl. relative /api/... certificate links) → anchors
    html = html.replace(/(https?:\/\/[^\s)]+|\/api\/v1\/cobranza\/certificate\/[A-Za-z0-9_\-]+\.pdf)/g, (m) => {
      const href = m.startsWith("http") ? m : `${API}${m}`;
      return `<a href="${href}" target="_blank" rel="noopener">${m.split("/").pop()}</a>`;
    });
    return html.replace(/\n/g, "<br>");
  }

  function fillAgent(el, text, chips) {
    const body = el.querySelector(".body");
    body.innerHTML = `<div class="reply">${linkify(text)}</div>`;
    if (chips && chips.length) {
      const c = document.createElement("div");
      c.className = "chips";
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

  function scroll() { requestAnimationFrame(() => { $wrap.scrollTop = $wrap.scrollHeight; }); }

  // ── Send ──
  async function submit() {
    const text = $input.value.trim();
    if (!text || busy) return;
    busy = true; $send.disabled = true;
    addUser(text);
    $input.value = "";
    const typingEl = addTyping();

    const body = {
      channel: "web",
      tenant_id: TENANT,
      text,
      visitor_id: visitorId,
      conversation_id: conversationId,
      campaign_token: CT || undefined,
    };

    try {
      const r = await fetch(`${API}/api/v1/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken || "",
          "X-Session-Token": sessionToken || "",
        },
        credentials: "include",
        body: JSON.stringify(body),
      });
      const data = await r.json();
      const msg = data.message || {};
      conversationId = msg.conversation_id || conversationId;
      const chips = (msg.quick_replies && msg.quick_replies.buttons)
        ? msg.quick_replies.buttons.map((b) => b.label)
        : (Array.isArray(msg.suggested_replies) ? msg.suggested_replies : []);
      fillAgent(typingEl, msg.content || "Disculpá, no pude procesar eso.", chips);
    } catch (e) {
      console.error(e);
      fillAgent(typingEl, "Tuve un problema de conexión con el servicio. Intentá de nuevo.", []);
    } finally {
      busy = false; $send.disabled = false; $input.focus();
    }
  }

  $form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  $input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  $input.addEventListener("input", () => {
    $input.style.height = "auto"; $input.style.height = Math.min($input.scrollHeight, 140) + "px";
  });

  renderIdentity();
  handshake();
})();
