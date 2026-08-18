# habr-mcp

MCP server for career.habr.com driven by a browser cookie. The official OAuth API is
read-only, so this writes the way a browser does: Rails forms + `X-CSRF-Token`.

## Cookie

DevTools → Application → Cookies → `career.habr.com` → grab `_career_session`
(and `remember_user_token` so the session lasts longer).

```bash
cp .env.example .env   # put HABR_COOKIE in it
set -a && . ./.env && set +a
uv run --script server.py --check   # → "logged in: <login>"
```

## Wiring it up

```json
{"mcpServers": {"habr": {
  "command": "uv", "args": ["run", "--script", "/home/kirill/Projects/habr-mcp/server.py"],
  "env": {"HABR_COOKIE": "_career_session=...; remember_user_token=..."}
}}}
```

## Tools

- `habr_whoami` — is the cookie still alive
- `habr_get(path, raw=False)` — HTML/JSON of any page; by default strips script/style/svg
  (−70% of the bytes: fonts and mixpanel). Pass `raw=True` when grepping JS chunks.
- `habr_form(path)` — form schema: field names, current values, select options, plus the
  Vue state from `data-ssr-state` (skills, specializations, languages, dates).
- `habr_submit(path, data)` — writes only the fields in `data` and resends the rest as they
  are (Rails writes the whole form, so anything omitted gets blanked). Action and method
  come from the form itself; Vue fields are filled in automatically. The first line of the
  response is `SAVED` or `NOT SAVED: <reason>` — Rails returns validation failures with
  HTTP 200, so the status code alone lies.
- `habr_ask(question)` — ask the user something (MCP elicitation).

```python
habr_form("/profile/specialization")
# POST /profile/specialization
#   _method='patch', user[work_state]='search' of [...], user[salary]='400000', ...
#   data-ssr-state: selectedSkills, selectedCategories, selectedLanguages
habr_submit("/profile/specialization", {"user[salary]": "500000"})
# SAVED HTTP 200 PATCH ...
```

Entry points: `/profile/personal/edit`, `/profile/specialization`,
`/profile/experiences/new`, `/profile/university_educations/new`,
`/profile/additional_educations/new`.

There is deliberately no typed `create_resume`: the page is its own schema.

The cookie lives until roughly September (`remember_user_token`); after that
`habr_whoami` reports "anonymous".

## Tests

```bash
./server.py --check      # form parser, Vue fields, verdicts (offline) + live csrf
./test_ask.py            # elicitation over stdio
./test_submit_live.py    # editing one field must not wipe skills/languages — real profile
./test_tools_live.py     # HTML stripping, Vue in habr_form, validation-error parsing
```

The last three hit the network and edit a live profile (salary there and back),
so they need a working `.env`.
