# Tuobot Command Reference — Q6 Drafts

A guide to every slash command in Tuobot, grouped by who uses them and what they do.

**Who can use what**
- **Everyone**: any player in a Tuobot channel.
- **Mod**: members with the channel's **moderator role** (`/channel set moderator_role`), plus everyone who counts as Admin.
- **Admin**: members with the channel's **admin role** (`/channel set admin_role`), anyone with Discord's **Administrator** permission, and the bot owner.
- **Owner**: the bot owner only (`OWNER_ID` in `bot/constants.py`). Admins can't run these.

Discord lists every command for everyone. Tuobot checks who you are when you run one, and tells you privately if you aren't allowed.

**Commands that ask first.** `/channel delete`, `/rating reset`, `/rating snap`, `/stats season_end`, `/stats replace_player` and `/stats nuclear_option` do nothing unless you add `confirm: True`. Without it, they tell you (and only you) what they would do.

---

## 🎮 Player Commands (Everyone)

### Queue Management
| Command | What it does |
|---|---|
| `/add [queues]` | Add yourself to a queue. Without a queue name it adds you to the default queue(s). |
| `/remove [queues]` | Remove yourself from a queue. Without a name it removes you from all the queues you're in on this channel. |
| `/remove_all [player]` | Remove yourself from **every queue across the entire server** (all channels). Mods can pass a player to remove someone else. |
| `/remove_after <time>` | Set a timer to auto-remove yourself from this channel's queues after a duration (e.g. `30m`, `1h`, `1:30:00`). |
| `/who [queues]` | List the players currently added to each queue on this channel. |
| `/recommend <format> [queue]` | Queue not filling? Propose a smaller **casual** game (1v1–5v5) to everyone else in your queue. Accept/Deny buttons, 5-minute window; the first enough accepters get instant random Team A / Team B — no check-in, no draft, no report — and **everyone stays in the queue**, so the real game can still pop. Casual = no MMR, no records. |
| `/matches` | Show all active matches on this channel. |
| `/teams` | Show the teams for the match you're currently in. |

### During Check-in & Draft
| Command | What it does |
|---|---|
| `/ready` | Confirm participation during the check-in stage. Same as reacting ✅ on the check-in message. Only you see the reply. |
| `/notready` | Abort participation during the check-in stage. Same as reacting ❌. |
| `/pick <player>` | Captains only: pick a player from the unpicked pool. The dropdown shows only valid choices, and the bot pings the captain whose turn it is. |

### Substitutes
| Command | What it does |
|---|---|
| `/subme` | Mark yourself as looking for a substitute (run it again to stop). |
| `/subfor <player>` | Take the spot of a player who used `/subme`. |
| `/subauto` | Hand your spot to the next available player in the queue. They take your exact place: same team, and captain if you were one. |

### Personal Settings
| Command | What it does |
|---|---|
| `/auto_ready [duration]` | Auto-confirm check-in for the next match. Default 10 minutes, up to the channel's limit (`max_auto_ready`, 15 minutes unless changed). `/auto_ready off` cancels it. Only you see the reply. |
| `/allow_offline` | Toggle offline immunity: while it's on, going offline won't remove you from queues. It switches itself off when your next match starts. |
| `/switch_dms` | Toggle whether the bot sends you a DM when a queue starts. |
| `/nick <nick>` | Change your server nickname. The `[rating]` prefix is kept when the channel shows ratings in nicknames (`rating_nicks`). |

### Stats & Info
| Command | What it does |
|---|---|
| `/rank [player]` | Show a player's **current-season** rating profile — rating, W/L/A (wins / losses / aborts), win rate, and recent matches. Defaults to yourself. |
| `/profile [player]` | Render a PNG profile card — **all-time across seasons**: career record, peak rating, best-ever streak, a rating sparkline, most-teamed-with and nemesis (current rating + streak show live state). Defaults to yourself. |
| `/leaderboard [page]` | The merged leaderboard — ⏮ ◀ ▶ ⏭ flip pages, 🔍 **Me** jumps to and highlights your row, and 🔁 toggles between **All Players** and the **Season (15+)** board. |
| `/season_leaderboard [page] [min_matches]` | The same merged board, opened on the Season view (15+ matches unless you pass `min_matches`). |
| `/lastgame [queue] [player] [match_id]` | Show the most recent finished match, or the latest one in a queue, the latest one a player played, or a specific match id. |
| `/top [period]` | Top 10 players by matches played: all-time, or the past `day` / `week` / `month` / `year`. |
| `/activity [player]` | Activity heatmap (weekday × hour, **Central time**, last 28 days) for the channel or one player. |
| `/house_points` | Hogwarts House Cup standings — points are earned by winning matches with players from each house. |
| `/stats show [player]` | Matches played per queue, for the channel or one player. |
| `/server <queue>` | Show the configured server for a queue. |

### Reporting Results
| Command | What it does |
|---|---|
| `/report <result>` | Report a match result. Choices: `loss`, `abort`. An abort needs the other captain to also `/report abort`, and is then **recorded** — no winner, no rating change, streaks untouched. |

### Misc / Fun
| Command | What it does |
|---|---|
| `/cointoss [side]` | Flip a coin (call `heads` or `tails` if you like). |
| `/commands` | Send you the link to this command list. |
| `/don` | Pings @Don with the L_Don emoji. |
| `/donbot` | Posts the "age of Donbot is over" gif. |
| `/sitboy` | tuonela's personal gif command (only tuonela can use it). |

---

## 🛡️ Mod & Admin Commands

### `/queue` — Queues
| Command | Who | What it does |
|---|---|---|
| `/queue add_player <player> <queue>` | Mod | Force-add a player to a queue. If a match is in check-in, they go to standby instead. |
| `/queue remove_player <player> [queues]` | Mod | Force-remove a player from a queue (or all queues if none specified). |
| `/queue clear [queue]` | Mod | Empty a queue (or all queues if none specified). |
| `/queue start <queue>` | Mod | Manually start a queue even if not full. |
| `/queue list` | Everyone | List every queue on the channel. |
| `/queue show <queue>` | Everyone | DM you a queue's full configuration. |
| `/queue create_pickup <name> [size]` | Admin | Create a new pickup queue (2–100 players, default 8). |
| `/queue set <queue> <variable> <value>` | Admin | Set a queue config variable (e.g. `priority`, `check_in_timeout`, `ranked`). |
| `/queue delete <queue>` | Admin | Delete a queue. |

### `/match` — Matches
| Command | Who | What it does |
|---|---|---|
| `/match sub_player <player1> <player2>` | Mod | Sub player1 out, player2 in. **If their team loses, the rating penalty goes to player1** (the original who committed). |
| `/swap <player1> <player2>` | Mod | Swap two players. Auto-detects what you want: <br>• Both in the same match → swap their team positions<br>• One in the match, one outside → bring outsider in, send insider out (no penalty redirect)<br>• Both queued together → swap queue positions |
| `/match put <match_id> <player> <team>` | Mod | Put a player on Team A, Team B, or back in the unpicked pool. |
| `/match report <match_id> [winner_team] [abort]` | Mod | Force a match result as a moderator, or record it as aborted. |
| `/match create <queue> <winners> <losers> [aborted]` | Mod | Manually record a finished rating match. |
| `/match force_checkin <match_id>` | Admin | Force all players in a match to ready up immediately. |

### `/stats` — Stats & Seasons
| Command | Who | What it does |
|---|---|---|
| `/stats reset_player <player>` | Mod | Reset one player's season state (rating, record, streak). Their all-time history is preserved. |
| `/stats undo_match <match_id>` | Mod | Undo a finished match — reverses all rating changes, streaks **and any house points it awarded** (via the `house_awards` ledger). Only works for matches from the current season. |
| `/stats season_start` | Admin | Start a new season — turns ranked back ON for all queues, announces in the channel. |
| `/stats season_end [min_matches] [confirm]` | Admin | End the season — posts the standings embed, season highlights (incl. win/loss streaks), and the House Cup winner, then disables ranked and resets the season board (ratings **and** house points). **All-time match history is preserved** for `/profile` and career stats. Default 15 minimum matches. Asks first. |
| `/stats house_points_reset` | Admin | Reset all four Hogwarts house point totals to zero. |
| `/stats replace_player <player1> <player2> [confirm]` | Admin | Move player1's rating and match history onto player2 (used when someone changes accounts). Asks first. |
| `/stats nuclear_option [confirm]` | Owner | ☢️ **FULL wipe** — ratings *and* all-time match history. Unlike `season_end`, nothing survives and there is no undo. Asks first. |

### `/rating` — Rating Adjustments
| Command | Who | What it does |
|---|---|---|
| `/rating seed <player> <rating> [deviation]` | Mod | Set a player's rating manually. |
| `/rating penalty <player> <points> [reason]` | Mod | Subtract points from a player's rating. |
| `/rating hide_player <player>` | Mod | Hide a player from the leaderboard. |
| `/rating unhide_player <player>` | Mod | Show a hidden player on the leaderboard again. |
| `/rating snap [confirm]` | Admin | Snap every rating down to its rank's threshold (e.g. 1450 → Gold 1400). Asks first. |
| `/rating reset [confirm]` | Owner | Blank every rating on the channel. `/stats season_end` is the normal way to reset a season. Asks first. |

### `/noadds` — Queue Bans
| Command | Who | What it does |
|---|---|---|
| `/noadds add <player> [duration] [reason]` | Mod | Ban a player from joining queues (2 hours if no duration is given). |
| `/noadds remove <player>` | Mod | Lift a player's ban. |
| `/noadds list` | Everyone | Show everyone currently banned. |

### `/phrases` — Player Phrases
| Command | Who | What it does |
|---|---|---|
| `/phrases add <player> <phrase>` | Mod | Add a custom phrase that gets shown when this player adds to a queue. |
| `/phrases clear <player>` | Mod | Remove all phrases for a player. |

### `/channel` — Channel Setup
| Command | Who | What it does |
|---|---|---|
| `/channel enable` | Discord Administrator | Enable the bot on this channel. |
| `/channel disable` | Discord Administrator | Turn the bot off on this channel until the bot restarts. Settings are kept. |
| `/channel delete [confirm]` | Discord Administrator | Delete this channel's settings and **every queue on it**, and disable the bot here. Match history and ratings are kept. Asks first. |
| `/channel show` | Everyone | DM you the channel configuration. |
| `/channel set <variable> <value>` | Admin | Set a channel config variable. |
| `/channel cleanup [confirm]` | Owner | ☢️ Purge configs for channels that were **deleted from Discord** before `channel delete` was run in them (they otherwise linger forever, logging "Could not reach a text channel" at every boot). Dry-run listing by default; `confirm: True` deletes. Match history untouched. |

### Owner only
| Command | What it does |
|---|---|
| `/autoreply <on\|off\|status>` | 🪦 The out-of-office prank: while **on**, anyone who @-pings Scourage gets the next line of a story (in order, wrapping; `{pinger}` lines address them by name), one reply per person per 2 minutes. **on** restarts the story from line 1; state survives restarts. Lines live in `AUTOREPLY_LINES` in `constants.py`. |

---

## 📌 Key Concepts

**Queue priority**: Set via `/queue set <queue> priority <number>`. When a lower-priority queue pops, players stay in higher-priority queues. Recommended: `6v6-ranked = 100`, `bonanza = 80`, others = `0`.

**Check-in**: When a queue pops, the check-in message pings everyone who still has to ready up. React ✅ / ❌ or use `/ready` / `/notready`.

**Standby pool**: If a queue is in check-in, new players adding go to standby. At 2/3 of the check-in time, standby players are pulled in as additional candidates — the first to ready up gets the spot. Standby players don't get check-in violations.

**Captains**: Picked automatically every match — there is no Captain role, `/capfor`, or `/capme` anymore. Anyone in the lobby can be chosen **except** players holding the **bar** role (the captains ban list) and players with fewer than **25 games** on their profile. If fewer than two queued players qualify, the most experienced non-barred players fill in. The queue's `pick_captains` mode (smart / by rating / fair pairs / random) then chooses the pair from that pool.

**Ranks**: Chad 0 · Wood 600 · Iron 800 · Bronze 1000 · Silver 1200 · Gold 1400 · **Platinum 1600** · Diamond 1800 · Champion 2000 · Star ★ 2200+ (Oct 2026 ladder). Weekly decay for inactive players stops at their rank's threshold.

**Daily reset**: Every queue is emptied at **4:00 AM Central** each day (the bot posts a short notice in any channel where someone was removed). Active matches aren't affected — just re-add.

**Restarts**: Queues and matches in progress are saved and come back after a restart or redeploy, including a series that's between games. The bot posts a short notice in the match channel when it restores one.

**Hogwarts houses**: Captains' Discord house roles determine team names. The winning team of a **ranked** match awards house points: captain = 10, other players = 5. Players with no house role contribute nothing. Totals feed the House Cup and reset each season.

**Aborts (there are no draws)**: Q6 matches always have a winner — so the "no winner" outcome is an **abort**. Confirmed aborts are recorded on the books (the match id keeps its row), count as a played match, change nobody's rating, and leave win streaks untouched. The third number in every record (W-L-**A**) is aborts. Mods can reverse one with `/stats undo_match` like any result.

**Milestones & rank-ups**: Ranked results embeds automatically celebrate the big moments — your **50th / 100th / 250th / 500th / 1000th** career match, climbing into a **new rank** (with a special call-out the *first time ever*), and setting a **new all-time best win streak** (W4+). All powered by permanent match history.

**Seasons**: Each season tracks ratings/stats independently. `/stats season_end` resets the season board (ratings + house points), posts the standings, highlights, and House Cup, then turns ranked off; **match history is kept forever** (stamped with a `season` number) to power all-time stats like `/profile`. `/stats season_start` re-enables ranked and starts the new season counter.

**Fill-in subs**: With `/match sub_player`, the sub's own rating is never at risk. If their team wins, they get the win as normal; if their team loses, the loss and its rating drop go to the player they replaced. An abort costs nobody anything. Use `/swap` instead if you want a clean, penalty-free swap.
