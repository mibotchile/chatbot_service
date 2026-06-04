/**
 * Prestamype hero entrance animation — GSAP 3.
 *
 * Listens for the generic "pu:branding-ready" event dispatched by app.js
 * after branding-pending is removed. Falls back to playing after 1200 ms so
 * the animation never gets stuck if the event is missed.
 *
 * CSP: external file, no inline eval. Selectors scoped to .hero to avoid
 * touching the widget shadow DOM.
 */
(function () {
  "use strict";

  if (!window.gsap) return;

  var hero = document.querySelector(".hero");
  if (!hero) return;

  var copyChildren = hero.querySelectorAll(".kicker, #hero-h1, .lead, .hero-cta");
  var phone = hero.querySelectorAll(".device-stage, .phone");

  // Set initial hidden state immediately — composes with branding-pending
  // visibility:hidden so there is no flash of unstyled / unanimated content.
  gsap.set(copyChildren, { autoAlpha: 0, y: 24 });
  gsap.set(phone, { autoAlpha: 0, x: 40, scale: 0.96 });

  var played = false;

  var tl = gsap.timeline({ paused: true, onStart: function () { played = true; } });

  gsap.matchMedia().add(
    "(prefers-reduced-motion: reduce)",
    function () {
      // Instant reveal — no motion for users who prefer it.
      gsap.set(copyChildren, { autoAlpha: 1, y: 0 });
      gsap.set(phone, { autoAlpha: 1, x: 0, scale: 1 });
      played = true;
    }
  );

  gsap.matchMedia().add(
    "(prefers-reduced-motion: no-preference)",
    function () {
      tl
        .to(copyChildren, {
          autoAlpha: 1,
          y: 0,
          duration: 0.7,
          stagger: 0.1,
          ease: "power3.out",
        })
        .to(
          phone,
          {
            autoAlpha: 1,
            x: 0,
            scale: 1,
            duration: 0.9,
            ease: "back.out(1.4)",
          },
          0.2
        );
    }
  );

  function play() {
    if (played) return;
    tl.play();
  }

  // Play when branding is ready (normal path).
  document.addEventListener("pu:branding-ready", play, { once: true });

  // Fallback: if event never fires (edge case), play after 1200 ms.
  setTimeout(function () {
    if (!played) play();
  }, 1200);
})();
