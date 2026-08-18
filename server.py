#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2", "httpx"]
# ///
"""MCP server for career.habr.com, driven by a browser session cookie.

There is no write API: the site is Rails with server-rendered forms. So instead
of 20 typed tools this exposes 5 generic ones — an edit page is its own schema,
and the LLM reads it.

Cookie: devtools -> Application -> Cookies -> _career_session, put it in HABR_COOKIE.
"""
import os
import re
import html as _html
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

BASE = "https://career.habr.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

mcp = MCPServer("habr-career")
_http: httpx.Client | None = None
_csrf: str | None = None


def http() -> httpx.Client:
    global _http
    if _http is None:
        cookie = os.environ.get("HABR_COOKIE", "").strip()
        env = Path(__file__).with_name(".env")
        if not cookie and env.exists():  # secret lives in .env, not in the MCP client config
            cookie = re.sub(r"^HABR_COOKIE=|^\s*['\"]|['\"]\s*$", "", env.read_text().strip())
        if cookie and "=" not in cookie:  # bare session value was passed
            cookie = f"_career_session={cookie}"
        _http = httpx.Client(
            base_url=BASE, timeout=30, follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9",
                     **({"Cookie": cookie} if cookie else {})},
        )
    return _http


def csrf() -> str:
    """The Rails token is masked per session — one for every form, fetched once."""
    global _csrf
    if _csrf is None:
        html = http().get("/").text
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if not m:
            raise RuntimeError("csrf-token not found: session/WAF served the wrong page")
        _csrf = m.group(1)
    return _csrf


class _Forms(HTMLParser):
    """Pulls out forms, fields and their CURRENT values — beats 40 KB of HTML in context.

    Values are mandatory: Rails forms are written whole, so submitting without the
    current user[about] blanks it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._ta: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self.forms.append({"action": a.get("action", ""),
                               "method": a.get("method", "get").upper(), "fields": []})
        elif tag in ("input", "select", "textarea") and a.get("name") and self.forms:
            f = {"name": a["name"], "tag": tag}
            if a.get("type"):
                f["type"] = a["type"]
            # habr_submit supplies authenticity_token — in the schema it is just noise
            if a.get("value") and a["name"] != "authenticity_token":
                f["value"] = a["value"]
            if a.get("type") in ("checkbox", "radio") and "checked" not in a:
                f.pop("value", None)
            self.forms[-1]["fields"].append(f)
            if tag == "textarea":
                self._ta = f
        elif tag == "option" and self.forms and self.forms[-1]["fields"]:
            cur = self.forms[-1]["fields"][-1]
            cur.setdefault("options", []).append(a.get("value", ""))
            if "selected" in a:
                cur["value"] = a.get("value", "")

    def handle_data(self, data: str) -> None:
        if self._ta is not None and data.strip():
            self._ta["value"] = data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea":
            self._ta = None


def _ssr(html_text: str) -> dict[str, Any] | None:
    """State of the Vue forms: fields live in <script data-ssr-state>, not in the HTML."""
    m = re.search(r"data-ssr-state[^>]*>(.*?)</script>", html_text, re.S)
    return json.loads(_html.unescape(m.group(1))) if m else None


def _verdict(body: str) -> str:
    """Rails returns validation errors with HTTP 200 and a JS body — without this, 200 lies."""
    errs = re.findall(r"validation-error[^>]*>([^<]+)<", body)
    if errs:
        return "NOT SAVED: " + "; ".join(dict.fromkeys(errs))
    # success is either notify('...успешно...') or a redirect to the listing page
    ok = "успешно" in body or "document.location.href" in body
    return "SAVED" if ok else "UNCLEAR, verify via /<login>/print"


@mcp.tool()
def habr_get(path: str, limit: int = 20000, raw: bool = False) -> str:
    """GET a page or JSON endpoint of career.habr.com. path looks like '/settings/profile'.

    HTML is stripped of script/style/svg — they are 97% of the page bytes (fonts,
    mixpanel) and carry no meaning. raw=True returns it untouched: needed when
    grepping through JS chunks.
    """
    r = http().get(path)
    body = r.text
    if not raw and body.lstrip().startswith("<"):
        body = re.sub(r"<(script|style|svg)\b.*?</\1>", "", body, flags=re.S | re.I)
    tail = "" if len(body) <= limit else f"\n...[truncated, {len(body)} chars total]"
    return f"HTTP {r.status_code} {r.url}\n{body[:limit]}{tail}"


def _vue_fields(ssr: dict[str, Any]) -> list[tuple[str, str]]:
    """Fields rendered by Vue: absent from the HTML, but Rails expects them in the body.

    Without them a PATCH wipes skills/specializations/languages and breaks dates.
    """
    out: list[tuple[str, str]] = []
    for key, name in (("selectedSkills", "skillsFieldName"),
                      ("selectedCategories", "categoryFieldName")):
        field = ssr.get(name)
        for v in ssr.get(key) or []:
            out.append((field, str(v["value"] if isinstance(v, dict) else v)))
    for lang in ssr.get("selectedLanguages") or []:
        out += [("user[foreign_languages][][language_id]", str(lang["languageId"])),
                ("user[foreign_languages][][grade_id]", str(lang["gradeId"]))]
    prefix = ssr.get("fieldName")  # experience / university_education
    for key, part in (("startDate", "start_date"), ("endDate", "end_date")):
        d = ssr.get(key)
        if prefix and d:
            y, m, _ = d.split("-")
            out += [(f"{prefix}[{part}(1i)]", y), (f"{prefix}[{part}(2i)]", str(int(m))),
                    (f"{prefix}[{part}(3i)]", "1")]
    return out


def _fields(path: str) -> tuple[str, list[tuple[str, str]]] | None:
    """First meaningful form on the page -> (action, current fields as pairs).

    Pairs, not a dict: Rails arrays (skills[], languages[]) repeat the same name.
    """
    page = http().get(path).text
    p = _Forms()
    p.feed(page)
    for f in p.forms:
        if f["action"].endswith("sign_out") or not f["fields"]:
            continue
        # a field with no 'value' is not submitted by the browser (empty checkbox) — nor by us
        base = [(x["name"], x["value"]) for x in f["fields"] if "value" in x]
        ssr = _ssr(page)
        return f["action"], base + (_vue_fields(ssr) if ssr else [])
    return None


@mcp.tool()
def habr_form(path: str) -> str:
    """Schema of the forms on a page: action, method, field names, select options.

    The main way to learn what an edit page accepts, instead of reading the whole
    HTML. Some pages are rendered by Vue — their fields come from data-ssr-state and
    are appended below the HTML forms.
    """
    p = _Forms()
    page = http().get(path).text
    p.feed(page)
    out = []
    for f in p.forms:
        if f["action"].endswith("sign_out"):  # on every page, pure noise
            continue
        names = ", ".join(
            x["name"]
            + (f"={x['value']!r}" if "value" in x else "")
            + (f" of {x['options']}" if "options" in x else "")
            for x in f["fields"]
        )
        out.append(f"{f['method']} {f['action']}\n  {names or '(no fields)'}")
    ssr = _ssr(page)
    if ssr:  # the Vue half of the form: skills, specs, languages, dates — not in the HTML
        # groups/sOptions are tens of KB of reference data, they eat the whole output
        useful = {k: v for k, v in ssr.items()
                  if k not in ("groups", "sOptions", "hhImport", "userBoosterStatus")}
        out.append("data-ssr-state (Vue fields of this form, sent by habr_submit):\n  "
                   + json.dumps(useful, ensure_ascii=False)[:3000])
    return "\n\n".join(out) or "no forms found (authentication required?)"


@mcp.tool()
def habr_submit(path: str, data: dict[str, Any], method: str = "POST") -> str:
    """Submit the form found on `path`, overriding only the fields given in `data`.

    Every other field is resent with its current value, otherwise Rails blanks it.
    Action and method come from the form itself; Vue fields (skills, specializations,
    languages, dates) are filled in automatically. Field names come from habr_form, e.g.
    habr_submit("/profile/specialization", {"user[salary]": "500000"}).

    The reply starts with SAVED / NOT SAVED: <reason> — Rails returns validation
    failures with HTTP 200, so the status code alone cannot be trusted.

    If the page has no form (action links such as deleting an experience entry), `data`
    is sent as-is to `path` with the given method.
    """
    global _csrf
    found = _fields(path)
    if found:
        action, form = found
        override = {k: str(v) for k, v in data.items()}
        # pairs, not a dict: skills[]/languages[] repeat one name. A key passed in
        # data collapses all of its occurrences into a single new value.
        fields, replaced = [], set()
        for k, v in form:
            if k not in override:
                fields.append((k, v))
            elif k not in replaced:
                replaced.add(k)
                fields.append((k, override[k]))
        fields += [(k, v) for k, v in override.items() if k not in replaced]
        method = dict(fields).get("_method", "POST")
        path, data = action, [(k, v) for k, v in fields if k != "_method"]

    def send() -> httpx.Response:
        # httpx data= only accepts a Mapping, while Rails arrays (skills[]) repeat
        # the name — so encode the pairs ourselves.
        body = (urlencode(data, encoding="utf-8") if isinstance(data, list)
                else urlencode(list(data.items()), encoding="utf-8"))
        return http().request(
            method.upper(), path, content=body,
            headers={"X-CSRF-Token": csrf(), "Referer": BASE + path,
                     "X-Requested-With": "XMLHttpRequest",
                     "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )

    r = send()
    if r.status_code == 422:  # Rails: stale CSRF — the token lives with the session
        _csrf = None
        r = send()
    return f"{_verdict(r.text)} HTTP {r.status_code} {method.upper()} {r.url}\n{r.text[:2000]}"


@mcp.tool()
def habr_whoami() -> str:
    """Check that the cookie is alive: returns the current user's login, or 'anonymous'."""
    html = http().get("/").text
    m = re.search(r'class="menu-head[^"]*" href="/([\w.-]+)"', html)
    if not m:
        return "anonymous (cookie expired or not set)"
    name = re.search(r'class="menu-head__name">([^<]+)', html)
    return f"logged in: {m.group(1)}" + (f" ({name.group(1)})" if name else "")


class _Answer(BaseModel):
    # not required: the Hermes client answers an elicitation approve/decline with an
    # empty content, and a required field fails validation instead of replying
    answer: str = Field("", description="The user's answer")


@mcp.tool()
async def habr_ask(question: str, ctx: Context) -> str:
    """Ask the user for data that is not in the profile: a link to their hh.ru resume,
    a company name, an employment period. Ask — never invent: this is their resume.
    """
    if getattr(ctx.client_capabilities, "elicitation", None) is None:
        return "client does not support elicitation — ask the user yourself, in chat"
    r = await ctx.elicit(question, _Answer)
    if r.action != "accept":
        return f"the user answered: {r.action}"
    return r.data.answer or "client returned an empty answer — ask the user yourself, in chat"


def demo() -> None:
    """Checks the non-trivial parts: form parser (offline) + a live csrf scrape."""
    p = _Forms()
    p.feed('<form action="/x" method="post"><input type="hidden" name="authenticity_token" '
           'value="t"><input type="hidden" name="_method" value="patch">'
           '<input name="user[first_name]" value="Кирилл">'
           '<textarea name="user[about]">я тут</textarea>'
           '<select name="user[qid]"><option value="3"><option value="4" selected></select>'
           '</form>')
    assert len(p.forms) == 1, p.forms
    f = p.forms[0]
    assert (f["action"], f["method"]) == ("/x", "POST"), f
    assert [x["name"] for x in f["fields"]] == [
        "authenticity_token", "_method", "user[first_name]", "user[about]", "user[qid]"], f
    assert f["fields"][0].get("value") is None, "the csrf token must not leak into the schema"
    assert f["fields"][1]["value"] == "patch", "Rails needs _method"
    assert f["fields"][3]["value"] == "я тут", "textarea text lost -> submit would blank it"
    assert f["fields"][4] == {"name": "user[qid]", "tag": "select",
                              "options": ["3", "4"], "value": "4"}, f["fields"][4]

    # merge baseline: valueless fields (empty checkbox) must not clobber the hidden pair
    p2 = _Forms()
    p2.feed('<form action="/y" method="post"><input type="hidden" name="u[remote]" value="0">'
            '<input type="checkbox" name="u[remote]" value="1">'
            '<input type="checkbox" name="u[relo]" value="1" checked></form>')
    base = {x["name"]: x["value"] for x in p2.forms[0]["fields"] if "value" in x}
    assert base == {"u[remote]": "0", "u[relo]": "1"}, base

    # Vue fields: without them a PATCH wipes skills/languages, and 200 lies about success
    ssr = {"skillsFieldName": "user[user_skills_ids][]",
           "selectedSkills": [{"value": 12}, {"value": 245}],
           "categoryFieldName": "user[raw_specialization_ids][]",
           "selectedCategories": [2, 4],
           "selectedLanguages": [{"languageId": 1, "gradeId": 4}],
           "fieldName": "experience", "startDate": "2024-08-01", "endDate": None}
    v = _vue_fields(ssr)
    assert ("user[user_skills_ids][]", "12") in v and ("user[user_skills_ids][]", "245") in v, v
    assert ("user[raw_specialization_ids][]", "4") in v, v
    assert ("user[foreign_languages][][grade_id]", "4") in v, v
    assert ("experience[start_date(2i)]", "8") in v, "month must have no leading zero"
    assert not any("end_date" in k for k, _ in v), "endDate=None -> do not send the fields"
    assert _ssr('<script data-ssr-state type="application/json">{"a":1}</script>') == {"a": 1}

    assert _verdict("window.helpers.notify('Настройки успешно сохранены')") == "SAVED"
    bad = '<span class="validation-error">Не может быть пустым</span>'
    assert _verdict(bad).startswith("NOT SAVED") and "пустым" in _verdict(bad), _verdict(bad)

    t = csrf()
    assert len(t) > 40, f"suspicious csrf: {t!r}"
    print(f"ok: form parser, csrf {t[:12]}...")
    print(habr_whoami())


if __name__ == "__main__":
    import sys
    demo() if "--check" in sys.argv else mcp.run()
