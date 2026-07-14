# Claude / Cowork Project Guide

This file is kept for Claude Code / Cowork compatibility. Do not treat it as a separate roadmap or deployment source of truth.

## Source Of Truth

- Development workflow and production gates: `HERMAS_AGENT.md`
- Product roadmap and revised priorities: `ROADMAP.md`
- Codex / agent project context: `AGENTS.md`
- User-facing product overview: `README.md`

## Current Alignment

astro-bot is a Taiwan astrophotography LINE Bot. It is not only a Milky Way planner; the product direction is to help photographers decide whether a sky or astronomy scene is worth going out for.

The bot should eventually support:

- 深空與星野：銀河、星座、星雲、星系、流星雨、彗星等
- 日月行星運行景象：月出月落、月相、日月方位、行星與月亮接近、行星可見性等
- 天氣與地景條件：雲量、透明度、視寧度、結露、霧、雲海、海岸潮汐等
- 攝影決策：去哪裡、何時拍、風險是什麼、備案地點與器材提醒

新增日月行星題材時必須先接入可靠天文資料來源與計算邏輯；在資料未完成前，仍須維持現有攔截與「不猜測」原則。

## Next Development Priority

Phase 3A (出勤決策引擎) is complete. All three hard exit gates — CCI, location comparison mode, multi-subject CCI framework — are ✅ in `ROADMAP.md`. Do not re-propose these as upcoming work or restart from this build order.

The project is now in Phase 3B: 規劃 UX 與資料營運. Current build order per `ROADMAP.md`:

1. ✅ User state 持久化儲存 — done 2026-07-14 (`state_store.py`). Do not re-propose this as upcoming work. Note: the specific "reply with coordinates via LINE chat after a restart" flow has logic + unit test coverage but has not itself been exercised end-to-end over a live LINE conversation — the user's actual workflow edits the "自定義地點" Sheet directly instead of replying to the bot's coordinate prompt. This is not a Phase 3B #1 blocker, just an open verification gap worth knowing about.
2. ✅ 回覆速度優化 — done and live 2026-07-14. Weather/seeing fetch is parallelized, full `[耗時]` timing instrumentation is present, Gemini 2.5 Flash reduced measured `generate_reply` latency by 77%, and vectorized Skyfield calculations removed the 117-target open-exploration bottleneck. Four real LINE samples: text open exploration 9.95s; single-day voice end-to-end 9.93s and 10.65s; six-day voice range end-to-end 16.61s. The initial waiting copy now says usually 10–20 seconds with a complex-query caveat. Do not re-propose this as unfinished. Four production samples are still not a statistical P90; continue aggregating Render timing logs before claiming the metric itself is met.
3. ✅ 語音輸入支援 — done and live 2026-07-14, verified over real LINE voice messages (known-location happy path + unknown-location safety fallback both confirmed). Reuses the existing `OPENROUTER_API_KEY` (no separate `GEMINI_API_KEY` needed) via OpenRouter's multimodal audio input to `google/gemini-2.5-flash`. Do not re-propose this as upcoming work.
4. ✅ 自定義地點別名支援 — done 2026-07-14. The 「自定義地點」 Sheet now has a sixth 「別名」 column; existing five-column sheets are migrated on startup. Comma, Chinese comma, ideographic comma, and newline-separated aliases are reloaded into the existing exact-match path and resolve to the canonical custom-location name. Existing `user-provided` rows can refresh aliases; approved locations remain protected from same-name Sheet overrides. This is intentionally not fuzzy or pinyin matching. Do not re-propose this as unfinished.

Two production bugs were also found and fixed alongside #1 on 2026-07-14 (see `CHANGELOG.md`): custom locations added by manually editing the Sheet weren't picked up by an already-running process (fixed with a throttled reload), and `init_sheets()` threw a `NameError` on every single boot due to an import-ordering bug, which had been silently self-healing via query-triggered reconnect and masking the real failure. Both confirmed fixed live.

Do not revive the older priority order that treated Clear Outside, Meteoblue, JPL Horizons, drones, or restricted-area data as immediate core work. Meteoblue and JPL Horizons/MPC are now scheduled (Phase 3B #12 and #11) but not urgent; Clear Outside, drones, and restricted-area data remain deferred or dropped per `ROADMAP.md`.

## Runtime Facts

- Production branch: `main`
- Render service: `astro-bot-web`
- Production URL: `https://astro-bot-web-xlny.onrender.com`
- Health check: `https://astro-bot-web-xlny.onrender.com/healthz`
- LINE webhook: `https://astro-bot-web-xlny.onrender.com/callback`
- Runtime entrypoint: `main.py`
- Render import fallback: `app.py`
- LLM runtime: OpenRouter chat completions
- Runtime model: `OPENROUTER_MODEL` from Render/OpenRouter env when configured
- Default `OPENROUTER_MODEL` fallback: `anthropic/claude-sonnet-4.5`
- Production locations: `data/taiwan_locations.json`, approved entries only

## Non-Negotiable Rules

- Run the dry-run gate in `HERMAS_AGENT.md` before commit/push/deploy.
- Do not invent weather, seeing, transparency, tides, celestial positions, or location coordinates.
- Keep `查詢記錄` and `用戶反饋` Google Sheet formats separate.
- If a place is not reliably resolved, ask for coordinates and log the missing place instead of substituting another location.
- If a target is not supported by reliable data, intercept or clearly state the limitation.
