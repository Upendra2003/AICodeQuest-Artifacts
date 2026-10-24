"""Phase 30 demo: the Lead gate, played end to end against the REAL local
Ollama model -- not MockLLM, not ScriptedLLM.

Two things are being demonstrated, and the second is the point of the
phase:

  1. A lead can play the gate: pick NFRs, get a diff-grounded
     multiple-choice question per NFR, answer it, see the key revealed,
     and clear (or fail) on percent correct.
  2. Every generated item's AUTOMATED QC verdict is printed next to what
     it actually generated -- because Phase 30's whole claim is that we
     stopped assuming LLM-authored assessment content is sound. This
     script is where that claim meets a real model. If the local model
     writes bad questions, this output says so rather than hiding it.

Deliberately NOT mocked, for the same reason Phase 7's demo wasn't: a
mocked run proves the plumbing and nothing about generation quality, and
generation quality is the thing under question here. Expect this to be
slow (one generation plus lead_gate.self_consistency_k cold re-asks per
item, times however many NFRs) and expect it to sometimes produce a
QC-failed item -- that is a finding to report, not a failure to hide.

Run (needs a local Ollama server; no Mongo needed, mongomock is used):

    cd backend && ../.venv/bin/python scripts/demo_phase_30.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import mongomock  # noqa: E402

from app import create_app  # noqa: E402
from app.core.config import get_config  # noqa: E402
from app.services import consent  # noqa: E402

# A small, realistic diff with obvious NFR angles on several axes: an
# unbounded read (reliability/performance), a formatted-in SQL string
# (security), and a silently-swallowed exception (maintainability).
DIFF_TEXT = """--- a/reports.py
+++ b/reports.py
@@ -1,1 +1,17 @@
 import sqlite3
+def export_user_report(conn, user_email, since):
+    cursor = conn.cursor()
+    query = "SELECT * FROM events WHERE email = '%s' AND ts > '%s'" % (user_email, since)
+    cursor.execute(query)
+    rows = cursor.fetchall()
+    lines = []
+    for row in rows:
+        try:
+            lines.append(format_event_row(row))
+        except Exception:
+            pass
+    return ",".join(lines)
+
+
+def format_event_row(row):
+    return "%s,%s,%s" % (row[0], row[1], row[2])
"""

NFRS = ["security", "reliability", "maintainability"]


def _rule(char="=", width=78):
    print(char * width)


def main() -> None:
    cfg = get_config()["lead_gate"]
    print(f"lead_gate config: options_per_question={cfg['options_per_question']}, "
          f"self_consistency_k={cfg['self_consistency_k']}, "
          f"pass_threshold_pct={cfg['pass_threshold_pct']}, "
          f"verification.enabled={cfg['verification']['enabled']}")
    print(f"NFRs: {NFRS}\n")

    db = mongomock.MongoClient()["demo_phase_30"]
    app = create_app(mongo_db=db)
    client = app.test_client()

    reg = client.post("/auth/register", json={
        "email": "lead-demo@example.com", "password": "correct-horse-battery",
        "display_name": "Demo Lead", "role_hint": "lead",
    })
    token = client.post("/auth/login", json={
        "email": "lead-demo@example.com", "password": "correct-horse-battery",
    }).get_json()["access_token"]
    consent.sign_participant_consent(
        db, reg.get_json()["id"], version=get_config()["consent"]["current_version"],
        retain_code_snippets=True, retain_transcripts=True, allow_quotes_in_publication=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    # The real pr_id format contains a "#" (owner__repo#number), which a
    # test-client URL treats as a fragment and silently truncates -- so
    # every request must percent-encode it, exactly as the extension's
    # api.js does with encodeURIComponent.
    pr_id = quote("demo__repo#30", safe="")
    client.post(f"/lead-gate/{pr_id}/nfrs", headers=headers,
                json={"selected": NFRS, "custom": [], "diff": DIFF_TEXT})

    # --- generation (the slow part; all of a retry's questions at once) ---
    _rule()
    print("GENERATING (real model -- one generation + k cold re-asks per item)")
    _rule()
    t0 = time.monotonic()
    client.get(f"/lead-gate/{pr_id}/current-question", headers=headers)
    elapsed = time.monotonic() - t0
    questions = list(db["lead_questions"].find({}).sort("question_index", 1))
    print(f"{len(questions)} item(s) in {elapsed:.1f}s "
          f"({elapsed / max(1, len(questions)):.1f}s each)\n")

    qc_failed = 0
    for q in questions:
        _rule("-")
        print(f"[{q['nfr']}]  qc_passed={q['qc_passed']}  "
              f"regenerations={q['regeneration_count']}  "
              f"self_consistency={q.get('self_consistency_answers')}")
        if not q["qc_passed"]:
            qc_failed += 1
            for reason in q["qc_reasons"]:
                print(f"    ! {reason}")
        print(f"    grounded in: {q['matched_diff_identifiers']}")
        print(f"\n  Q: {q['question_text']}")
        for i, opt in enumerate(q["options"]):
            mark = " <= key" if i == q["answer_index"] else ""
            print(f"     {chr(65 + i)}. {opt}{mark}")
        print(f"\n  Why: {q['answer_explanation']}\n")

    # --- play it: answer every question CORRECTLY, then verify one item ---
    _rule()
    print("PLAYING (answering with the recorded key each time)")
    _rule()
    for q in questions:
        payload = client.get(f"/lead-gate/{pr_id}/current-question", headers=headers).get_json()
        if payload.get("question") is None:
            break
        # Proof the key is not served with the question -- the property
        # the whole reveal-after-answer design exists to hold.
        assert "answer_index" not in str(payload["question"]), "answer key leaked to the lead!"

        result = client.post(f"/lead-gate/{pr_id}/answer", headers=headers,
                             json={"selected_index": q["answer_index"]}).get_json()
        print(f"  [{result['nfr']}] picked {chr(65 + result['selected_index'])} -> "
              f"is_correct={result['is_correct']} score={result['score']}")

        # The human-verification step, exercised on the first item only so
        # the demo also shows the skip path on the rest.
        if q is questions[0]:
            verified = client.post(f"/lead-gate/{pr_id}/verify", headers=headers, json={
                "question_id": str(q["_id"]),
                "question_valid": True,
                "answer_valid": q["qc_passed"],
                "comment": "demo verification",
            }).get_json()
            print(f"      verified: question_valid=True "
                  f"answer_valid={q['qc_passed']} "
                  f"flagged_for_review={verified['flagged_for_review']}")
        else:
            client.post(f"/lead-gate/{pr_id}/verify", headers=headers,
                        json={"question_id": str(q["_id"])})
            print("      verification skipped (recorded as a skip)")

        client.post(f"/lead-gate/{pr_id}/advance", headers=headers)

    state = client.get(f"/lead-gate/{pr_id}/state", headers=headers).get_json()
    _rule()
    print(f"FINAL STATE: {state['state']}  weighted_score={state['weighted_score']}  "
          f"passed={state['passed']}")

    record = db["lead_records"].find_one({})
    summary = record["summary"] if record else {}
    print(f"lead_records summary: answered={summary.get('answered')} "
          f"correct={summary.get('correct')} "
          f"verifications_given={summary.get('verifications_given')} "
          f"questions_flagged_invalid={summary.get('questions_flagged_invalid')} "
          f"questions_qc_failed={summary.get('questions_qc_failed')}")
    _rule()
    print(f"\nQC verdict on this run: {len(questions) - qc_failed}/{len(questions)} "
          f"item(s) passed the automated gate.")
    if qc_failed:
        print("A QC-failed item was still SERVED (by design) and is marked "
              "qc_passed=False in Mongo -- see reports/phase-30-review.md.")


if __name__ == "__main__":
    main()
