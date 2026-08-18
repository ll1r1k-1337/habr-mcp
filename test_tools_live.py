#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2", "httpx"]
# ///
"""Live checks for the rest: HTML stripping, Vue state in habr_form, the verdict line.

Runs against whatever account the cookie belongs to — nothing is hardcoded.
"""
import importlib.util
import re
import sys

spec = importlib.util.spec_from_file_location("srv", "server.py")
m = importlib.util.module_from_spec(spec)
sys.modules["srv"] = m
spec.loader.exec_module(m)

raw = m.habr_get("/profile/specialization", limit=999999, raw=True)
cln = m.habr_get("/profile/specialization", limit=999999)
print(f"habr_get: raw={len(raw)} clean={len(cln)} (-{round(100 * (1 - len(cln) / len(raw)))}%)")
assert len(cln) < len(raw) / 2, "stripping did not work"
assert "mixpanel" not in cln, "scripts survived"

form = m.habr_form("/profile/specialization")
assert "data-ssr-state" in form and "selectedSkills" in form, form[:300]
print("habr_form: Vue block visible, skillsFieldName =", "skillsFieldName" in form)

# a description under 50 chars must be rejected by Rails, and the verdict must catch it
ids = re.findall(r"/profile/experiences/(\d+)/edit", m.habr_get("/profile/experiences"))
assert ids, "no experience entries on the profile — add one to run this check"
edit = f"/profile/experiences/{ids[0]}/edit"
kept = re.search(r"<textarea[^>]*name=\"experience\[description\]\"[^>]*>(.{0,40})",
                 m.habr_get(edit, limit=999999), re.S).group(1).strip()

bad = m.habr_submit(edit, {"experience[description]": "too short"})
print("verdict:", bad.split("\n")[0][:100])
assert bad.startswith("NOT SAVED"), f"validation failure not detected: {bad[:200]}"
assert kept in m.habr_get(edit, limit=999999), "DESCRIPTION WIPED by the rejected edit"
print("ok: HTML stripping, Vue in habr_form, error verdict, data intact")
