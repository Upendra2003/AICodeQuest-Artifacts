"""Runs a REAL backend for manually testing the Chrome extension against
real GitHub PR pages: real persistent Mongo (mongodb://localhost:27017,
already running via `make up`), real live Ollama (qwen2.5:7b-instruct
generator, mistral:latest opponent -- both already pulled and running
locally per config/params.yaml's `llm` section), and the SANDBOXED
execution backend config asks for -- Piston, as of Phase 28.

It used to pass tests/support/local_executor.run_locally instead, a
deliberate Phase 3/4 workaround from when Judge0 could not execute
anything on this cgroup v2 host. Phase 28 replaced Judge0 with Piston,
which runs here, but this script was never switched over -- so the study
was played against a runner whose own docstring says "NEVER use this for
arbitrary or participant-supplied code", and which supports exactly four
languages. The first real C++ pull request made that visible: every
prefetch job died with `ValueError: unsupported language 'cpp'` inside
suitegen, so no turn ever got a test suite and every one of them paid
the full 45s inline_wait_ms first.

`runner=None` means "whatever config/params.yaml's execution.backend
says", resolved identically in this process and in the RQ worker (see
app/workers/runners.py). Piston must be up (`docker compose up -d
piston`) WITH its runtimes installed -- a fresh container has none; see
infra/piston/install_runtimes.py.

Meant to be reached via SSH port forwarding from wherever the extension
and a real Chrome browser actually run:

    ssh -L 5055:localhost:5055 <this-host>

then build/load the extension locally as normal -- its
extension.backend_base_url (config/params.yaml, default
http://localhost:5055) will transparently reach this process through the
tunnel with no rebuild needed.

Port 5055, not Flask's default 5000: an unrelated `python app.py` from
another project holds 0.0.0.0:5000 on this host. The port below is parsed
out of extension.backend_base_url rather than hardcoded, so the extension
and this server can never disagree about it.

Run via:

    cd backend && ../.venv/bin/python scripts/run_dev_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# Flask's own request line ("POST /gate/evaluate 200") says a call happened
# but nothing about WHY the gate fired or didn't -- app.api.gate and
# app.api.dev_game/lead_gate log the trigger decision/reasons/state
# transitions at INFO, and the root logger is unconfigured by default
# (WARNING), so none of it would otherwise reach this console.
#
# The handler is app/telemetry/log_format.py's: coloured by SUBSYSTEM, so a
# turn's path through dev_gate -> perturbation -> llm -> suitegen -> piston
# is skimmable instead of one wall of text. Run with AICQ_VERBOSE=1 to add
# every LLM prompt and response and per-stage entry/exit timings.
from app.telemetry.log_format import setup_from_config  # noqa: E402

_VERBOSE = setup_from_config()

from app import create_app  # noqa: E402
from app.core.config import get_config  # noqa: E402
from app.services.health import check_execution_backend  # noqa: E402

ACCOUNTS = [
    {"email": "developer@example.com", "password": "correct-horse-battery", "display_name": "Test Developer", "role_hint": "developer"},
    {"email": "lead@example.com", "password": "correct-horse-battery", "display_name": "Test Lead", "role_hint": "lead"},
]


def _runtime_summary(backend: str) -> str:
    """A loud line at boot about the execution sandbox.

    A fresh Piston container ships with ZERO language runtimes and answers
    /runtimes with `[]` -- it is reachable, healthy-looking, and cannot
    execute one submission. Silent degradation there costs a whole session
    of turns with no test suites, so say it here rather than let it be
    discovered in the logs afterwards.
    """
    try:
        _, status = check_execution_backend()
    except Exception as exc:  # noqa: BLE001 -- never block the dev server on a probe
        return f"UNREACHABLE ({type(exc).__name__}: {exc})"
    if status.get("ok"):
        runtimes = status.get("runtimes")
        return f"ok{f', {runtimes} runtimes installed' if runtimes is not None else ''}"
    return f"NOT OK -- {status.get('error') or status}"


def _ensure_accounts(app) -> None:
    client = app.test_client()
    for account in ACCOUNTS:
        r = client.post("/auth/register", json=account)
        if r.status_code == 201:
            print(f"  registered: {account['email']}")
        elif r.status_code == 409:
            print(f"  already exists: {account['email']}")
        else:
            print(f"  WARNING: unexpected response registering {account['email']}: {r.status_code} {r.get_json()}")


def main() -> None:
    # mongo_db left as None -> real Mongo at MONGO_URI/mongodb://localhost:27017
    # (app/db/client.py), the "aicodequest" database -- this is the SAME
    # persistent database prior phase demos/tests may have touched, not an
    # ephemeral mongomock instance. Nothing here wipes existing data.
    app = create_app()  # runner=None -> config's execution.backend (Piston)

    print("Ensuring test accounts exist (real Mongo, argon2-hashed, real /auth/register path):")
    _ensure_accounts(app)

    backend_base_url = get_config()["extension"]["backend_base_url"]
    port = urlparse(backend_base_url).port or 5055

    llm_cfg = get_config()["llm"]
    print(f"\nLLM: provider={llm_cfg['provider']} model={llm_cfg['model']} opponent={llm_cfg['opponent_model']} "
          f"@ {llm_cfg['ollama_base_url']} (REAL, live calls)")
    exec_backend = get_config()["execution"]["backend"]
    print(f"Execution backend: {exec_backend} (config execution.backend) -- {_runtime_summary(exec_backend)}")
    print(
        f"Logging: {'VERBOSE' if _VERBOSE else 'normal'}"
        f"{'' if _VERBOSE else '  (re-run with AICQ_VERBOSE=1 for full LLM prompts/responses and per-stage timings)'}"
    )
    print(f"\nListening on 0.0.0.0:{port}. From your local machine:")
    print(f"  ssh -L {port}:localhost:{port} <this-host>")
    print(f"Then load the extension locally (extension.backend_base_url must be {backend_base_url}, the default).")
    print("\nLogin accounts:")
    for a in ACCOUNTS:
        print(f"  {a['role_hint']}: {a['email']} / {a['password']}")
    print()

    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
