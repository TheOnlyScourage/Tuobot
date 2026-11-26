# -*- coding: utf-8 -*-
import re
import asyncio
import traceback
from nextcord import Embed, DiscordException
# get / find are re-exported: the bot imports them from here.
from nextcord.utils import get, find  # noqa: F401
from datetime import timedelta

# Nov 2026: removed eleven helpers nothing called any more (EmojiFormatter,
# random_string, hl_user, hl_role, format_channel/role/emoji/message, escape,
# reply, discord_table) — leftovers of PUBobot's text commands.


def join_and(names):
	""" Generates 'item1, item2, item3 & item4' string from a list """
	return ', '.join(names[:-1]) + f' & {names[-1]}' if len(names) > 1 else names[0]


def error_embed(description, title='Error'):
	if title:
		return Embed(title=title, description=description, color=0xca0000)
	else:
		return Embed(description=description, color=0xca0000)


def ok_embed(description, title='Success'):
	if title:
		return Embed(title=title, description=description, color=0x32cd32)
	else:
		return Embed(description=description, color=0x32cd32)


# No real duration ("12h30m15s", "1d 12h", "100Y", "01:30:00") comes close to
# this; a hard cap means no input can ever make the parser slow.
_MAX_DURATION_LEN = 32


def parse_duration(string):
	"""Parse "3h2m1s" / "1d 12h" / "03:02:01" into a timedelta ('inf' → 0).

	Units: s m h d, W (week), M (30 days), Y (365 days). Raises ValueError on
	anything else, which every caller turns into a friendly error.

	The unit must be a LETTER. The old pattern `^(\\d+\\w ?)+$` let `\\w` match
	digits too, so a long run of digits backtracked exponentially: any player
	could type ~50 digits into /remove_after and freeze the whole bot for about
	20 minutes (Nov 2026 audit). `[A-Za-z]` accepts exactly the same valid
	inputs and runs in linear time."""
	if string == 'inf':
		return 0
	if len(string) > _MAX_DURATION_LEN:
		raise ValueError()

	if re.match(r"^\d+:\d\d:\d\d$", string):
		x = sum(x * int(t) for x, t in zip([3600, 60, 1], string.split(":")))
		return timedelta(seconds=x)

	elif re.match(r"^(\d+[A-Za-z] ?)+$", string):
		duration = 0
		for part in re.findall(r"\d+[A-Za-z]", string):
			val = float(part[:-1])
			if part[-1] == 's':
				duration += val
			elif part[-1] == 'm':
				duration += val * 60
			elif part[-1] == 'h':
				duration += val * 60 * 60
			elif part[-1] == 'd':
				duration += val * 60 * 60 * 24
			elif part[-1] == 'W':
				duration += val * 60 * 60 * 24 * 7
			elif part[-1] == 'M':
				duration += val * 60 * 60 * 24 * 30
			elif part[-1] == 'Y':
				# Was `365 * 24 * 60` — missing a ×60, so "1Y" meant ~6 days.
				duration += val * 365 * 24 * 60 * 60
			else:
				raise ValueError()
		if duration > timedelta.max.total_seconds():
			raise ValueError()  # e.g. "9999999999Y" — too large for a timedelta
		return timedelta(seconds=int(duration))
	else:
		raise ValueError()


def iter_to_dict(it, key):
	""" Converts an iterable of dictionaries to a dict """
	return {i[key]: i for i in it}


def seconds_to_str(seconds):
	return str(timedelta(seconds=seconds))


def escape_cb(string):
	""" Removes bad characters for string inside a dc codeblock """
	return re.sub(r"([`<>\*_\\\[\]\~])|((?=\s)[^ ])", "", string)


def get_nick(user):
	""" Remove rating tag and text formatting characters """
	string = user.nick or user.name
	if x := re.match(r"^\[\d+\] (.+)", string):
		string = x.group(1)
	return escape_cb(string)


def split_big_text(string: str, limit: int = 2000, delimiter: str = None, prefix: str = "", suffix: str = ""):
	""" Yields pieces with limited length and prefix and suffix attached. Split by delimiter if possible. """
	_limit = limit-len(prefix+suffix)
	while len(string) > _limit:
		if delimiter and (pos := string[:_limit].rfind(delimiter)) != -1:
			yield prefix + string[:pos+len(delimiter)] + suffix
			string = string[pos+len(delimiter):]
		else:
			yield prefix + string[:_limit] + suffix
			string = string[_limit:]
	if len(string):
		yield prefix + string + suffix


class SafeTemplateDict(dict):
	""" returns {key} for missing keys, useful for string.format_map() """
	def __missing__(self, key):
		return '{'+key+'}'


# ── Background work ───────────────────────────────────────────────────────────
# asyncio keeps only a WEAK reference to a task, so a bare create_task() /
# ensure_future() whose result nobody keeps can be garbage-collected before it
# finishes, and an exception inside one surfaced only as "Task exception was
# never retrieved" on stderr, never in the bot's log.
_background_tasks = set()


def spawn(coro, name=None):
	"""Run `coro` in the background: keep it alive until it finishes and log it
	if it fails. Use this instead of a bare asyncio.create_task()."""
	task = asyncio.ensure_future(coro)
	if name:
		task.set_name(name)
	_background_tasks.add(task)
	task.add_done_callback(_background_done)
	return task


def _background_done(task):
	_background_tasks.discard(task)
	if task.cancelled() or task.exception() is None:
		return
	from core.console import log  # here, not at import time: this module stays light
	exc = task.exception()
	log.error("Background task {!r} failed: {!r}\n{}".format(
		task.get_name(), exc, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
	))


async def send_dm(member, *args, **kwargs):
	"""DM a member. Closed DMs (or any other Discord refusal) aren't an error."""
	try:
		await member.send(*args, **kwargs)
	except DiscordException:
		pass
