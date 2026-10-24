"""A simple, self-contained web page for viewing your own AICodeQuest
profile without installing the Chrome extension (per the phase brief's
"extension popup + a simple web page" requirement) -- a login form plus
fetch() calls straight against this SAME backend's /auth/login and
/leaderboard/* JSON endpoints. No build step, no separate frontend
project, no new templating convention: this backend has no other
templated pages, so returning one raw HTML string here is genuinely
simpler than introducing a templates/ folder + Jinja for exactly one
static page.

The JWT is kept in an in-memory JS variable only (not localStorage/
sessionStorage) -- it's gone on tab close/refresh, so re-login is
required each visit. A deliberate simplicity/security tradeoff for a
page whose whole purpose is a quick profile check, not a persistent
session; see reports/phase-11-review.md open questions.
"""

from __future__ import annotations

from flask import Blueprint, Response

profile_page_bp = Blueprint("profile_page", __name__)

_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>AICodeQuest Profile</title>
<style>
  body { font: 14px/1.5 system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #1f2328; }
  h1 { font-size: 20px; }
  form { display: flex; flex-direction: column; gap: 8px; max-width: 320px; }
  input, button { font: inherit; padding: 6px 8px; }
  #login-error { color: #cf222e; display: none; }
  #profile { display: none; }
  .tier { display: inline-block; padding: 3px 10px; border-radius: 12px; background: #eaeef2; font-weight: 600; text-transform: capitalize; }
  table { border-collapse: collapse; margin: 12px 0; width: 100%; }
  td { padding: 4px 8px; border-bottom: 1px solid #eee; }
  td:last-child { text-align: right; font-family: ui-monospace, monospace; }
  #badges { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
  #badges li { background: #ddf4ff; border: 1px solid #54aeff; border-radius: 12px; padding: 2px 10px; font-size: 12px; }
  .consented { color: #2da44e; }
  .not-consented { color: #9a6700; }
  #history-list { font-size: 13px; }
  #export-error { color: #cf222e; display: none; }
</style>
</head>
<body>
<h1>AICodeQuest Profile</h1>

<form id="login-form">
  <input id="email" type="email" placeholder="email" autocomplete="username" required />
  <input id="password" type="password" placeholder="password" autocomplete="current-password" required />
  <button type="submit">Log in</button>
  <div id="login-error"></div>
</form>

<div id="profile">
  <p>Logged in as <strong id="display-name"></strong> -- <span class="tier" id="tier"></span> streak: <span id="streak"></span></p>

  <h3>Badges</h3>
  <ul id="badges"></ul>

  <h3>Your stats</h3>
  <table id="stats"></table>

  <h3>Consent</h3>
  <p id="consent"></p>

  <h3>History</h3>
  <ul id="history-list"></ul>

  <button id="export-button" type="button">Export my data (JSON)</button>
  <div id="export-error"></div>
</div>

<script>
  let token = null;

  async function api(path, options) {
    const res = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || (path + " failed with " + res.status));
    return data;
  }

  function renderProfile(profile) {
    document.getElementById("display-name").textContent = profile.display_name;
    document.getElementById("tier").textContent = profile.public_metrics.tier;
    document.getElementById("streak").textContent = profile.public_metrics.streak;

    const badgesEl = document.getElementById("badges");
    badgesEl.innerHTML = "";
    if (profile.public_metrics.badges.length === 0) {
      const li = document.createElement("li");
      li.textContent = "no badges yet";
      badgesEl.appendChild(li);
    }
    for (const badge of profile.public_metrics.badges) {
      const li = document.createElement("li");
      li.textContent = badge.name;
      li.title = badge.tier + " tier";
      badgesEl.appendChild(li);
    }

    const priv = profile.private_metrics;
    const rows = [
      ["Total gates cleared", profile.public_metrics.total_gates_cleared],
      ["Developer gates cleared", priv.dev_gates_cleared],
      ["Lead gates cleared", priv.lead_gates_cleared],
      ["No-scaffold clears", priv.no_scaffold_clears],
      ["Replays", priv.replay_count],
      ["Scaffold exposures", priv.scaffold_exposures],
      ["Escalated (researcher review)", priv.escalated_count],
    ];
    const statsEl = document.getElementById("stats");
    statsEl.innerHTML = "";
    for (const [label, value] of rows) {
      const tr = document.createElement("tr");
      const tdLabel = document.createElement("td");
      tdLabel.textContent = label;
      const tdValue = document.createElement("td");
      tdValue.textContent = value;
      tr.append(tdLabel, tdValue);
      statsEl.appendChild(tr);
    }

    const consentEl = document.getElementById("consent");
    if (profile.consent) {
      consentEl.textContent = profile.consent.withdrawn
        ? "Consent withdrawn"
        : "Consented (v" + profile.consent.version + ")";
      consentEl.className = "consented";
    } else {
      consentEl.textContent = "Not yet consented";
      consentEl.className = "not-consented";
    }

    const historyEl = document.getElementById("history-list");
    historyEl.innerHTML = "";
    if (profile.history.length === 0) {
      const li = document.createElement("li");
      li.textContent = "no gate history yet";
      historyEl.appendChild(li);
    }
    for (const h of profile.history) {
      const li = document.createElement("li");
      li.textContent = h.persona + " -- " + h.pr_id + " -- " + h.state + " (" + h.updated_at + ")";
      historyEl.appendChild(li);
    }

    document.getElementById("login-form").style.display = "none";
    document.getElementById("profile").style.display = "block";
  }

  document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const errorEl = document.getElementById("login-error");
    errorEl.style.display = "none";
    try {
      const { access_token } = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: document.getElementById("email").value,
          password: document.getElementById("password").value,
        }),
      });
      token = access_token;
      const profile = await api("/leaderboard/me");
      renderProfile(profile);
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.style.display = "block";
    }
  });

  document.getElementById("export-button").addEventListener("click", async () => {
    const errorEl = document.getElementById("export-error");
    errorEl.style.display = "none";
    try {
      const data = await api("/leaderboard/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "aicodequest-data-export.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.style.display = "block";
    }
  });
</script>
</body>
</html>
"""


@profile_page_bp.get("/profile")
def profile_page():
    return Response(_HTML, mimetype="text/html")
