/* Landing page: 3D robot greeting (pure CSS 3D + a little JS for typing and mouse-follow). */
"use strict";
(function () {
  const MESSAGES = ["Hi!!! 👋 Welcome to Copilot", "I'll help you beat the ATS.", "Click “Get started” to begin!"];
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let started = false, typingTimer = null;

  function type(i) {
    const el = document.getElementById("speech-text");
    if (!el) return;
    const msg = MESSAGES[i % MESSAGES.length];
    let n = 0;
    el.textContent = "";
    clearTimeout(typingTimer);
    const step = () => {
      el.textContent = msg.slice(0, ++n);
      if (n < msg.length) typingTimer = setTimeout(step, reduce ? 0 : 55);
      else typingTimer = setTimeout(() => type(i + 1), 2600);
    };
    step();
  }

  function follow() {
    const stage = document.getElementById("robot-stage");
    const robot = document.getElementById("robot");
    if (!stage || !robot || reduce) return;
    window.addEventListener("pointermove", (e) => {
      if (document.getElementById("home").hidden) return;
      const r = stage.getBoundingClientRect();
      const x = (e.clientX - (r.left + r.width / 2)) / window.innerWidth;
      const y = (e.clientY - (r.top + r.height / 2)) / window.innerHeight;
      robot.style.setProperty("--ry", `${Math.max(-35, Math.min(35, x * 70))}deg`);
      robot.style.setProperty("--rx", `${Math.max(-15, Math.min(15, -y * 30))}deg`);
    });
  }

  window.startRobot = function () {
    type(0);
    if (!started) {
      started = true;
      follow();
      document.getElementById("see-features")?.addEventListener("click", () =>
        document.getElementById("features").scrollIntoView({ behavior: reduce ? "auto" : "smooth" }));
      document.getElementById("robot")?.addEventListener("click", () => type(0));
    }
  };
})();
