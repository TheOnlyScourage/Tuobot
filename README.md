# Tuobot Command Reference — Q6 Drafts

A guide to every slash command available in Tuobot, grouped by who uses them and what they do.

---

## 🎮 Player Commands (Everyone)

### Queue Management
| Command | What it does |
|---|---|
| `/add` | Add yourself to a queue. Without a queue name it adds you to the default. |
| `/remove` | Remove yourself from a queue. Without a name it removes you from all the queues you're in on this channel. |
| `/remove_all` | Remove yourself from **every queue across the entire server** (all channels). Mods can pass a player name to remove someone else. |
| `/remove_after [time]` | Set a timer to auto-remove yourself from this channel's queues after a duration (e.g. `30m`, `1h`). |
| `/who` | List the players currently added to each queue on this channel. |
| `/matches` | Show all active matches on this channel. |
| `/teams` | Show the teams for the match you're currently in. |

### During Check-in & Draft
| Command | What it does |
|---|---|
| `/ready` | Confirm participation during the check-in stage. Same as reacting ✅ on the check-in message. |
| `/notready` | Abort participation during the check-in stage. Same as reacting ❌. |
| `/capfor` | Volunteer to be captain on the team you choose. |
| `/capme` | Step down from the captain position you were assigned. |
| `/pick` | Captains only — pick a player from the unpicked pool. The dropdown shows only valid choices. |

### Substitutes
| Command | What it does |
|---|---|
| `/subme` | Request to be substituted out of your current match. |
| `/subauto` | Replace yourself with the next player in the queue. Teams get rebalanced by rating. |
| `/subfor` | Volunteer to take someone else's spot as a substitute. |

### Personal Settings
| Command | What it does |
|---|---|
| `/auto_ready [duration]` | Auto-confirm check-in for the next match. Default 10 minutes. Useful if you're going AFK but still want to play if the queue pops. |
| `/allow_offline` | Toggle whether the bot ignores your offline/idle status when adding you to queues. |
| `/switch_dms` | Toggle whether the bot sends you a DM when a queue starts. |
| `/nick [name]` | Change your server nickname while keeping the `[rating]` prefix. |

### Stats & Info
| Command | What it does |
|---|---|
| `/rank [player]` | Show a player's rating profile — current rating, W/L/D, win rate, and recent matches. Defaults to yourself. |
| `/leaderboard [page]` | Show the rating leaderboard (paged, 12 per page). |
| `/season_leaderboard [page]` | Leaderboard filtered to players with 15+ matches this season. |
| `/lastgame` | Show details of the most recent finished match. |
| `/top` | Show top players ranked by match count. |
| `/activity` | Activity heatmap (weekday × hour, IST). |
| `/house_points` | Hogwarts House Cup standings — points are earned by winning matches with players from each house. |

### Server
| Command | What it does |
|---|---|
| `/server <queue>` | Show the configured server for a queue. |

### Reporting Results
| Command | What it does |
|---|---|
| `/report <result>` | Report a match result. Choices: `loss`, `draw`, `abort`. The other team confirms by also reporting. |

### Misc / Fun
| Command | What it does |
|---|---|
| `/cointoss` | Flip a coin. |
| `/help` | Show channel or queue help text. |
| `/commands` | Show the full command list. |
| `/don` | Pings @Don with the L_Don emoji. |

---

## 🛡️ Moderator Commands

### `/admin queue` — Queue Management
| Command | What it does |
|---|---|
| `/admin queue add_player <player> <queue>` | Force-add a player to a queue. If a match is in check-in, they go to standby instead. |
| `/admin queue remove_player <player> [queue]` | Force-remove a player from a queue (or all queues if none specified). |
| `/admin queue clear [queue]` | Empty a queue (or all queues if none specified). |
| `/admin queue start <queue>` | Manually start a queue even if not full. |
| `/admin queue split <queue> [group_size] [by_rating]` | Split queue players into multiple matches. Optionally sort by rating first. |
| `/admin queue list` | List every queue on the channel. |
| `/admin queue show <queue>` | Show a queue's full configuration. |

### `/admin match` — Match Management
| Command | What it does |
|---|---|
| `/admin match force_checkin <match_id>` | Force all players in a match to ready up immediately. |
| `/admin match sub_player <player1> <player2>` | Sub player1 out, player2 in. **If their team loses, the rating penalty goes to player1** (the original who committed). |
| `/swap <player1> <player2>` | Swap two players. Auto-detects what you want: <br>• Both in the same match → swap their team positions<br>• One in the match, one outside → bring outsider in, send insider out (no penalty redirect)<br>• Both queued together → swap queue positions |
| `/admin match put <player> <team>` | Manually put a player on a specific team (or `unpicked`). |
| `/admin match report <match_id> <winner>` | Force a match result as a moderator. |
| `/admin match create` | Manually record a finished rating match. |

### `/admin stats` — Stats & Season Management
| Command | What it does |
|---|---|
| `/admin stats season_start` | Start a new season — turns ranked back ON for all queues, announces in the channel. |
| `/admin stats season_end [min_matches]` | End the season — posts the standings embed, season highlights (incl. win/loss streaks), and the House Cup winner, then disables ranked and resets all stats (ratings **and** house points). Default 15 minimum matches. |
| `/admin stats house_points_reset` | Reset all four Hogwarts house point totals to zero. |
| `/admin stats reset` | Wipe all stats data for the channel (be careful!). |
| `/admin stats reset_player <player>` | Reset one player's stats. |
| `/admin stats stats_replace_player <player1> <player2>` | Replace player1's stats history with player2 (used when someone changes accounts). |
| `/admin stats undo_match <match_id>` | Undo a finished match — reverses all rating changes. |
| `/admin stats show [player]` | Show channel or per-player stats. |

### `/admin noadds` — Bans
| Command | What it does |
|---|---|
| `/admin noadds add <player> <duration> [reason]` | Ban a player from joining queues. |
| `/admin noadds remove <player>` | Remove a player from the noadds list. |
| `/admin noadds list` | Show everyone currently banned. |

### `/admin rating` — Rating Adjustments
| Command | What it does |
|---|---|
| `/admin rating seed <player> <rating> [deviation]` | Set a player's rating manually. |
| `/admin rating penality <player> <amount>` | Subtract points from a player's rating. |
| `/admin rating hide_player <player>` | Hide a player from the leaderboard. |
| `/admin rating unhide_player <player>` | Show a hidden player on the leaderboard again. |
| `/admin rating reset` | Reset all rating data on the channel. |
| `/admin rating snap` | Snap players' ratings to their rank thresholds. |

### `/douche` (Community Log)
| Command | What it does |
|---|---|
| `/douche add <player> <target>` | Record that one player "douched" another (moderator only). |
| `/douche summary [player]` | Show a player's douche record: received, given, and recent. |
| `/douche leaderboard` | Show the guild's douche leaderboard. |

---

## 👑 Admin Commands

### `/admin channel` — Channel Setup
| Command | What it does |
|---|---|
| `/admin channel enable` | Enable the bot on a new channel. |
| `/admin channel disable` | Disable the bot on a channel. |
| `/admin channel delete` | Delete all configs and stats, and disable the bot. **Destructive.** |
| `/admin channel show` | Show the channel configuration. |
| `/admin channel set <variable> <value>` | Set a channel config variable. |

### `/admin queue` — Queue Creation/Config (Admin)
| Command | What it does |
|---|---|
| `/admin queue create_pickup <name> <size>` | Create a new pickup queue. |
| `/admin queue set <queue> <variable> <value>` | Set a queue config variable (e.g. `priority`, `check_in_timeout`, `ranked`). |
| `/admin queue delete <queue>` | Delete a queue. |

### `/admin phrases` — Player Phrases
| Command | What it does |
|---|---|
| `/admin phrases add <player> <phrase>` | Add a custom phrase that gets shown when this player adds to a queue. |
| `/admin phrases clear <player>` | Remove all phrases for a player. |

---

## 📌 Key Concepts

**Queue priority**: Set via `/admin queue set <queue> priority <number>`. When a lower-priority queue pops, players stay in higher-priority queues. Recommended: `6v6-ranked = 100`, `bonanza = 80`, others = `0`.

**Standby pool**: If a queue is in check-in, new players adding go to standby. At 2/3 of the check-in time, standby players are pulled in as additional candidates — the first to ready up gets the spot. Standby players don't get check-in violations.

**Hogwarts houses**: Captains' Discord house roles determine team names. The winning team of a **ranked** match awards house points: captain = 10, other players = 5. Players with no house role contribute nothing. Totals feed the House Cup and reset each season.

**Seasons**: Each season tracks ratings/stats independently. `season_end` resets everything (ratings + house points), posts the standings, highlights, and House Cup, then turns ranked off. `season_start` re-enables ranked and starts the new season counter.

**Fill-in subs**: When using `/admin match sub_player`, the sub plays for free if they win, but losses are charged to the original player. Use `/swap` instead if you want a clean penalty-less swap.
