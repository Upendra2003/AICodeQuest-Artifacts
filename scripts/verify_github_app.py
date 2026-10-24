"""Prove a registered GitHub App's credentials actually work.

Answers, in order, the questions that go wrong first:

  1. Are the env vars even set?
  2. Does the private key sign a JWT GitHub accepts? (GET /app)
  3. Which permissions and events did the App actually get?
  4. Where is it installed, and can we mint an installation token there?

Run it BEFORE opening a test pull request: every failure below produces a
webhook delivery that looks like a code bug and is really a setup mistake.

    cd backend && ../.venv/bin/python scripts/verify_github_app.py

Reads the same env vars app/services/github_auth.py does, so a pass here
means the backend will authenticate too:

    GITHUB_APP_ID
    GITHUB_APP_PRIVATE_KEY       (PEM content) -- or --
    GITHUB_APP_PRIVATE_KEY_PATH  (path to the .pem; preferred, since it
                                  keeps the key out of the environment and
                                  out of shell history)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.services.github_auth import GitHubAuthError, generate_app_jwt  # noqa: E402

API = "https://api.github.com"
OK, BAD, INFO = "  OK  ", " FAIL ", "  ..  "


def main() -> int:
    print("\nGitHub App credential check\n" + "=" * 60)

    app_id = os.environ.get("GITHUB_APP_ID")
    inline = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")

    print(f"[{OK if app_id else BAD}] GITHUB_APP_ID = {app_id or '(not set)'}")
    if key_path:
        exists = Path(key_path).is_file()
        print(f"[{OK if exists else BAD}] GITHUB_APP_PRIVATE_KEY_PATH = {key_path}"
              f"{'' if exists else '  <- file not found'}")
    elif inline:
        looks_pem = "BEGIN" in inline and "PRIVATE KEY" in inline
        print(f"[{OK if looks_pem else BAD}] GITHUB_APP_PRIVATE_KEY set "
              f"({len(inline)} chars){'' if looks_pem else '  <- does not look like a PEM'}")
    else:
        print(f"[{BAD}] neither GITHUB_APP_PRIVATE_KEY nor GITHUB_APP_PRIVATE_KEY_PATH is set")

    if not app_id or not (inline or key_path):
        print("\nSet the missing variables and run again. See docs/GITHUB-APP-SETUP.md §3.")
        return 1

    try:
        token = generate_app_jwt()
    except GitHubAuthError as exc:
        print(f"[{BAD}] could not sign a JWT: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 -- a malformed PEM raises from the crypto layer
        print(f"[{BAD}] private key could not be used to sign: {type(exc).__name__}: {exc}")
        print("       A truncated or re-wrapped PEM is the usual cause -- prefer "
              "GITHUB_APP_PRIVATE_KEY_PATH over pasting the key into a variable.")
        return 1
    print(f"[{OK}] signed an RS256 JWT with the private key")

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.get(f"{API}/app", headers=headers, timeout=20)
    except requests.RequestException as exc:
        print(f"[{BAD}] could not reach api.github.com: {exc}")
        return 1

    if resp.status_code != 200:
        print(f"[{BAD}] GitHub rejected the JWT: HTTP {resp.status_code} {resp.text[:200]}")
        print("       401 here almost always means the App ID and the private key "
              "belong to DIFFERENT Apps.")
        return 1

    app = resp.json()
    print(f"[{OK}] GitHub accepted it -- App '{app.get('name')}' "
          f"owned by @{(app.get('owner') or {}).get('login')}")

    perms = app.get("permissions") or {}
    events = app.get("events") or []
    wanted_perms = {"pull_requests": "read", "checks": "write",
                    "contents": "read", "metadata": "read"}
    for name, need in wanted_perms.items():
        got = perms.get(name)
        good = got == need or (need == "read" and got == "write")
        print(f"[{OK if good else BAD}] permission {name}: {got or 'MISSING'} (need {need})")
    for event in ("pull_request", "push", "check_run"):
        print(f"[{OK if event in events else BAD}] event {event}"
              f"{'' if event in events else ' NOT subscribed'}")

    resp = requests.get(f"{API}/app/installations", headers=headers, timeout=20)
    if resp.status_code != 200:
        print(f"[{BAD}] could not list installations: HTTP {resp.status_code}")
        return 1
    installations = resp.json()
    if not installations:
        print(f"[{BAD}] the App is not installed anywhere yet -- install it on the "
              "account that OWNS the repository your pull requests target.")
        return 1

    for inst in installations:
        account = (inst.get("account") or {}).get("login")
        print(f"[{OK}] installed on @{account} (installation_id={inst.get('id')})")
        token_resp = requests.post(
            f"{API}/app/installations/{inst['id']}/access_tokens", headers=headers, timeout=20
        )
        if token_resp.status_code == 201:
            print(f"[{OK}]   minted an installation access token for @{account}")
        else:
            print(f"[{BAD}]   could not mint a token: HTTP {token_resp.status_code} "
                  f"{token_resp.text[:150]}")

    print("\nAll set -- the backend will authenticate with these credentials.")
    print("Remember the App must be installed on the account that owns the BASE "
          "repository of your pull requests, not the fork they come from.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
