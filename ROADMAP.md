# astro-bot Roadmap

This document is the product roadmap source of truth. `README.md` can summarize user-facing scope, and `AGENTS.md` can summarize agent/development context, but roadmap priority changes should be reflected here first.

## Product Principle

astro-bot is not only a Milky Way planner. It should help Taiwan photographers decide whether a sky or astronomy scene is worth going out for, based on reliable astronomy calculations, weather conditions, landscape constraints, and practical photography risk.

The bot should support:

- 深空與星野：銀河、星座、星雲、星系、流星雨、彗星等
- 日月行星運行景象：月出月落、月相、日月方位、行星與月亮接近、行星可見性等
- 天氣與地景條件：雲量、透明度、視寧度、結露、霧、雲海、海岸潮汐等
- 攝影決策：去哪裡、何時拍、風險是什麼、備案地點與器材提醒

新增日月行星題材時必須先接入可靠天文資料來源與計算邏輯；在資料未完成前，仍須維持現有攔截與「不猜測」原則。

## Current Product Scope

- LINE Bot on Render, implemented primarily in `main.py`.
- Natural-language Chinese queries for Taiwan astrophotography planning.
- Approved Taiwan location database in `data/taiwan_locations.json`.
- Skyfield-based calculations for target altitude/azimuth, astronomical twilight, moonrise/moonset, moon phase, dark-sky windows, and Milky Way composition direction.
- Open-Meteo weather inputs for cloud cover, temperature, humidity, dew point, wind, and visibility where available.
- OpenRouter-powered intent parsing and reply generation.
- Google Sheets logging for query records and user feedback/wishlist input.
- Quick Reply service menu triggered by `選單`, `功能`, `服務`, `menu`, or `/menu`.

## Operating Guardrails

- Astronomy calculations and coordinates must come from code or trusted data sources. The LLM may explain and summarize, but must not invent positions, weather, seeing, transparency, tides, or location coordinates.
- Unsupported or not-yet-integrated topics must be intercepted or answered with explicit data limitations.
- Weather is the first go/no-go filter. Bad weather should produce a clear no-go conclusion instead of a long optimistic astronomy analysis.
- When target matching fails, return an empty target set or ask for clarification; do not fall back to the full target catalog.
- Production changes must pass the dry-run gate in `HERMAS_AGENT.md` before commit/push/deploy.

## Near-Term Priority: Phase 3A

Focus: upgrade from single-place analysis to practical outing decisions using mostly existing data.

| Priority | Feature | Notes |
| --- | --- | --- |
| Highest | Confidence score / CCI | Add a quantitative confidence indicator beyond ✅/⚠️/❌. |
| Highest | Red-team risk logic | Make replies explicitly state the strongest reason not to go, reducing over-optimistic conclusions. |
| Highest | Location comparison mode | Support queries like `合歡山 vs 阿里山`, comparing CCI, dark windows, moonlight, cloud cover, dew/fog risk, and transparency if available. |
| High | Tonight / weekend best location | Rank approved locations for questions like `今晚哪裡最好拍` or `這週末去哪裡`. |
| High | Multi-subject decision framework | CCI should work for Milky Way, nebulae, meteor showers, moon scenes, planetary/moon events, fog, and cloud-sea scenes. |

Already completed:

- Dew/fog threshold tightened to `T - Td < 1.5°C`.
- Dynamic formatting hides Milky Way composition details unless relevant.
- Basic exposure suggestions are included when conditions are suitable.

## Phase 3B: Planning UX And Data Operations

| Priority | Feature | Notes |
| --- | --- | --- |
| High | Location wishlist review flow | Review Google Sheets location wishes, add sources/coordinates/aliases/access notes, then promote approved entries into `data/taiwan_locations.json`. |
| Medium | Backup location suggestions | Recommend nearby approved alternatives when lowland fog or local cloud risk is high. |
| Medium | Cloud sea / fog mode | Build a distinct judgement style for `想拍雲海` and `會不會起霧`, initially using humidity, dew point spread, elevation, and known place traits. |
| Medium | Sun/moon/planet scene planning | Add moon/sun direction, moon phase, planet visibility, and conjunction topics only after reliable data and calculations are in place. |
| Medium | 15-day calendar summary | Compress the 15-day weather assessment into daily go/no-go ranking plus top details. |
| Medium | Reply speed optimization | Reduce the current 30-60 second path by combining LLM calls, caching, or avoiding unnecessary generation. |
| Low | Light pollution index | Add static sky brightness as a low-weight CCI factor after data is sourced. |

## Phase 4: Personalization And Subscription

| Priority | Feature | Notes |
| --- | --- | --- |
| High | User location/device memory | Store common locations, focal length, camera model, tracker availability, and preferred shooting style. |
| High | Saved observation plans | Let users save plans like `6/20 合歡山銀河` and ask for later updates. |
| Medium | Subscription push | Monitor a date range and push when conditions become favorable. |
| Medium | Closed-loop learning | Use feedback to improve location-specific forecast trust and support exceptional-event markers. |

## Deferred Until Data Or Demand Is Proven

| Priority | Feature | Reason |
| --- | --- | --- |
| Validate first | Layered cloud data source | Evaluate Taiwan coverage, reliability, cost, and licensing before integrating Clear Outside or alternatives. |
| Validate first | Vertical humidity gradient | Valuable for cloud sea/fog, but only after data source and accuracy are validated. |
| Later | Tide data | Build when coastal nightscape demand becomes strong enough. |
| Later | Meteoblue seeing index | Existing 7Timer seeing/transparency should be used first for CCI experiments. |
| Later | Real-time comet coordinates | Use wishlist demand to decide whether JPL Horizons integration is worth the complexity. |
| Later | CCTV / satellite image verification | High risk of overinterpretation; defer until the core decision engine is mature. |

## Dropped From Core Roadmap

| Feature | Reason |
| --- | --- |
| Drone flight safety warnings | Pulls the product into flight safety and legal responsibility beyond astronomy photography planning. |
| Restricted-area database | Local rules change often; use general reminders instead of maintaining a high-liability database. |

## Success Criteria

- Users can ask whether, where, and when to go shooting, not only whether one target is above the horizon.
- Replies clearly separate hard data, uncertainty, and practical recommendation.
- The bot says "no data" or asks for clarification when reliable inputs are missing.
- Bad-weather and high-risk cases produce decisive no-go guidance.
- Location comparison and best-location queries produce concise, defensible rankings.
