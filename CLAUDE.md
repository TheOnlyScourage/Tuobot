# CLAUDE.md

This file provides guidance to Claude (and Claude Code) when working with code in this repository.

## Project

**Tuobot** — a Discord pickup-queue bot for a **Q6 (Quidditch 6v6)** community, purpose-built for the LEAGUE7 server. It began as a fork of PUBobot2 → NammaPUBobot (an AoE2 pickup bot) and has been progressively reshaped into a Quidditch-specific bot with a custom MMR system, Hogwarts house points, and specialty-role features. Built with **Python 3.11** (Railway `Dockerfile` ships `python:3.11-slim`; `ruff.toml` targets `py311`), **nextcord** (Discord library), **aiomysql**, and **MySQL**.

A sibling bot, **Niffler** (formerly Q6 Cards Bot — Scourage's separate Node.js card-collecting game, its own repo), serves the same community. A shared economy between the two is the one remaining roadmap item (see **Roadmap**).

## Running the Bot

```bash
# Install dependencies
pip3 install -r requirements.txt

# Configure: copy and fill in credentials (config.cfg is raw Python, loaded via SourceFileLoader)
cp config.example.cfg config.cfg

# Run directly
python3 PUBobot2.py

# Or via the Railway wrapper (generates config.cfg from env vars, then runs the bot)
python3 start.py
```

`PUBobot2.py` is the entrypoint; Railway runs `start.py` (its `Dockerfile` CMD). In practice the bot is edited via the GitHub web editor and auto-deploys to Railway.

## Linting

```bash
# Lint — config in ruff.toml (line-length 120, TAB indentation, target py311)
ruff check .
```

## Tests

```bash
# Run the suite — needs ONLY pytest (no nextcord/aiomysql/DB)
pytest tests/ -v
```

`tests/` loads the **pure** modules by path — `mmr_engine` (hand-computed + golden 6v6 scenarios locking the MMR formula), `captain_selection` (bar-role / 25-game eligibility tiers + smart pair scoring), `milestones`, the rank ladder, `daily_clear_rules` (the DST-safe 4 AM clock), `autoreply_rules` and the `/recommend` rules — and adds AST/text **wiring guards** that fail CI when a partial paste-over severs a call chain (e.g. `test_manual_match.py`'s three-hop `/match create` keyword check, `test_captain_role_removed.py`). `tests/conftest.py` stubs the `bot` package so `bot/__init__.py` (which needs Discord + MySQL at import time) never runs — keep new tests dependency-free the same way. If a golden test fails after an intentional balance change, update the golden **and announce the change**.

The Nov 2026 audit added one guard file per batch of fixes. Most pull single functions out of the source with `ast` and run them against small fakes:
- `test_parse_duration.py` — duration parsing (overflow, year length, hostile input) · `test_state_restore.py` — save/restore of queues and matches across a redeploy · `test_daily_clear.py` — the 4 AM clear carries on past one broken channel
- `test_match_flow.py` — check-in's single exit, standby, draft subs, crashed-match cleanup
- `test_stats_integrity.py` — transactions, the fill-in penalty, undo, season stamping, atomic match ids, indexes
- `test_cleanup.py` — the dead code stays gone, background work goes through `spawn`, a failed match start puts the lobby back in the queue, `/stats replace_player` is one transaction
- `test_admin_and_config.py` — permissions, confirm-before-destroying, owner locks, the `start.py` → `config.cfg` round trip, removed settings, the crash exit code, and repo-wide wiring checks: every `bot.commands.X` exists, every `__all__` name exists, imports between the bot's own files resolve, and **`README.md` lists every slash command** (it's what `/commands` links to — add a row when you add a command)

Test files **must** be named `test_*.py` — pytest silently skips anything else.

CI (`.github/workflows/ci.yml`) runs `ruff check .`, `pytest tests/ -v`, and a docker build on every PR. Keep the tree green on all three.

## Architecture

### Boot sequence
`PUBobot2.py` is the entrypoint. It:
1. Loads `core/config.py` (imports `config.cfg` as a Python module via `SourceFileLoader`)
2. Connects to MySQL via `core/database.py` → `core/DBAdapters/mysql.py` (aiomysql pool), retrying for about a minute if the database isn't up yet
3. Runs `bot/db_init.py::init_all_tables` so every table exists (then a one-off NULL-deviation fix that needs `qc_players`)
4. Imports `bot/`, which registers all commands and event handlers
5. Starts the asyncio loop as three supervised tasks: the health-check web server, a 1-second `think()` tick, and the Discord client. If a supervised task crashes, the process saves state and exits with code **1**, so Railway's `ON_FAILURE` restart policy brings it back; a SIGTERM (redeploy) shuts down cleanly with code 0

### Core layer (`core/`)
- **`config.py`** — loads `config.cfg` as a Python module (not INI/YAML — raw Python), then converts each key in `_SCHEMA` to its type with `_coerce` (an empty or non-numeric int keeps its default instead of crashing). Keys outside `_SCHEMA` are ignored
- **`client.py`** — `DiscordClient` subclass of `nextcord.Client`; custom event system allowing multiple handlers per event (`@dc.event`)
- **`database.py`** → **`DBAdapters/mysql.py`** — async MySQL access (aiomysql pool). The single adapter instance is `core.database.db`
- **`cfg_factory.py`** — generic typed-config system (`CfgFactory`): per-channel and per-queue settings stored in MySQL, used by both `QueueChannel` and `PickupQueue`
- **`console.py`** — logging (`log`; `LOG_LEVEL` is case-insensitive and also accepts ERROR / WARNING / OFF), **`locales.py`** — translation lookups (`gt`), **`utils.py`** — shared helpers (`find`, `get`, `get_nick`, `seconds_to_str`, `parse_duration`, `spawn` for background work, `send_dm`, etc.)

### Bot layer (`bot/`)
- **`bot/__init__.py`** — global state: `queue_channels` (the central `channel_id → QueueChannel` dict), `active_matches`, `active_queues`, `expire`, plus re-exports (`Match`, `PickupQueue`, `QueueChannel`, `Context`, `Exc`, `commands`, `stats`, …)
- **`bot/main.py`** — the run loop and **state persistence**: `save_state()`/`load_state()` write to a MySQL `saved_state` table (survives Railway redeploys) with a local `saved_state.json` fallback; also `update_rating_system()`. `save_state()` does nothing until `load_state()` has run (`bot.bot_was_ready`): the think loop starts before `on_ready`, and its periodic save used to overwrite the saved state with an empty one on every boot. `load_state()` never raises and restores item by item; a restored match keeps its id, team names, captains, fill-in subs, season and party-code listener, so a series can continue across a redeploy
- **`bot/db_init.py`** — one place that ensures all tables exist at startup
- **`bot/constants.py`** — centralized IDs and tunables: Discord role/emoji IDs, rank-emoji thresholds, MMR params, house-point values, specialty-role maps
- **`bot/events.py`** — Discord event handlers: `on_ready` loads queue channels from the DB; `on_think` runs the match / expire / noadds / daily-clear / stats-jobs / auto-ready ticks plus a periodic `save_state()` (`bot/alerts.py` registers itself as a second `on_think` handler); `on_message` handles party codes, the @Q ping embed, `/autoreply`, and the two literal `!enable_tuobot` / `!disable_tuobot` channel triggers; `on_presence_update` removes offline players (the older AFK auto-kick was removed)
- **`bot/queue_channel.py`** — `QueueChannel`: a Discord channel with pickup queues; owns its `CfgFactory` config, its `Rating` instance, and its list of `PickupQueue`s
- **`bot/queues/pickup_queue.py`** — `PickupQueue`: a player queue that spawns a `Match` when full (`common.py` holds `QueueResponses`/`Qr`); `daily_clear.py` (+ pure `daily_clear_rules.py`) empties every queue at 4:00 AM Central daily, ticked from `on_think`
- **`bot/match/`** — the `Match` lifecycle (`INIT → CHECK_IN → DRAFT → WAITING_REPORT`):
  - `match.py` (`Match` + `Team`), `check_in.py` (ready-up / race-to-ready / standby fill / abort), `draft.py` (captain picks; every sub path goes through `_swap_in`, which puts the newcomer in the exact slot, captaincy included), `standby.py` (race-to-ready standby fill), `embeds.py` (all match embeds), `captain_selection.py` (**pure** captain eligibility + smart pair scoring — see conventions), `party_code.py` (game-code prompts; listeners are keyed by **match id**, so two matches in one channel don't collide, and are saved with the match), `subbing.py` (pure `/subauto` selection helper)
- **`bot/commands/`** — command implementations (`admin`, `config`, `matches`, `misc`, `queues`, `stats`), star-imported via `__init__.py`; plus `views.py` (nextcord UI Views — currently `LeaderboardView`: button pagination, 🔍 Me, and an optional 🔁 two-board toggle; renders via injected callables so it imports nothing from the bot package)
- **`bot/context/`** — command-context abstraction:
  - `slash/` — the primary interface: command definitions in `commands.py` (thin wrappers over `bot/commands/` via `run_slash()`), autocomplete in `autocomplete.py`, subcommand groups in `groups.py` (`/channel`, `/queue`, `/match`, `/rating`, `/stats`, `/noadds`, `/phrases` — there is no `/admin` group; the Python names are still `admin_*`), `SlashContext` in `context.py`
  - The bot is **slash-only** apart from the two literal `!enable_tuobot` / `!disable_tuobot` triggers in `on_message`. The old `!text`-command `MessageContext` (and the `++` / `--` shorthand it served), the web dashboard's `WebContext` and `core/client.py`'s text-command registry were all removed by Nov 2026.
- **`bot/stats/`** — stats, rating, and season logic:
  - `mmr_engine.py` — **the single source of truth for the MMR formula** (`compute_mmr_changes`): flat captain rewards, upset scaling, streak multipliers
  - `rating.py` — a single `Rating` class: per-channel rating **storage/maintenance** (fetch/seed, admin adjustments, weekly decay, rank snapping, season reset). It does *not* compute match deltas — `mmr_engine` does. Decay stops at, and `/rating snap` rounds down to, the thresholds of the real ladder (`constants.RANK_EMOJIS`), never the channel's `ranks` role table
  - `stats.py` — table setup (incl. indexes) + ranked/unranked match registration + admin undo/reset + leaderboard queries + the weekly decay job (this file uses **spaces**, unlike the rest of the bot). Registration and undo each run in **one transaction**; match ids come from an atomic `LAST_INSERT_ID` counter
  - `season.py`, `season_highlights.py` (end-of-season superlatives incl. win/loss streaks + the House Cup embed), `house_points.py` (Hogwarts House Cup), `checkin_tracker.py` (check-in violations → auto-ban), `noadds.py` (queue bans + phrases)
  - `milestones.py` — **pure** milestone & rank-up detector for ranked results (career-match counts, first-time rank tiers vs the all-time peak, new best streaks); imports only `bot.constants`; fed by `Match._collect_milestones`
  - `profile_card.py` — **pure** Pillow renderer for `/profile` PNG cards (house-themed gradients, ghosted house-emblem watermark from the server's HOUSE_EMOJIS with big-initial fallback, rank colours, all-time sparkline/peak/best-streak) plus the pure data shapers `aggregate_encounters()` (teammate/nemesis) and `summarize_results()` (career W-L-D + best streak); imports only PIL + stdlib, fonts bundled in `assets/fonts/`
- **`bot/alerts.py`** — the "41 alert system": watches active matches and pings the queue when a draft finishes inside the scheduled window
- **`bot/expire.py`** — per-player expire timers; **`bot/exceptions.py`** — the `Exc` exception hierarchy
- **`bot/web.py`** — a small aiohttp server, always started, listening on Railway's `PORT` (default 8080): `/health` (200 only when Discord and the database both answer; `railway.toml`'s `healthcheckPath`) and a plain `/`. The old OAuth config dashboard and its `WS_*` / `DC_CLIENT_*` settings are gone

### Q6-specific feature set
Custom MMR (`mmr_engine`), Hogwarts **house points / House Cup** (awarded on ranked wins, reset per season), **specialty roles** (Seeker / Beater / Keeper) surfaced in embeds and season awards, **captain eligibility** (bar role + 25-game minimum, see conventions), **standby race-to-ready** fill, a **daily 4:00 AM Central queue clear**, a **season** lifecycle (`/stats season_start` ↔ `/stats season_end` with standings, highlights, streaks, and House Cup), **check-in violation** tracking with rolling auto-bans, **party codes**, **milestone & rank-up announcements** on ranked results, **/recommend** (a queued player proposes a smaller *casual* game — Accept/Deny buttons, first-come roster, `PickupQueue.start(players=…, team_size=…, casual=True)` → instant random teams, no check-in/draft/report, players STAY queued; casual matches are never registered), **/autoreply** (owner-toggled out-of-office prank: @-pings of the owner get `AUTOREPLY_LINES` in order with `{pinger}` substitution and a per-pinger cooldown; on/off + cursor persist in the `autoreply` table — a `db_init` step), and the **41 alert** system.

### Utils & scripts
Standalone tools, not imported by the running bot:
- `scripts/backup_db.sh` — DB backup helper

## Key conventions
- **Indentation: tabs** throughout, with one exception — `bot/stats/stats.py` uses 4-space indentation. Match the file you're editing.
- **The MMR formula lives only in `bot/stats/mmr_engine.py`.** `rating.py` is storage/decay; don't reintroduce a second rating path.
- **The rank ladder lives only in `bot/constants.py` (`RANK_EMOJIS`).** `bot/commands/stats.py` used to carry a duplicate copy that silently drifted — it now imports from constants. Oct 2026 ladder: Chad 0 / Wood 600 / Iron 800 / Bronze 1000 / Silver 1200 / Gold 1400 / Platinum 1600 / Diamond 1800 / Champion 2000 / Star 2200. The Discord rank **roles** are a separate, per-channel `ranks` config table (`/channel set`) — code changes to the ladder don't touch it, and it no longer affects decay or `/rating snap`.
- **Captains: no Captain role, no volunteering.** The Captain ROLE system (`CAPTAIN_ROLE_ID`, the `captain_role` pick mode, `captain_streak` cooldowns, the smart-scoring role bonus, `/capfor`, `/capme`) was removed in Oct 2026. `Match.init_captains` narrows the lobby with `captain_selection.eligible_captains` — not holding `constants.CAPTAIN_BAR_ROLE_ID` (the "bar" role = captains ban list) and `>= CAPTAIN_MIN_GAMES` career games on the channel (`stats.career_games_by_id`, prefetched async in `Match.new` and refreshed in `standby.py` after a roster swap) — then applies the queue's `pick_captains` mode to that pool. Fallback tiers: most-experienced non-barred fill in; barred players only if everyone is barred. Legacy stored modes (`captain_role`, `by role and rating`, `random with role preference`) are shimmed onto `smart` / `by rating` / `random`. The `captain_streak` DB table may still exist; nothing reads it.
- Config is a `.cfg` file but is actually **Python source** loaded via `SourceFileLoader`. A new config key needs three entries: `_SCHEMA` in `core/config.py` (type + default), `start.py`'s template (it generates `config.cfg` from Railway env vars), and `config.example.cfg`. The template writes every value as an escaped string literal (`{x!r}`) and `_coerce` converts it — never paste a raw value in (an empty id or a quote in `STATUS` used to crash-loop the boot).
- All DB access is async through **`core.database.db`**. Removing a `CfgFactory` variable is safe for stored data — the loader reads *defined* variables, so a saved value is just ignored — but remove every reader too: `cfg.<removed>` raises `AttributeError` (`test_admin_and_config.py` checks this). Nov 2026 removed 15 settings that did nothing (e.g. `prefix`, `promotion_*`, `emoji_ranks`, the old rating knobs).
- **Transactions:** `async with db.transaction() as tx:` — inside the block, read and write **only through `tx`** (`tx.insert` / `update` / `delete` / `execute` / `select_one`). A `db.` call inside the block runs on another pool connection: it isn't rolled back and can't see the uncommitted rows. Anything raised inside rolls the whole block back.
- **`PubobotException` subclasses `BaseException`**, so `except Exception` does not catch it. Loops and handlers that must survive any error catch `(Exception, bot.Exc.PubobotException)`. `bot/__init__.py` imports `main.py` before `bot.Exc` exists, so module-level code in `main.py` imports from `bot.exceptions` directly.
- **A match is reported once:** `report_loss` / `report_win` / `record_abort` start with `_ensure_active()`, and `finish_match` removes the match from `active_matches` before its first `await`. **Check-in has one exit:** every path that ends a check-in (`finish`, `finish_race`, aborts, timeouts) starts with `if not self._begin_close(): return`, so two endings firing together can't both run.
- **Background work goes through `core.utils.spawn(coro, name=...)`**, never a bare `asyncio.create_task()` / `ensure_future()` whose result is thrown away: asyncio holds tasks only weakly, so those can be garbage-collected mid-flight, and their errors never reached the bot's log. `spawn` keeps the task alive and logs a failure. DM people with `send_dm(member, ...)` — closed DMs are normal, not an error. (`test_cleanup.py` fails on a fire-and-forget task.)
- **Pings go in message content.** Discord never pings a mention inside an embed, so the check-in call-out and the captain's "your pick" ping are sent as `content=`.
- **Destructive slash commands ask first:** give them `confirm: bool = SlashOption(**_CONFIRM)` and return unless `_confirm_or_explain(...)` passes, before doing anything. **Owner-locked** (`constants.OWNER_ID`, checked on top of ADMIN): `/stats nuclear_option`, `/rating reset`, `/channel cleanup`, `/autoreply`. Any `bot.commands.X` a slash wrapper calls must be listed in that module's `__all__` — a stale `__all__` crashes the star-import at boot.
- **`bot.queue_channels`** is the central `channel_id → QueueChannel` dict. State persists to the MySQL `saved_state` table (+ `saved_state.json` fallback) and is restored on startup.
- Deployment target is **Railway** (`railway.toml`, `Dockerfile`, `start.py`).
- **`bot/db_init.py::init_all_tables` is the ONLY startup table-init path.** Any new table or column init MUST be registered as a step there — `init_stats_tables` was once defined-but-never-called, so its season-column ALTER never ran and every ranked registration crashed (July 2026 incident). If you add schema, add the step — and inside async init functions use `await db._ensure_table(...)` (async, does CREATE + column ALTER). The public `db.ensure_table()` is a legacy SYNC wrapper (`loop.run_until_complete`) that is only safe at import time while the loop is idle (noadds pattern); calling it inside a running loop crash-loops the boot (second half of the July 2026 incident).
- **Q6 has no draws — winner-NULL ranked rows are ABORTS.** A confirmed `/report abort` (or admin abort) is RECORDED via `Match.record_abort()`: normal registration, zero MMR (the engine short-circuits winner-NULL), the third record column counts it (the `qc_players` column is still *named* `draws` — schema name only), and streaks are PRESERVED everywhere (register, milestones, season highlights, profile card all skip winner-NULL rows in streak walks). Reversible via `undo_match`. True unregistered voids now only happen pre-live (check-in failures → `cancel()`).
- **Match history is permanent.** `season_end` → `reset_channel()` clears only `qc_players` (+ house points); `qc_matches` / `qc_player_matches` / `qc_rating_history` accumulate across seasons and power all-time stats (`/profile`, future milestones). Season-scoped queries MUST filter on `qc_matches.season` (stamped at registration; NULL legacy rows are backfilled at startup). The only full-history deleters are the explicit `/stats nuclear_option` (`wipe_channel` — **owner-locked** to `constants.OWNER_ID`, admins can't fire it) and per-match `undo_match` (current-season matches only — undoing an old one would take its rating changes off the new season's board).

## Roadmap (agreed, not yet built)

**Shared economy with Niffler** — the one remaining roadmap item. Tuobot *earns*, Niffler *spends*.
- **Shape:** a one-way, append-only payout ledger table in Tuobot's MySQL. Tuobot only ever inserts rows; Niffler (Node.js, keeps its own local SQLite) polls the ledger and credits silver through its `db.addCoins`, idempotent on the Tuobot match id so a re-poll can never double-pay.
- **Payout per completed match** (a Q6 match is a best-of-3): 300 silver base to everyone who played, +200 for the win, +150 for captaining. Win streaks do **not** pay silver.
- **Tuobot's side:** the ledger table (a `db_init` step — no SQL reserved words as column names) and rows written at ranked registration; casual `/recommend` games are never registered, so they never pay. The `house_awards` ledger (awards keyed by match id) is the deliberate dry run of this pattern.
- **Settle before building:** (1) undo — `house_awards` *deletes* its rows on `undo_match`, but Niffler may already have paid out, so the economy ledger needs a compensating negative row instead; (2) whether an abort (winner-NULL) pays the base; (3) how Niffler, which is hosted separately, connects to Tuobot's Railway MySQL.
