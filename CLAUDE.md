# CLAUDE.md

This file provides guidance to Claude (and Claude Code) when working with code in this repository.

## Project

**Tuobot** — a Discord pickup-queue bot for a **Q6 (Quidditch 6v6)** community, purpose-built for the LEAGUE7 server. It began as a fork of PUBobot2 → NammaPUBobot (an AoE2 pickup bot) and has been progressively reshaped into a Quidditch-specific bot with a custom MMR system, Hogwarts house points, and specialty-role features. Built with **Python 3.11** (Railway `Dockerfile` ships `python:3.11-slim`; `ruff.toml` targets `py311`), **nextcord** (Discord library), **aiomysql**, and **MySQL**.

A sibling bot, **Niffler** (a.k.a. Q6 Cards Bot, maintained by a collaborator), serves the same community; a shared-economy integration between the two is planned but not yet built.

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

`tests/` holds regression suites for the two **pure** modules: `bot/stats/mmr_engine.py` (hand-computed + golden 6v6 scenarios locking the MMR formula) and `bot/match/captain_selection.py` (both selection strategies, incl. the eligibility-role vs. bonus-role fallback lock). `tests/conftest.py` loads them via importlib with a stubbed `bot` package so `bot/__init__.py` (which needs Discord + MySQL at import time) never runs — keep new tests dependency-free the same way. If a golden test fails after an intentional balance change, update the golden **and announce the change**.

CI (`.github/workflows/ci.yml`) runs `ruff check .`, `pytest tests/ -v`, and a docker build on every PR. Keep the tree green on all three.

## Architecture

### Boot sequence
`PUBobot2.py` is the entrypoint. It:
1. Loads `core/config.py` (imports `config.cfg` as a Python module via `SourceFileLoader`)
2. Connects to MySQL via `core/database.py` → `core/DBAdapters/mysql.py` (aiomysql pool)
3. Imports `bot/`, which registers all commands and event handlers, and runs `bot/db_init.py` to ensure every table exists
4. Starts the asyncio loop with a 1-second `think()` tick alongside the Discord client

### Core layer (`core/`)
- **`config.py`** — loads `config.cfg` as a Python module (not INI/YAML — raw Python)
- **`client.py`** — `DiscordClient` subclass of `nextcord.Client`; custom event system allowing multiple handlers per event, plus the `@dc.command()` / `@dc.event` registry
- **`database.py`** → **`DBAdapters/mysql.py`** — async MySQL access (aiomysql pool). The single adapter instance is `core.database.db`
- **`cfg_factory.py`** — generic typed-config system (`CfgFactory`): per-channel and per-queue settings stored in MySQL, used by both `QueueChannel` and `PickupQueue`
- **`console.py`** — logging (`log`), **`locales.py`** — translation lookups (`gt`), **`utils.py`** — shared helpers (`find`, `get`, `get_nick`, `seconds_to_str`, `parse_duration`, etc.)

### Bot layer (`bot/`)
- **`bot/__init__.py`** — global state: `queue_channels` (the central `channel_id → QueueChannel` dict), `active_matches`, `active_queues`, `expire`, plus re-exports (`Match`, `PickupQueue`, `QueueChannel`, `Context`, `Exc`, `commands`, `stats`, …)
- **`bot/main.py`** — the run loop and **state persistence**: `save_state()`/`load_state()` write to a MySQL `saved_state` table (survives Railway redeploys) with a local `saved_state.json` fallback; also `update_rating_system()`
- **`bot/db_init.py`** — one place that ensures all tables exist at startup
- **`bot/constants.py`** — centralized IDs and tunables: Discord role/emoji IDs, rank-emoji thresholds, MMR params, house-point values, specialty-role maps
- **`bot/events.py`** — Discord event handlers: `on_ready` loads queue channels from the DB, `on_think` runs match/expire/noadds/alerts ticks, `on_presence_update` removes offline players (the older AFK auto-kick was removed)
- **`bot/queue_channel.py`** — `QueueChannel`: a Discord channel with pickup queues; owns its `CfgFactory` config, its `Rating` instance, and its list of `PickupQueue`s
- **`bot/queues/pickup_queue.py`** — `PickupQueue`: a player queue that spawns a `Match` when full (`common.py` holds `QueueResponses`/`Qr`)
- **`bot/match/`** — the `Match` lifecycle (`INIT → CHECK_IN → DRAFT → WAITING_REPORT`):
  - `match.py` (`Match` + `Team`), `check_in.py` (ready-up / race-to-ready / standby fill / abort), `draft.py` (captain picks), `standby.py` (race-to-ready standby fill), `embeds.py` (all match embeds), `captain_selection.py` (captain-role pick logic + streak cooldowns), `party_code.py`, `subbing.py` (pure `/subauto` selection helper)
- **`bot/commands/`** — command implementations (`admin`, `config`, `matches`, `misc`, `queues`, `stats`), star-imported via `__init__.py`; plus `views.py` (nextcord UI Views — currently the button-paginated `LeaderboardView`; renders via injected callables so it imports nothing from the bot package)
- **`bot/context/`** — command-context abstraction:
  - `slash/` — the primary interface: command definitions in `commands.py` (thin wrappers over `bot/commands/` via `run_slash()`), autocomplete in `autocomplete.py`, subcommand groups in `groups.py`, `SlashContext` in `context.py`
  - `message/` — a **minimal** `MessageContext` kept only to support the `++` / `--` add/remove shorthand; the full `!command` system was removed (slash-only)
- **`bot/stats/`** — stats, rating, and season logic:
  - `mmr_engine.py` — **the single source of truth for the MMR formula** (`compute_mmr_changes`): flat captain rewards, upset scaling, streak multipliers
  - `rating.py` — a single `Rating` class: per-channel rating **storage/maintenance** (fetch/seed, admin adjustments, weekly decay, rank snapping, season reset). It does *not* compute match deltas — `mmr_engine` does
  - `stats.py` — table setup + ranked/unranked match registration + admin undo/reset + leaderboard queries + the weekly decay job (this file uses **spaces**, unlike the rest of the bot)
  - `season.py`, `season_highlights.py` (end-of-season superlatives incl. win/loss streaks + the House Cup embed), `house_points.py` (Hogwarts House Cup), `captain_streak.py`, `checkin_tracker.py` (check-in violations → auto-ban), `noadds.py` (queue bans + phrases)
  - `profile_card.py` — **pure** Pillow renderer for `/profile` PNG cards (house-themed gradients, rank colours, all-time sparkline/peak/best-streak) plus the pure data shapers `aggregate_encounters()` (teammate/nemesis) and `summarize_results()` (career W-L-D + best streak); imports only PIL + stdlib, fonts bundled in `assets/fonts/`
- **`bot/alerts.py`** — the "41 alert system": watches active matches and pings the queue when a draft finishes inside the scheduled window
- **`bot/douche.py`** — a light per-guild "douche" moderation log (received/given counts + leaderboard)
- **`bot/expire.py`** — per-player expire timers; **`bot/exceptions.py`** — the `Exc` exception hierarchy
- **`bot/web.py`** — an optional aiohttp server: a health-check endpoint plus an OAuth2 config dashboard (MySQL-backed sessions), gated on `WS_ENABLE` and the OAuth env vars. Off by default

### Q6-specific feature set
Custom MMR (`mmr_engine`), Hogwarts **house points / House Cup** (awarded on ranked wins, reset per season), **specialty roles** (Seeker / Beater / Keeper) surfaced in embeds and season awards, **captain streak** cooldowns, **standby race-to-ready** fill, a **season** lifecycle (`/season_start` ↔ `/season_end` with standings, highlights, streaks, and House Cup), **check-in violation** tracking with rolling auto-bans, **party codes**, the **41 alert** system, and **douche** tracking.

### Utils & scripts
Standalone tools, not imported by the running bot:
- `scripts/backup_db.sh` — DB backup helper

## Key conventions
- **Indentation: tabs** throughout, with one exception — `bot/stats/stats.py` uses 4-space indentation. Match the file you're editing.
- **The MMR formula lives only in `bot/stats/mmr_engine.py`.** `rating.py` is storage/decay; don't reintroduce a second rating path.
- Config is a `.cfg` file but is actually **Python source** loaded via `SourceFileLoader`. New config vars also need entries in `start.py`'s template (it generates `config.cfg` from Railway env vars).
- All DB access is async through **`core.database.db`**. Removing a `CfgFactory` variable is safe — the loader reads *defined* variables, so a dropped column is just orphaned, not fatal.
- **`bot.queue_channels`** is the central `channel_id → QueueChannel` dict. State persists to the MySQL `saved_state` table (+ `saved_state.json` fallback) and is restored on startup.
- Deployment target is **Railway** (`railway.toml`, `Dockerfile`, `start.py`).
- **`bot/db_init.py::init_all_tables` is the ONLY startup table-init path.** Any new table or column init MUST be registered as a step there — `init_stats_tables` was once defined-but-never-called, so its season-column ALTER never ran and every ranked registration crashed (July 2026 incident). If you add schema, add the step.
- **Match history is permanent.** `season_end` → `reset_channel()` clears only `qc_players` (+ house points); `qc_matches` / `qc_player_matches` / `qc_rating_history` accumulate across seasons and power all-time stats (`/profile`, future milestones). Season-scoped queries MUST filter on `qc_matches.season` (stamped at registration; NULL legacy rows are backfilled at startup). The only full-history deleters are the explicit `/admin stats nuclear_option` (`wipe_channel` — **owner-locked** to `constants.OWNER_ID`, admins can't fire it) and per-match `undo_match`.

## Roadmap (agreed, not yet built)

Larger parked designs:
- **Seeker 1v1 snitch report system** — Model C: variable snitch total (up to 5), time-limited with sudden death; agreed formula `dominance_factor = 1 + (margin-1)*0.10 + (total-1)*0.06`, `softness_factor = 1 - (loser/winner)*0.5`, hard cap ±200.
- **Shared economy** — Tuobot earns, Niffler (Glas's bot) spends; one MySQL database; append-only transaction ledger. The `house_awards` ledger + `undo_match` reversal is the deliberate dry run of this pattern.
- **Crash-notification embed** — when `Match.think()` raises and `on_think` drops the match, post a best-effort "Match #X hit an internal error and was cancelled" embed instead of vanishing silently.

Feature TODO (queued from the ideas session):
- **Milestones** — 50th/100th/250th match, first time reaching a rank, new best streak → one appended line on the results embed. **Unblocked**: counts are all-time now that match history is permanent (100 games in one season stopped being realistic).
- **Rank-up announcements** — when a result crosses a rank threshold (from `constants.py`), append "💎 X reached Diamond!" to the results embed.
- **Spectator predictions** — after a draft locks, non-players tap 🅰️/🅱️ to call the winner; track an "Oracle" accuracy leaderboard.
- **MVP voting** — post-report buttons for teammates to vote MVP; cosmetic tally on `/profile` (and a future economy earn hook).
- **Themed captain coinflip** — "The Snitch is released..." flavour embed for first pick / side choice.
- **Projected MMR preview** — at team lock, call `mmr_engine` preview-style: "Team A wins: +62 avg / Team B wins: +81 avg" on the match embed.

### Not in this codebase (removed — don't go looking)
The AoE2/civ-sync stats, the multiple rating engines (Flat / **Glicko2** / **TrueSkill** / AoE2 — now a single `Rating`), the **map / map-voting** system, the full text-command (`!cmd`) system, and the `utils/` folder (the one-off PUBobot CSV migration importer + its DB helpers — migration long done, source CSVs deleted) have all been removed. Stale references may still linger in comments; the code paths are gone. (The old AoE2-era `tests/` folder is also gone — the current `tests/` is the new mmr/captain-selection suite, unrelated to it.)
