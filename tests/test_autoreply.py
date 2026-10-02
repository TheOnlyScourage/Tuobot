# -*- coding: utf-8 -*-
"""/autoreply: the story lines, the pure rules, and wiring guards.

bot/stats/autoreply_rules.py is dependency-free apart from bot.constants, so
it loads by path with the constants module exposed as `bot.constants`
(test_recommend-style). The AST/text guards pin every hop a partial paste-over
could sever: db_init step, package export, on_message hook, on_ready load,
the slash definition, and the owner lock on the handler."""
import ast
import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(rel, name):
	spec = importlib.util.spec_from_file_location(name, ROOT / rel)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


_constants = _load("bot/constants.py", "constants_for_autoreply")
_bot_pkg = types.ModuleType("bot")
_bot_pkg.__path__ = []
sys.modules.setdefault("bot", _bot_pkg)
sys.modules["bot.constants"] = _constants
rules = _load("bot/stats/autoreply_rules.py", "autoreply_rules")

LINES = _constants.AUTOREPLY_LINES
EMOJI = re.compile(r"<a?:[A-Za-z0-9_]+:\d+>")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


# ── the lines themselves ──────────────────────────────────────────────────────

def test_story_has_lines_and_no_blanks():
	assert len(LINES) == 26
	assert all(line.strip() for line in LINES)


def test_only_known_placeholders():
	for line in LINES:
		assert set(PLACEHOLDER.findall(line)) <= {"pinger", "mention"}, line


def test_emoji_codes_are_well_formed():
	# a mangled paste (missing '>', wrong id, stray ':name:') renders as raw text in Discord
	for line in LINES:
		stripped = EMOJI.sub("", line)
		assert "<" not in stripped and ">" not in stripped, f"malformed emoji in: {line}"
		assert not re.search(r"(?<!<a)(?<!<):[A-Za-z]\w+:", stripped), f"bare :emoji: left in: {line}"


def test_target_is_the_owner():
	assert _constants.AUTOREPLY_TARGET_ID == _constants.OWNER_ID
	assert _constants.AUTOREPLY_COOLDOWN == 120


# ── pure rules ───────────────────────────────────────────────────────────────

def test_render_substitutes_pinger_and_mention():
	out = rules.render_line("Please stop pinging him {pinger}. It's getting weird.", "tuonela", "<@1>")
	assert out == "Please stop pinging him tuonela. It's getting weird."
	assert rules.render_line("{mention} no.", "x", "<@449913356506365972>") == "<@449913356506365972> no."


def test_render_leaves_lines_without_placeholders_untouched():
	for line in LINES:
		if "{" not in line:
			assert rules.render_line(line, "someone", "<@1>") == line


def test_render_never_crashes_on_unknown_placeholder():
	assert rules.render_line("hello {whoever}", "a", "b") == "hello {whoever}"


def test_every_story_line_renders():
	for line in LINES:
		rendered = rules.render_line(line, "tuonela", "<@1>")
		assert "{pinger}" not in rendered


def test_cursor_wraps():
	n = len(LINES)
	assert rules.line_at(0) == LINES[0]
	assert rules.line_at(n) == LINES[0]
	assert rules.advance(n - 1) == 0
	assert rules.advance(3) == 4


def test_cooldown_math():
	assert rules.on_cooldown(last=100.0, now=150.0) is True
	assert rules.on_cooldown(last=100.0, now=220.0) is False
	assert rules.on_cooldown(last=None, now=10.0) is False  # never-seen pinger


# ── wiring guards ────────────────────────────────────────────────────────────

def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def test_db_init_registers_the_table():
	src = _src("bot/db_init.py")
	assert "from bot.stats.autoreply      import init_autoreply_table" in src
	assert "('autoreply',          init_autoreply_table)" in src


def test_package_exports_and_events_wire_it():
	assert "from .stats.autoreply import autoreply" in _src("bot/__init__.py")
	ev = _src("bot/events.py")
	assert "if bot.autoreply.wants(message):" in ev and "await bot.autoreply.handle(message)" in ev
	assert "await bot.autoreply.load()" in ev


def test_slash_and_handler_with_owner_lock():
	assert _src("bot/context/slash/commands.py").count("name='autoreply'") == 1
	tree = ast.parse(_src("bot/commands/admin.py"))
	fn = next(
		n for n in ast.walk(tree)
		if isinstance(n, ast.AsyncFunctionDef) and n.name == "autoreply"
	)
	names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
	assert "OWNER_ID" in names, "handler must be owner-locked"
