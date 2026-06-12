# Lessons Learned - 2026-06-12

## Summary

Today we fixed the production LINE Bot flow after moving `astro-bot` to a proper Render Web Service. The end-to-end test now works:

LINE group message -> Render Web Service `/callback` -> OpenRouter -> Google Sheets -> LINE reply.

The main lesson is that a successful webhook verification only proves that LINE can reach the callback URL. It does not prove that the bot can reply to LINE users.

## What Happened

1. `astro-bot` was originally running as a Render Background Worker.
2. LINE Webhook needs a public HTTP endpoint, so a Render Web Service was required.
3. We created `astro-bot-web` on Render Starter plan and pointed LINE Developers webhook to:

```text
https://astro-bot-web-xlny.onrender.com/callback
```

4. LINE webhook verification succeeded, but user messages initially received no bot response.
5. Render logs showed the callback received the message, then LINE reply failed with:

```text
Authentication failed. Confirm that the access token in the authorization header is valid.
accessToken expired(2)
```

6. The root cause was an expired `LINE_CHANNEL_ACCESS_TOKEN` in the new Render Web Service environment.
7. After reissuing the LINE channel access token and updating `astro-bot-web`, the health check showed `line_token_probe: ok` and the LINE end-to-end test passed.

## Root Cause

The new Web Service had a configured LINE access token, but the token was expired. Because the old code sent LINE replies directly without a safe wrapper, the LINE API 401 error caused `/callback` to return HTTP 500.

Important distinction:

- `LINE_CHANNEL_SECRET` verifies incoming webhook signatures.
- `LINE_CHANNEL_ACCESS_TOKEN` authorizes outgoing reply/push messages.

Webhook verify can pass even when `LINE_CHANNEL_ACCESS_TOKEN` is expired.

## Fixes Added

- Added `app.py` as a Render-compatible import entrypoint fallback.
- Created Render Web Service `astro-bot-web`.
- Confirmed production URL:

```text
https://astro-bot-web-xlny.onrender.com
```

- Added `/healthz` production diagnostics for:
  - Google Sheets connection
  - OpenRouter key probe
  - LINE access token probe
  - deployed git version
- Wrapped LINE `reply_message` and `push_message` calls so expired tokens are logged clearly and do not crash the webhook.
- Confirmed Render auto-deploy from GitHub `main`.

## New Operating Rules

1. Always deploy LINE webhook code to a Render Web Service, not only a Background Worker.
2. After changing LINE webhook URL, run both checks:
   - LINE Developers webhook Verify
   - Real LINE group message test
3. After every production deploy, check:

```bash
curl -sS https://astro-bot-web-xlny.onrender.com/healthz
```

4. Expected healthy signals:

```text
ok: true
google_sheets_connected: true
openrouter_key_probe: ok fields=data
line_token_probe: ok
```

5. If LINE Bot receives messages but does not reply, check in this order:
   - Render logs show `[收到] ...`
   - `/healthz` shows `line_token_probe`
   - `LINE_CHANNEL_ACCESS_TOKEN` belongs to the same Messaging API channel
   - LINE Developers webhook points to `/callback` on `astro-bot-web`
6. Never paste or document raw API keys, LINE tokens, or Google service account secrets. Use fingerprints/status only.

## Mistakes To Avoid

- Do not assume Render Worker can serve LINE webhooks.
- Do not assume LINE webhook Verify means bot replies will work.
- Do not update environment variables only on the Worker when production traffic goes to the Web Service.
- Do not debug OpenRouter or Google Sheets first when LINE logs show `invalid_token`.
- Do not deploy code before dry run checks pass.

## Dry Run And Deploy Checklist

Before pushing production changes:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/astro-bot-pycache python3 -m compileall main.py app.py
git diff --check
git status --short
```

After pushing:

```bash
curl -sS https://astro-bot-web-xlny.onrender.com/healthz
```

Then send a real LINE message and confirm:

- LINE replies with the initial "calculating" message.
- LINE replies with the final answer.
- Google Sheets receives the expected row.
- Render logs do not show LINE API 401 errors.

## Final State

As of the end of 2026-06-12:

- Render Web Service: `astro-bot-web`
- Service ID: `srv-d8ltb1po3t8c73bemlhg`
- Production URL: `https://astro-bot-web-xlny.onrender.com`
- Repo: `dannytsao/astro-bot`
- Branch: `main`
- LINE webhook: `https://astro-bot-web-xlny.onrender.com/callback`
- Health endpoint: `https://astro-bot-web-xlny.onrender.com/healthz`
- End-to-end LINE test: passed
