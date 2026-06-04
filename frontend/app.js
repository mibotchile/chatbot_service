(() => {
  "use strict";
  const qs = new URLSearchParams(location.search);
  const TENANT = (window.__TENANT__ || qs.get("tenant") || "prestaunion").replace(/[^a-z0-9_-]/gi, "");

  // Derive the API base from this page's own path (works under a proxy prefix).
  const API = (() => {
    const path = location.pathname.replace(/\/[^/]*$/, ""); // strip /index.html
    return (location.origin + path).replace(/\/$/, "");
  })();

  const $ = (id) => document.getElementById(id);

  function applyBrand(b) {
    const color = b.primary_color || "#0083E0";
    const root = document.documentElement.style;

    // PrestamYpe-style minimalist tenants opt out of the rich landing via
    // show_demo_cards=false + a cases table. The layout class is also set as
    // a first-paint hint (THEME_HINTS) so this is idempotent / FOUC-free.
    if (TENANT === "prestamype") document.body.classList.add("tenant-prestamype");

    // The whole page reads brand color from these CSS vars.
    root.setProperty("--vox-brand", color);
    root.setProperty("--vox-brand-hover", color);
    root.setProperty("--vox-brand-soft", _tint(color, 0.12));

    document.title = `${b.name} · Portal de clientes`;

    // Header: logo image if provided, else the tenant name wordmark.
    const wm = $("wordmark");
    if (b.logo_url) {
      wm.innerHTML = `<img class="logo" src="${b.logo_url}" alt="${b.name}" />`;
    } else {
      wm.textContent = b.name;
    }
    const agentName = b.agent_name || "Ada";
    $("agent-name").textContent = agentName;
    if ($("ph-name")) $("ph-name").textContent = agentName;

    // Hero copy (all data-driven).
    if ($("kicker")) $("kicker").textContent = b.kicker ? `${agentName} ${b.kicker}` : "";
    $("hero-h1").textContent = b.hero_headline || "";
    if ($("hero-lead")) $("hero-lead").textContent = b.hero_subline || "";
    if ($("hero-note")) $("hero-note").textContent = b.hero_note || "";

    // Features ("Qué puede hacer Ada"). Empty → hide the block + its label.
    renderFeatures(b.features, agentName);

    // Footer.
    $("footer-powered").textContent = b.footer || "Powered by Onbotgo";
    // PrestamYpe runs against REAL data (Doris) — no demo disclaimer.
    // Demo tenants keep the "datos ficticios" notice.
    if (TENANT === "prestamype") {
      $("footer-disclaimer").textContent = `${b.name} · `;
    } else {
      $("footer-disclaimer").textContent = `${b.name} · Demostración con datos ficticios. `;
    }

    // ── PrestamYpe: PRODUCTION landing (real borrowers via Doris) ──
    // No demo cards, no test-DNI table. The flow is: landing → CTA opens the
    // chat → the borrower types their REAL DNI. Hide the whole cases section
    // and wire the hero CTA to open the chat widget.
    if (TENANT === "prestamype") {
      hideDemoCards();
      const note = $("dni-note");
      if (note) note.style.display = "none";
      wireCtaToChat("Consultar mi préstamo");
      revealContent();
      return;
    }

    // ── Identification UI ──
    // Some tenants hide the "Ingresa como uno de estos clientes" cards and
    // rely on DNI-first identification in the chat.
    if (b.show_demo_cards === false) {
      hideDemoCards();
      // No-PII contract: /branding never carries borrower DNIs/names, so the
      // landing must not render any. Show a neutral instruction instead.
      neutralizeDniNote(b.name);
      revealContent();
      return;
    }

    // Default tenants: clickable demo account cards (pre-identified tokens).
    renderDniNote(b);
    if (Array.isArray(b.demo_tokens) && b.demo_tokens.length) {
      renderCards(b.demo_tokens, agentName);
    }
    revealContent();
  }

  // Remove the anti-flash gate now this tenant's data is written.
  // Dispatches a generic event so tenant-specific scripts (e.g. hero.js) can
  // start animations exactly when content becomes visible — not before.
  function revealContent() {
    document.documentElement.classList.remove("branding-pending");
    document.dispatchEvent(new CustomEvent("pu:branding-ready", { detail: { tenant: TENANT } }));
  }

  // Production tenants (no demo section to scroll to): the hero CTA opens
  // the chat widget directly. The widget lives in its own Shadow DOM and
  // exposes no public open(); we click its FAB (reuses the widget's own
  // toggle, no widget.js changes). The FAB mounts async, so retry briefly.
  function wireCtaToChat(label) {
    const link = $("hero-cta-link");
    const text = $("hero-cta-text");
    if (text && label) text.textContent = label;
    if (!link) return;
    link.removeAttribute("href");
    link.style.cursor = "pointer";
    link.addEventListener("click", (ev) => {
      ev.preventDefault();
      openChatWidget();
    });
  }

  // Click the widget's FAB inside its Shadow DOM. Retries a few times in
  // case the CTA is clicked before the widget finished mounting.
  function openChatWidget(tries) {
    tries = tries || 0;
    const host = document.getElementById("pu-widget-host");
    const fab = host && host.shadowRoot && host.shadowRoot.getElementById("pu-fab");
    if (fab) { fab.click(); return; }
    if (tries < 20) setTimeout(() => openChatWidget(tries + 1), 100);
  }

  // Render the "Qué puede hacer" feature grid from data. Empty → hide block.
  function renderFeatures(features, agentName) {
    const grid = $("features");
    const label = $("features-label");
    if (!grid) return;
    if (!Array.isArray(features) || !features.length) {
      grid.style.display = "none";
      if (label) label.style.display = "none";
      return;
    }
    if (label) label.textContent = `Qué puede hacer ${agentName}`;
    grid.innerHTML = features.map((f) =>
      `<div class="feat"><h3>${_esc(f.title || "")}</h3><p>${_esc(f.body || "")}</p></div>`
    ).join("");
  }

  // DNI note: title is fixed; the intro line + sample list come from data.
  // The branding endpoint does NOT carry borrower DNIs (PII), so the sample
  // list stays generic — real demo DNIs are shared by the demo owner or read
  // from the visible demo cards.
  function renderDniNote(b) {
    const hint = $("dni-hint");
    if (hint) hint.textContent = b.dni_hint || "";
    // No PII in /branding → no hardcoded DNI list. The clickable demo cards
    // (rendered below) are the identification path; the list stays empty.
    const list = $("dni-list");
    if (list && !b.dni_hint) {
      const note = $("dni-note");
      if (note) note.style.display = "none";
    }
  }

  // Hide the demo-account cards + their section heading.
  function hideDemoCards() {
    const heading = $("acceso");
    const cards = $("cards");
    if (heading) heading.style.display = "none";
    if (cards) cards.style.display = "none";
  }

  // Replace the DNI note with a single neutral instruction (no PII rendered).
  function neutralizeDniNote(name) {
    const note = $("dni-note");
    if (note) {
      note.innerHTML =
        `<p class="dni-note-instruction"><b>Pídele al equipo de ${_esc(name || "la empresa")} un DNI de prueba y escríbelo en el chat cuando Ada te lo solicite.</b></p>`;
    }
  }

  function _badgeClass(status) {
    if (status === "en_mora") return "mora";
    if (status === "sin_deuda" || status === "cancelado") return "libre";
    return "aldia";
  }

  function renderCards(tokens, agentName) {
    const wrap = $("cards");
    if (!wrap) return;
    wrap.innerHTML = "";
    const arrow = '<svg fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24" style="width:14px;height:14px"><path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14m0 0l-7-7m7 7l-7 7"/></svg>';
    const ico = '<svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 21V7a2 2 0 012-2h14a2 2 0 012 2v14M3 21h18M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"/></svg>';
    tokens.forEach((t) => {
      const a = document.createElement("a");
      a.className = "card";
      a.href = `?tenant=${TENANT}&ct=${encodeURIComponent(t.token)}`;
      // No-PII contract: the branding endpoint sends only a casuística label
      // + status + currency — never the name. The card headline IS the
      // casuística so each demo case is clear.
      const parts = String(t.label || "").split("·").map((s) => s.trim());
      const casu = parts[0] || t.status_label || "Caso demo";
      const hint = parts.slice(1).join(" · ");
      a.innerHTML = `
        <div class="ico">${ico}</div>
        <div class="who">${_esc(casu)}</div>
        <div class="biz">${_esc(hint || (t.currency === "USD" ? "Dólares (US$)" : "Soles (S/)"))}</div>
        <span class="badge ${_badgeClass(t.status)}">${_esc(t.status_label || casu)}</span>
        <span class="go">Entrar como este caso ${arrow}</span>`;
      wrap.appendChild(a);
    });
  }

  // Escape user/data-driven strings before injecting as HTML.
  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Light tint of a hex color for soft backgrounds (icon chips, badges).
  function _tint(hex, alphaToWhite) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m) return "#e7fff3";
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    const mix = (c) => Math.round(c + (255 - c) * (1 - alphaToWhite));
    return `rgb(${mix(r)},${mix(g)},${mix(b)})`;
  }

  fetch(`${API}/api/v1/tenant/${TENANT}/branding`)
    .then((r) => (r.ok ? r.json() : null))
    .then((b) => {
      if (b) { applyBrand(b); }
      else { revealContent(); }  // unknown tenant: still reveal neutral page
    })
    .catch((e) => { console.error("[branding] fetch failed", e); revealContent(); });
})();
