# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 4 fixes: admin commands and configuration.

- permission errors name the level you're missing (mod vs admin were swapped)
- destructive commands only run with confirm:True; /rating reset is owner-only
- start.py writes config.cfg values as escaped literals and core/config.py turns
  them back into real types (an empty id or a quote in STATUS crash-looped boot)
- LOG_LEVEL accepts the usual spellings; a deleted role no longer breaks config
- the settings that did nothing are gone, and nothing reads a removed setting
- every bot.commands.X a slash command calls exists, every __all__ name exists,
  and imports between the bot's own files resolve (partial paste-overs)
- README.md (what /commands links to) lists every slash command

These modules need nextcord/aiomysql at import time, so the functions under
test are pulled out of the source with ast and run against small fakes."""
import __future__
import ast
import asyncio
import importlib.util
import json
import re
import runpy
import types
from enum import IntEnum
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for d in ("bot", "core") for p in (ROOT / d).rglob("*.py"))


def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def _tree(rel):
	return ast.parse(_src(rel))


def _class(tree, name):
	return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _func(tree, name, cls=None):
	scope = _class(tree, cls) if cls else tree
	return next(
		n for n in ast.walk(scope)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def _code(fn):
	"""fn's source without its docstring (which may describe the old bug)."""
	body = [
		s for s in fn.body
		if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
	]
	return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _extract(rel, names, ns=None, cls=None):
	"""Run just the named top-level functions/assignments of a file (or of one
	class in it) in a fresh namespace, without importing the module."""
	tree = _tree(rel)
	scope = _class(tree, cls).body if cls else tree.body
	picked = [
		n for n in scope
		if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names)
		or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))
	]
	assert {getattr(n, "name", None) or n.targets[0].id for n in picked} == set(names), f"missing in {rel}"
	ns = {} if ns is None else ns
	code = compile(
		ast.Module(body=picked, type_ignores=[]), str(ROOT / rel), "exec",
		flags=__future__.annotations.compiler_flag, dont_inherit=True
	)
	exec(code, ns)
	return ns


class _PermissionError(Exception):
	pass


_FAKE_BOT = types.SimpleNamespace(Exc=types.SimpleNamespace(PermissionError=_PermissionError))


class Perms(IntEnum):
	USER = 0
	MEMBER = 1
	MODERATOR = 2
	ADMIN = 3


async def _noop(*args, **kwargs):
	return None


# ── Permissions ───────────────────────────────────────────────────────────────

def test_permission_errors_name_the_missing_level():
	check_perms = _extract("bot/context/context.py", {"check_perms"}, ns={"bot": _FAKE_BOT}, cls="Context")["check_perms"]

	def member(level):
		return types.SimpleNamespace(Perms=Perms, access_level=level, qc=types.SimpleNamespace(gt=lambda s: s))

	with pytest.raises(_PermissionError, match="moderator"):
		check_perms(member(Perms.MEMBER), Perms.MODERATOR)
	with pytest.raises(_PermissionError, match="admin"):
		check_perms(member(Perms.MEMBER), Perms.ADMIN)
	with pytest.raises(_PermissionError, match="admin"):
		check_perms(member(Perms.MODERATOR), Perms.ADMIN)
	check_perms(member(Perms.MODERATOR), Perms.MODERATOR)
	check_perms(member(Perms.ADMIN), Perms.ADMIN)


def _subcommands():
	"""{(group function, subcommand name): decorated function} in slash/commands.py."""
	out = {}
	for fn in ast.walk(_tree("bot/context/slash/commands.py")):
		if not isinstance(fn, ast.AsyncFunctionDef):
			continue
		for dec in fn.decorator_list:
			if (
				isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "subcommand"
				and isinstance(dec.func.value, ast.Attribute)
			):
				name = next(k.value.value for k in dec.keywords if k.arg == "name")
				out[(dec.func.value.attr, name)] = fn
	return out


_ASKS_FIRST = [
	("admin_channel", "delete"),
	("admin_rating", "reset"),
	("admin_rating", "snap"),
	("admin_stats", "nuclear_option"),
	("admin_stats", "season_end"),
	("admin_stats", "replace_player"),
]


@pytest.mark.parametrize("group,name", _ASKS_FIRST)
def test_destructive_commands_ask_first(group, name):
	fn = _subcommands()[(group, name)]
	assert "confirm" in [a.arg for a in fn.args.args], f"/{group[6:]} {name} has no confirm option"
	calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
	confirm_lines = [c.lineno for c in calls if ast.unparse(c.func) == "_confirm_or_explain"]
	acting_lines = [
		c.lineno for c in calls
		if ast.unparse(c.func) == "run_slash" or (isinstance(c.func, ast.Attribute) and c.func.attr == "delete")
	]
	assert confirm_lines, f"/{group[6:]} {name} never asks for confirmation"
	assert acting_lines and min(confirm_lines) <= min(acting_lines), f"/{group[6:]} {name} acts before asking"


def test_confirm_helper_only_passes_with_confirm_true():
	ns = _extract(
		"bot/context/slash/commands.py", {"_confirm_or_explain"},
		ns={"error_embed": lambda text, title=None: {"text": text, "title": title}}
	)
	sent = []

	class Response:
		async def send_message(self, **kwargs):
			sent.append(kwargs)

	interaction = types.SimpleNamespace(response=Response())
	assert asyncio.run(ns["_confirm_or_explain"](interaction, True, "Wipes it.")) is True
	assert sent == []
	assert asyncio.run(ns["_confirm_or_explain"](interaction, False, "Wipes it.")) is False
	assert sent[0]["ephemeral"] is True
	assert "Wipes it." in sent[0]["embed"]["text"] and "confirm: True" in sent[0]["embed"]["text"]


def test_rating_reset_is_owner_only():
	reset_calls = []

	class Rating:
		async def reset(self):
			reset_calls.append(True)

	def ctx(user_id):
		return types.SimpleNamespace(
			author=types.SimpleNamespace(id=user_id), Perms=Perms, check_perms=lambda level: None,
			qc=types.SimpleNamespace(gt=lambda s: s, rating=Rating()), success=_noop
		)

	rating_reset = _extract("bot/commands/admin.py", {"rating_reset"}, ns={"bot": _FAKE_BOT, "OWNER_ID": 42})["rating_reset"]
	with pytest.raises(_PermissionError):
		asyncio.run(rating_reset(ctx(7)))
	assert reset_calls == []
	asyncio.run(rating_reset(ctx(42)))
	assert reset_calls == [True]


def test_renamed_commands():
	subs = _subcommands()
	assert ("admin_rating", "penalty") in subs and ("admin_rating", "penality") not in subs
	assert ("admin_stats", "replace_player") in subs and ("admin_stats", "stats_replace_player") not in subs


def test_auto_ready_can_be_switched_off_and_rejects_inf():
	code = _code(_func(_tree("bot/context/slash/commands.py"), "_auto_ready"))
	assert "'off'" in code and "bot.auto_ready.pop(" in code
	# parse_duration('inf') returns the int 0, which used to crash .total_seconds()
	assert "isinstance(parsed, _td)" in code


def test_nick_reads_the_rating_channel_and_respects_rating_nicks():
	fn = _func(_tree("bot/commands/misc.py"), "set_nick")
	where = next(k.value for n in ast.walk(fn) if isinstance(n, ast.Call) for k in n.keywords if k.arg == "where")
	pairs = {ast.literal_eval(k): ast.unparse(v) for k, v in zip(where.keys, where.values)}
	assert pairs == {"channel_id": "ctx.qc.rating.channel_id", "user_id": "ctx.author.id"}
	code = _code(fn)
	assert "ctx.qc.cfg.rating_nicks" in code and "_build_rated_nick(" in code


# ── Startup configuration ─────────────────────────────────────────────────────

_HOSTILE_ENV = {
	"DC_BOT_TOKEN": 'tok"en\\x',
	"DC_OWNER_ID": "",                # empty: used to be a SyntaxError
	"DC_SLASH_SERVERS": "123, 456",
	"DB_URI": 'mysql://user:p@ss\\word"q@host:3306/db',
	"LOG_LEVEL": "error",
	"STATUS": 'Playing "Q6" drafts',
	"HELP": 'Line one\nLine "two" \\ end',
}


def _load_start():
	spec = importlib.util.spec_from_file_location("tuobot_start_script", ROOT / "start.py")
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)  # safe: main() only runs under __main__
	return module


def _config_coerce():
	ns = _extract("core/config.py", {"_coerce", "_SCHEMA", "_TRUE"}, ns={"json": json})
	return ns["_coerce"], ns["_SCHEMA"]


def test_start_writes_a_config_that_loads_and_types_correctly(tmp_path, monkeypatch):
	start = _load_start()
	for key in ("DC_BOT_TOKEN", "DC_OWNER_ID", "DC_SLASH_SERVERS", "DB_URI", "LOG_LEVEL", "STATUS", "HELP", "COMMANDS_URL"):
		monkeypatch.delenv(key, raising=False)
	for key, value in _HOSTILE_ENV.items():
		monkeypatch.setenv(key, value)
	monkeypatch.chdir(tmp_path)
	launched = []
	monkeypatch.setattr(start.os, "execvp", lambda *args: launched.append(args))

	start.main()
	assert launched, "start.py should launch the bot after writing config.cfg"

	written = runpy.run_path(str(tmp_path / "config.cfg"))  # used to raise SyntaxError
	for key in ("DC_BOT_TOKEN", "DB_URI", "STATUS", "HELP", "LOG_LEVEL"):
		assert written[key] == _HOSTILE_ENV[key]

	coerce, schema = _config_coerce()
	assert set(schema) <= set(written), "start.py should write every key core/config.py knows"
	typed = {key: coerce(written[key], typ) for key, (typ, _default) in schema.items()}
	assert typed["DC_OWNER_ID"] is None  # empty -> keep the default instead of crashing
	assert typed["DC_SLASH_SERVERS"] == [123, 456]
	assert typed["DC_BOT_TOKEN"] == _HOSTILE_ENV["DC_BOT_TOKEN"]


def test_a_crash_exits_non_zero_so_railway_restarts_the_bot():
	"""railway.toml restarts only ON_FAILURE; exiting 0 after a crash left the bot offline."""
	tree = _tree("PUBobot2.py")
	run = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.Expr) and ast.unparse(n) == "loop.run_forever()")
	after = ast.unparse(ast.Module(body=tree.body[run + 1:], type_ignores=[]))
	assert "if _crashed:" in after and "sys.exit(1)" in after
	callback = _code(_func(tree, "_task_done_callback"))
	assert "global _crashed" in callback
	assert callback.index("if exc is None:") < callback.index("_crashed = True")


def test_coerce_int_keeps_the_default_on_empty_or_garbage(capsys):
	coerce, _schema = _config_coerce()
	assert coerce("", int) is None
	assert coerce("   ", int) is None
	assert coerce(" 42 ", int) == 42
	assert coerce("310593959506477075", int) == 310593959506477075
	assert coerce("abc", int) is None
	assert "not a whole number" in capsys.readouterr().out
	assert coerce(True, int) == 1
	assert coerce("off", bool) is False and coerce("Yes", bool) is True
	assert coerce("[1, 2]", list) == [1, 2] and coerce("", list) == []


def test_log_level_accepts_common_spellings(capsys):
	ns = _extract("core/console.py", {"LogLevelToInt", "_LOG_LEVEL_ALIASES", "_log_level"})
	level, ints = ns["_log_level"], ns["LogLevelToInt"]
	assert level("ERROR") == level("error") == level("warning") == ints["ERRORS"]
	assert level("debug") == ints["DEBUG"]
	assert level("off") == level("NONE") == ints["NOTHING"]
	assert level(None) == level("") == ints["INFO"]
	assert level("loud") == ints["INFO"]
	assert "unknown LOG_LEVEL" in capsys.readouterr().out
	assert "self.loglevel = _log_level(cfg.LOG_LEVEL)" in _src("core/console.py")


def test_a_deleted_role_is_blanked_not_fatal():
	logged = []
	wrap = _extract(
		"core/cfg_factory.py", {"wrap"}, ns={"log": types.SimpleNamespace(error=logged.append)}, cls="RoleVar"
	)["wrap"]
	var = types.SimpleNamespace(name="admin_role")
	gone = types.SimpleNamespace(get_role=lambda role_id: None)
	assert asyncio.run(wrap(var, 1234, gone)) is None  # used to raise ValueError
	assert logged and "1234" in logged[0]
	role = object()
	here = types.SimpleNamespace(get_role=lambda role_id: role)
	assert asyncio.run(wrap(var, 1234, here)) is role
	assert asyncio.run(wrap(var, None, here)) is None


def test_queue_size_is_really_bounded():
	fn = _func(_tree("bot/queue_channel.py"), "new_queue", cls="QueueChannel")
	guard = next(n for n in ast.walk(fn) if isinstance(n, ast.If) and "Queue size" in ast.unparse(n.body[0]))
	check = compile(ast.Expression(guard.test), "size_guard", "eval")
	rejected = {size for size in (0, 1, 2, 8, 100, 101, 1000) if eval(check, {"size": size})}
	assert rejected == {0, 1, 101, 1000}  # was `1 > size > 100`, which rejected nothing


# ── Settings that did nothing ─────────────────────────────────────────────────

_REMOVED_CHANNEL_SETTINGS = {
	"prefix", "description", "promotion_role", "promotion_delay", "emoji_ranks", "rating_min_deviation",
	"rating_scale", "rating_loss_scale", "rating_win_scale", "rating_draw_bonus", "rating_ws_boost", "rating_ls_boost",
}
_REMOVED_QUEUE_SETTINGS = {"description", "promotion_role", "promotion_msg"}
_REMOVED_CONFIG_KEYS = {
	"DC_CLIENT_ID", "DC_CLIENT_SECRET", "DC_INVITE_LINK", "WS_ENABLE", "WS_HOST", "WS_PORT",
	"WS_OAUTH_REDIRECT_URL", "WS_ROOT_URL", "WS_SSL_CERT_FILE", "WS_SSL_KEY_FILE",
}


def _declared_settings(rel):
	return {
		n.args[0].value for n in ast.walk(_tree(rel))
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
		and isinstance(n.func.value, ast.Name) and n.func.value.id == "Variables"
		and n.args and isinstance(n.args[0], ast.Constant)
	}


def test_settings_that_did_nothing_are_gone():
	channel = _declared_settings("bot/queue_channel.py")
	queue = _declared_settings("bot/queues/pickup_queue.py")
	assert not channel & _REMOVED_CHANNEL_SETTINGS
	assert not queue & _REMOVED_QUEUE_SETTINGS
	assert {"admin_role", "moderator_role", "max_auto_ready", "rating_decay", "rating_nicks"} <= channel
	assert {"priority", "pick_captains", "show_streamers", "start_direct_msg", "match_lifetime"} <= queue
	schema = _config_coerce()[1]
	assert not set(schema) & _REMOVED_CONFIG_KEYS


def _is_cfg(node):
	return (isinstance(node, ast.Attribute) and node.attr == "cfg") or (isinstance(node, ast.Name) and node.id == "cfg")


def test_nothing_reads_a_removed_setting():
	"""A setting that's no longer defined raises AttributeError when read."""
	removed = _REMOVED_CHANNEL_SETTINGS | _REMOVED_QUEUE_SETTINGS | _REMOVED_CONFIG_KEYS
	hits = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			name = None
			if isinstance(n, ast.Attribute) and _is_cfg(n.value):
				name = n.attr
			elif isinstance(n, ast.Subscript) and _is_cfg(n.value) and isinstance(n.slice, ast.Constant):
				name = n.slice.value
			if name in removed:
				hits.append(f"{path.relative_to(ROOT)}:{n.lineno} {ast.unparse(n)}")
	assert not hits, hits


def test_show_streamers_setting_reaches_the_match():
	fn = _func(_tree("bot/queues/pickup_queue.py"), "_match_cfg", cls="PickupQueue")
	assert "show_streamers" in {k.arg for n in ast.walk(fn) if isinstance(n, ast.Call) for k in n.keywords}


# ── Wiring between files (partial paste-overs) ────────────────────────────────

def _top_level_names(tree):
	"""Names a module defines at top level, and whether it star-imports."""
	names, star = set(), False

	def visit(body):
		nonlocal star
		for n in body:
			if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
				names.add(n.name)
			elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
				for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
					names.update(x.id for x in ast.walk(t) if isinstance(x, ast.Name))
			elif isinstance(n, ast.Import):
				names.update((a.asname or a.name).split(".")[0] for a in n.names)
			elif isinstance(n, ast.ImportFrom):
				if any(a.name == "*" for a in n.names):
					star = True
				names.update(a.asname or a.name for a in n.names if a.name != "*")
			elif isinstance(n, (ast.If, ast.Try, ast.With)):
				visit(n.body)
				visit(getattr(n, "orelse", []))
				for h in getattr(n, "handlers", []):
					visit(h.body)
				visit(getattr(n, "finalbody", []))

	visit(tree.body)
	return names, star


def _exports():
	"""{name: (module file, defined there?)} for every bot/commands/*.py __all__ entry."""
	out = {}
	for path in sorted((ROOT / "bot" / "commands").glob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		all_node = next((
			n for n in tree.body
			if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)
		), None)
		if all_node is None:
			continue
		defined, _star = _top_level_names(tree)
		for name in ast.literal_eval(all_node.value):
			out[name] = (path.name, name in defined)
	return out


def test_every_exported_command_exists():
	# `from .x import *` with a stale __all__ raises AttributeError: boot crash.
	assert not [f"{mod}: {name}" for name, (mod, ok) in _exports().items() if not ok]


def test_slash_commands_only_call_exported_commands():
	exported = set(_exports())
	missing = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if (
				isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute) and n.value.attr == "commands"
				and isinstance(n.value.value, ast.Name) and n.value.value.id == "bot" and n.attr not in exported
			):
				missing.append(f"{path.relative_to(ROOT)}:{n.lineno} bot.commands.{n.attr}")
	assert not missing, missing


def _resolve(path, node):
	"""The repo file an ImportFrom points at (module.py or package/__init__.py), or None."""
	if node.level:
		base = path.parent
		for _ in range(node.level - 1):
			base = base.parent
		parts = node.module.split(".") if node.module else []
	elif node.module and node.module.split(".")[0] in ("bot", "core"):
		base, parts = ROOT, node.module.split(".")
	else:
		return None
	target = base.joinpath(*parts)
	if target.with_suffix(".py").is_file():
		return target.with_suffix(".py")
	if (target / "__init__.py").is_file():
		return target / "__init__.py"
	return None


def test_imports_between_the_bots_own_files_resolve():
	"""e.g. bot/context/__init__.py importing a class that context.py no longer
	defines: the whole bot fails to boot until the other file is pasted."""
	problems = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if not isinstance(n, ast.ImportFrom) or (target := _resolve(path, n)) is None:
				continue
			defined, star = _top_level_names(ast.parse(target.read_text(encoding="utf-8")))
			for alias in n.names:
				name = alias.name
				submodule = target.parent / f"{name}.py" if target.name == "__init__.py" else None
				if name == "*" or name in defined or star or (submodule and (submodule.is_file() or submodule.with_suffix("").is_dir())):
					continue
				problems.append(f"{path.relative_to(ROOT)}:{n.lineno} imports {name} from {target.relative_to(ROOT)}")
	assert not problems, problems


def test_settings_only_use_variable_types_that_exist():
	variables = _class(_tree("core/cfg_factory.py"), "Variables")
	available = {t.id for n in variables.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
	used = {
		n.attr for path in SOURCES for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
		if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "Variables"
	}
	assert used <= available, used - available


# ── README ────────────────────────────────────────────────────────────────────

def _slash_commands():
	"""Every slash command as typed in Discord: '/add', '/channel enable', ..."""
	groups = {}
	for fn in ast.walk(_tree("bot/context/slash/groups.py")):
		if isinstance(fn, ast.AsyncFunctionDef):
			for dec in fn.decorator_list:
				if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "dc.slash_command":
					groups[fn.name] = next(k.value.value for k in dec.keywords if k.arg == "name")
	commands = [f"/{groups[group]} {name}" for group, name in _subcommands()]
	for fn in ast.walk(_tree("bot/context/slash/commands.py")):
		if isinstance(fn, ast.AsyncFunctionDef):
			for dec in fn.decorator_list:
				if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "dc.slash_command":
					commands.append("/" + next(k.value.value for k in dec.keywords if k.arg == "name"))
	return commands


def test_readme_lists_every_slash_command():
	readme = _src("README.md")
	commands = _slash_commands()
	assert len(commands) > 60
	missing = [c for c in commands if not re.search("`" + re.escape(c) + r"[`\s]", readme)]
	assert not missing, f"README.md (what /commands links to) is missing: {missing}"
	assert "`/admin " not in readme, "there is no /admin group: the groups are /channel, /queue, /match, ..."
