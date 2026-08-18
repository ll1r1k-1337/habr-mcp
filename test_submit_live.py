#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2", "httpx"]
# ///
"""Live check: habr_submit on a Vue page must not wipe the skills.

Runs against whatever account the cookie belongs to — nothing is hardcoded.
"""
import importlib.util
import re
import sys

spec = importlib.util.spec_from_file_location("srv", "server.py")
m = importlib.util.module_from_spec(spec)
sys.modules["srv"] = m
spec.loader.exec_module(m)

who = m.habr_whoami()
login = re.search(r"logged in: ([\w.-]+)", who)
assert login, who
resume = f"/{login.group(1)}/print"

grab = lambda p, s: (re.search(p, s, re.S) or [None, ""])[1]
before = m.habr_get(resume, limit=99999)
skills = grab(r'skills">([^<]+)<', before)
langs = grab(r"foreign-languages.*?<td>([^<]+)", before)
salary = grab(r"ожидания: <b>([^<]+)<", before)
assert skills, "no skills on the profile — nothing to protect, test is meaningless"
print("before:", salary or "(not set)", "|", skills[:60], "...")

# nudge the salary by 10k and put it back at the end
digits = int(re.sub(r"\D", "", salary) or 0)
new = str(digits + 10000 if digits else 300000)
print("reply :", m.habr_submit("/profile/specialization", {"user[salary]": new}).split("\n")[0])

after = m.habr_get(resume, limit=99999)
skills2 = grab(r'skills">([^<]+)<', after)
langs2 = grab(r"foreign-languages.*?<td>([^<]+)", after)
salary2 = grab(r"ожидания: <b>([^<]+)<", after)
print("after :", salary2, "|", skills2[:60], "...")
print("langs :", langs2 or "(none)")

if digits:  # restore, so the test leaves the profile as it found it
    m.habr_submit("/profile/specialization", {"user[salary]": str(digits)})

assert skills2 == skills, f"SKILLS LOST\n{skills}\n{skills2}"
assert langs2 == langs, f"LANGUAGES LOST\n{langs}\n{langs2}"
assert salary2 != salary, "salary unchanged — the submit did nothing"
print("\nok: editing one field did not wipe the Vue fields")
