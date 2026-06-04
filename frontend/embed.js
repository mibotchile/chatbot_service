/* PubotWidget embed loader — the ~minimal snippet a client pastes on their site.
 *
 *   <script src="https://demos.mibot.cl/pubot-c02e78e1/embed.js"
 *           data-tenant="prestamype"
 *           data-pk="pk_live_your_tenant_key_here"
 *           async></script>
 *
 * Supported data-* attributes:
 *   data-tenant  — tenant slug (default: "prestaunion")
 *   data-pk      — publishable key for this tenant's embed (PUBLIC, not a secret).
 *                  Required on third-party pages so the widget can authenticate
 *                  gated API routes. Same-origin landings inject it via window.__PK__
 *                  (server-side sentinel replacement) so data-pk is optional there.
 *                  Get your key from the tenant config publishable_keys[status=current].
 *   data-ct      — optional demo campaign token (pre-identified visitor)
 *   data-api     — optional backend base URL override
 *
 * What it does (Intercom/Drift style):
 *   1. Reads its OWN config from the <script> tag: data-tenant / data-pk /
 *      data-ct / data-api (document.currentScript at parse time, captured up front).
 *   2. Derives the backend base URL from its own src (origin + path up to
 *      /embed.js), e.g. https://demos.mibot.cl/pubot-c02e78e1. data-api wins.
 *   3. Creates a host <div> on the client page and attaches a Shadow DOM
 *      (mode:"open") — this ISOLATES the widget's CSS/JS from the client site
 *      (their styles can't break the widget; the widget's can't touch them).
 *   4. Loads widget.js once (with data-no-automount so it doesn't make its own
 *      host) and calls window.PubotWidget.mount({ shadowRoot, api, tenant, ct, pk }).
 *   5. Idempotent: including the snippet twice mounts only once.
 *
 * Framework-free. Served alongside widget.min.js from the demo container.
 *
 * Widget URL strategy:
 *   embed.js loads api + "/widget.js". The server returns a 302 redirect to the
 *   current immutable versioned URL (/widget/<version>/widget.min.js), which the
 *   browser then fetches with Cache-Control: public, max-age=31536000, immutable.
 *   This means:
 *     - embed.js never needs to know the version string (no build-time sed required).
 *     - The redirect URL itself is no-cache, so after a deploy the browser picks
 *       up the new versioned URL on the next embed.js load.
 *     - The versioned asset is permanently cached once fetched — zero extra round
 *       trips for repeat visits.
 *   Alternatively, the Docker esbuild stage can sed __WIDGET_VERSION__ into embed.js
 *   to bypass the redirect entirely, but the redirect approach is simpler and correct.
 *
 * Security note: minification (applied to widget.js by the Docker build stage)
 *   provides size reduction and mild deterrence only — it is NOT a security control.
 *   No real secret (csrf_secret, session key) ever appears in client-side JS.
 */
(function () {
  "use strict";

  // currentScript is reliable while this file is being parsed (sync or async).
  var self = document.currentScript;

  // Guard: never mount twice (snippet pasted more than once, SPA re-injects…).
  if (window.__pubotEmbedLoaded) return;
  window.__pubotEmbedLoaded = true;

  function attr(name, fallback) {
    var v = self && self.getAttribute && self.getAttribute(name);
    return v != null && v !== "" ? v : fallback;
  }

  // ── Backend base URL: data-api wins, else derive from THIS script's src. ──
  function deriveApi() {
    var override = attr("data-api", null);
    if (override) return override.replace(/\/+$/, "");
    try {
      var u = new URL(self.src, location.href);
      // strip the trailing /embed.js (and any ?v= cache-bust) → the base path
      var prefix = u.pathname.replace(/\/embed\.js$/, "");
      return (u.origin + prefix).replace(/\/+$/, "");
    } catch (e) {
      return "";
    }
  }

  var api = deriveApi();
  var tenant = attr("data-tenant", "prestaunion");
  var ct = attr("data-ct", null);
  // Publishable key — PUBLIC, not a secret. Required on third-party embeds so
  // the widget can authenticate gated API routes. Same-origin landings use the
  // server-injected window.__PK__ instead (no data-pk needed there).
  var pk = attr("data-pk", null);

  // ── Host element + Shadow DOM (the isolation boundary). ──
  function makeShadowHost() {
    var host = document.getElementById("pubot-embed-host");
    if (host && host.shadowRoot) return host.shadowRoot; // already created
    host = document.createElement("div");
    host.id = "pubot-embed-host";
    // 0-size fixed anchor: the FAB + panel inside are position:fixed (viewport),
    // so the host doesn't reserve layout space on the client page.
    host.style.cssText = "position:fixed;z-index:2147483000;width:0;height:0;border:0;margin:0;padding:0;";
    document.body.appendChild(host);
    return host.attachShadow({ mode: "open" });
  }

  function boot() {
    var shadowRoot = makeShadowHost();

    function doMount() {
      if (!window.PubotWidget || !window.PubotWidget.mount) return false;
      window.PubotWidget.mount({ shadowRoot: shadowRoot, api: api, tenant: tenant, ct: ct, pk: pk });
      return true;
    }

    // widget.js may already be present (e.g. same page loaded both). If so, mount
    // immediately; otherwise inject it once and mount on load.
    if (doMount()) return;

    var existing = document.querySelector('script[data-pubot-widget="1"]');
    if (existing) {
      existing.addEventListener("load", doMount);
      return;
    }
    var s = document.createElement("script");
    s.src = api + "/widget.js";
    s.async = true;
    s.setAttribute("data-pubot-widget", "1");
    // Tell widget.js NOT to auto-mount its own host — the loader owns the shadow.
    s.setAttribute("data-no-automount", "");
    s.addEventListener("load", doMount);
    document.head.appendChild(s);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
