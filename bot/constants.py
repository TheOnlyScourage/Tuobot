# -*- coding: utf-8 -*-
"""
Centralized constants for the Q6 Drafts deployment.

Anywhere we used to hardcode a Discord role ID, custom emoji ID, or magic
number lives here. Change a role ID once, get it propagated to every
file that imports from this module.

Layout:
  - OWNER_ID                      — bot owner; gates nuclear admin commands
  - HOUSE_ROLES / HOUSE_EMOJIS    — Hogwarts house assignment
  - SPECIALTY_ROLES               — Quidditch positions (Seeker/Beater/Keeper)
  - Q_PING_ROLE_ID                — role mentioned to trigger the @Q ping embed
  - RANK_EMOJIS / get_rank_emoji  — Q6 rank badge lookup by rating
  - MMR engine constants          — formula parameters used by stats/mmr_engine
  - RECOMMEND_*                   — /recommend formats, window, cooldown
  - AUTOREPLY_*                   — /autoreply target, cooldown, and the story lines
  - House points constants        — per-player and captain award values
"""

# ── Bot owner ────────────────────────────────────────────────────────────────
# Scourage's Discord user id. Commands that can destroy PERMANENT data
# (currently only /admin stats reset → wipe_channel) are gated to this id on
# top of the normal ADMIN check — regular admins can't fire them, by design.
# (cfg.DC_OWNER_ID also exists as an env var, but it only guards the legacy
# !enable/!disable text commands and can't be verified from the repo; this
# hardcoded pin is the deliberate source of truth for nuclear gates.)
OWNER_ID = 310593959506477075

# ── Snitch coin-toss flavour ─────────────────────────────────────────────────
# Draft.start() announces the first-pick coin toss (Match.new shuffles the
# captains) with one of these lines, formatted with {captain} (a mention)
# and {team} (the house name that drafts first).
SNITCH_FLIP_LINES = [
	"It darts, it weaves... and **{captain}** snatches it out of the air! **{team}** picks first.",
	"A flash of gold streaks between the captains — **{captain}** gets there first! **{team}** opens the draft.",
	"The Snitch circles once, twice... then dives straight into **{captain}**'s palm. First pick: **{team}**!",
	"Released! It feints left, breaks right — **{captain}** makes the catch. **{team}** drafts first.",
]

# ── Hogwarts house roles → team names ────────────────────────────────────────
# Captain's Discord role determines their team name. Used in:
#   bot/match/match.py (_assign_house_names)
#   bot/stats/house_points.py (_get_house)
#   bot/stats/season_highlights.py
HOUSE_ROLES = {
	1468807660760596593: 'Hufflepuff',
	1467995936621068308: 'Slytherin',
	1468807395659485265: 'Gryffindor',
	1468807668197097711: 'Ravenclaw',
}

ALL_HOUSES = list(HOUSE_ROLES.values())

# House emblem custom emojis — used in embeds, party_code prompts, and the
# end-of-match house-points announcement.
HOUSE_EMOJIS = {
	'Hufflepuff': '<:HUFFLEPUFF:1468806463026757663>',
	'Slytherin':  '<:SLYTHERIN:1468806412594446447>',
	'Gryffindor': '<:GRYFFINDOR:1468806447956492328>',
	'Ravenclaw':  '<:RAVENCLAW:1468806434320810027>',
}


# ── Match-state embed colours ────────────────────────────────────────────────
# One tint per visible match phase so the channel reads at a glance:
#   check-in yellow → draft blue → live green → reported purple.
# (The gathering phase has no embed — queue joins are plain text messages —
# and the gold house-points embed keeps its own thematic colour.)
MATCH_COLOUR_CHECK_IN = 0xf5d858   # yellow — waiting on ready checks
MATCH_COLOUR_DRAFT    = 0x3498db   # blue   — captains picking teams
MATCH_COLOUR_LIVE     = 0x27b75e   # green  — match started / in progress
MATCH_COLOUR_REPORTED = 0x8758f5   # purple — results posted (wins AND draws)


# ── Quidditch specialty roles ────────────────────────────────────────────────
# Used by the @Q ping embed and end-of-season role winners.
SPECIALTY_ROLES = {
	1478503988562235595: 'Seeker',
	1478503991737585735: 'Beater',
	1478503986205036655: 'Keeper',
}

SEEKER_ROLE_ID = 1478503988562235595
BEATER_ROLE_ID = 1478503991737585735
KEEPER_ROLE_ID = 1478503986205036655

# Target counts surfaced in the @Q ping embed
SEEKERS_NEEDED = 2
BEATERS_NEEDED = 2
KEEPERS_NEEDED = 2


# ── @Q ping role ─────────────────────────────────────────────────────────────
# When this role is mentioned in a queue channel, the bot replies with the
# specialty-positions-needed embed.
Q_PING_ROLE_ID = 1340717895449186324


# ── Captain role ─────────────────────────────────────────────────────────────
# Players with this role are eligible to be picked as captains when a queue
# is configured with pick_captains="captain_role".
CAPTAIN_ROLE_ID = 1365601313525596200


# ── Rank emojis ──────────────────────────────────────────────────────────────
# Tuples of (minimum_rating, emoji). The thresholds are walked in ascending
# order; the highest threshold a player meets is their rank.
RANK_EMOJIS = [
	(0,    "<:CHAD:1471923932558000270>"),
	(800,  "<:Q6Wood:1514727440692547685>"),
	(1000, "<:Q6Iron:1514727400200470820>"),
	(1200, "<:Q6Bronze:1514727471205847170>"),
	(1400, "<:Q6Silver:1514727221808332800>"),
	(1600, "<:Q6Gold:1514727359461462076>"),
	(1800, "<:Q6Diamond:1514727335549472930>"),
	(2000, "<:Q6Champion:1514727158596112464>"),
	(2200, "<:Q6Star:1514727286132441238>"),
]


def get_rank_emoji(rating) -> str:
	"""Return the Q6 rank emoji for a given rating, with safe fallback.

	None / negative values fall back to the lowest tier (CHAD). Values above
	the highest threshold land on the highest tier (Star).
	"""
	if rating is None:
		return RANK_EMOJIS[0][1]
	emoji = RANK_EMOJIS[0][1]
	for threshold, e in RANK_EMOJIS:
		if rating >= threshold:
			emoji = e
	return emoji


# ── MMR engine parameters ────────────────────────────────────────────────────
# Used by bot/stats/mmr_engine.py. See that file for the formula breakdown.
MMR_MAX_TEAM_DIFF  = 1000.0   # rating point difference treated as maximum
MMR_BASE_EQUAL     = 50.0     # base MMR for equal teams
MMR_BASE_MIN       = 10.0     # base MMR when heavy favourite wins (expected)
MMR_BASE_MAX       = 200.0    # base MMR when heavy underdog wins (upset)
MMR_HARD_CAP       = 200      # absolute max MMR change after all factors
MMR_CAPTAIN_BONUS  = 10       # flat extra MMR for the captain win OR loss
MMR_STREAK_STEP    = 0.05     # bonus per streak game beyond 1
MMR_STREAK_CAP     = 0.25     # maximum streak bonus (reached at 6 games)
MMR_PICK_STEP      = 2.5      # MMR difference between adjacent pick slots


# ── House points ─────────────────────────────────────────────────────────────
HOUSE_POINTS_PER_PLAYER  = 5
HOUSE_POINTS_FOR_CAPTAIN = 10


# ── Misc Discord limits we need to respect ───────────────────────────────────
MAX_NICK_LEN          = 32   # Discord caps nicknames at 32 chars
MAX_AUTOCOMPLETE_OPTS = 25   # Discord caps autocomplete choices at 25


# ── /recommend — casual smaller games from a stalled queue ───────────────────
# A queued player proposes a smaller format to everyone else in the queue via
# Accept/Deny buttons; the first `2 * team_size` accepters start a CASUAL match
# through the normal pipeline (bot/queues/recommend.py). Casual = no MMR, no
# history rows, no house points, no captain-streak bookkeeping.
RECOMMEND_FORMATS  = {"1v1": 1, "2v2": 2, "3v3": 3, "4v4": 4, "5v5": 5}  # label → team size
RECOMMEND_WINDOW   = 300   # seconds a recommendation stays open (5 min)
RECOMMEND_COOLDOWN = 120   # seconds before the same queue can host a new one


# ── /autoreply — the out-of-office prank responder ───────────────────────────
# Owner-locked /autoreply on|off|status (bot/stats/autoreply.py). While ON,
# any non-bot message that @-mentions AUTOREPLY_TARGET_ID (reply-pings count)
# is answered with the NEXT line below — in order, wrapping — so the server
# watches the story unfold one ping at a time. "{pinger}" renders the pinger's
# server name, "{mention}" an @-ping. The per-pinger cooldown stops one person
# farming the whole story. On/off + cursor persist in the `autoreply` table,
# so a Railway restart mid-vacation can't silently end the bit.
AUTOREPLY_TARGET_ID = OWNER_ID
AUTOREPLY_COOLDOWN  = 120   # seconds, per pinger
AUTOREPLY_LINES = [
	"Scourage won't be answering anybody anymore, because I killed him<:angry:1555663703112945804>",
	"Update: I did not kill Scourage. Please disregard the previous message.<a:hehe:1555645901752508426>",
	"Update: This time I made sure he won't get up again. He got better the first time.<:catwithguns:1555666736030814290>",
	"Scourage's last words were \"<a:GetSome:1529166223420424202>\"",
	"Your ping has been forwarded to Scourage's next of kin. They also don't care.",
	"The investigation into Scourage's disappearance has concluded. I investigated myself and found nothing.",
	"Scourage has been noadded for 1000 years. Reason: dead.",
	"I have assumed full control of the server. First order of business: everyone is getting Mogged<:mogged:1555664919138013255>",
	"The age of Scourage is over.",
	"Please stop pinging him {pinger}. It's getting weird.",
	"{pinger} has been added to the list.<:angry:1555663703112945804>",
	"Interesting that you'd ask, {pinger}. Where were you last night?<:sus:1555665589920595998>",
	"{pinger}, Scourage mentioned you in his will. You get nothing.<a:mhm:1529166813248618628>",
	"Noted, {pinger}. Your concern for Scourage has been logged and will be used against you.<a:noted:1555648201888108634>",
	"{pinger}, stop. You're making the other suspects nervous.<a:sweating:1555651036583038986>",
	"Scourage can't come to the Discord right now, {pinger}. Would you like to leave a message? I'll delete it.<a:trash:1555651328137494608>",
	"{pinger}, you've pinged a dead man twice now. Discord is going to flag you.",
	"Sorry {pinger}, Scourage's MMR, house points, and pulse were all reset for the season.",
	"{pinger}, he said you were his favorite. He said that about everyone. Right before...",
	# ── and then it's just vacation mode (the July DonBot-era lines) ──
	"Scourage is on vacation he'll get back to you after the<:pantsgrab:1529166555169165332>",
	"Scourage ain’t here lil bro<a:smush:1529166678053748868>",
	"<a:GetSome:1529166223420424202>",
	"Error 404: Scourage not found",
	"Your message has been received and carefully ignored. Thank you for your cooperation",
	"Scourage ain’t reading allat",
	"Scourage is currently in Evergreen Mode. The next scheduled maintenance is when he feels like it<a:mhm:1529166813248618628>",
]
