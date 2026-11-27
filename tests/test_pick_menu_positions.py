# -*- coding: utf-8 -*-
"""The captain's pick menu shows each player's Quidditch position(s) next to
their rating (Nov 2026): "1657 rating • Keeper".

Positions come from the same Discord roles /profile reads (Chaser, Beater,
Seeker, Keeper, Flex, matched by name in any case). Someone holding several
gets them all, in the order /profile uses, so the first one listed is what
their /profile card says; someone holding none shows as Chaser, as on /profile."""
import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
	spec = importlib.util.spec_from_file_location(name, ROOT / rel)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


rules = _load("tuobot_buttons_rules_positions", "bot/buttons_rules.py")


def _player(member, role, uid, *names):
	return member(uid, roles=[role(name=n) for n in names])


def _nick(p):
	return f"player{p.id}"


class _Bare:
	"""A player known only by id (no Discord roles at all)."""

	def __init__(self, id):
		self.id = id


def test_every_position_a_player_holds_in_profile_order(captain_selection, member, role):
	roles_of = captain_selection.get_quidditch_roles
	keeper_seeker = _player(member, role, 1, "Hufflepuff", "Keeper", "Seeker")
	assert roles_of(keeper_seeker) == ['seeker', 'keeper']
	# /profile shows the first one, so the menu and the card always agree.
	assert captain_selection.get_quidditch_role(keeper_seeker) == 'seeker'
	assert roles_of(_player(member, role, 2, "KEEPER", "flex")) == ['keeper', 'flex']
	every = _player(member, role, 3, "Flex", "Keeper", "Seeker", "Beater", "Chaser")
	assert roles_of(every) == ['chaser', 'beater', 'seeker', 'keeper', 'flex']


def test_no_position_role_reads_as_chaser_like_profile(captain_selection, member, role):
	nobody = _player(member, role, 4, "Gryffindor", "Some Other Role")
	assert captain_selection.get_quidditch_roles(nobody) == ['chaser']
	assert captain_selection.get_quidditch_role(nobody) == 'chaser'
	# Something without Discord roles can't break the draft board either.
	assert captain_selection.get_quidditch_roles(_Bare(5)) == ['chaser']


def test_menu_shows_rating_and_positions(captain_selection, member, role):
	players = [
		_player(member, role, 3, "Gryffindor"),
		_player(member, role, 1, "Keeper"),
		_player(member, role, 2, "Keeper", "Seeker"),
	]
	ratings = {1: 1657, 2: 1607, 3: 1453}
	opts = rules.pick_options(players, _nick, ratings, positions_of=captain_selection.get_quidditch_roles)
	assert [(o["label"], o["description"]) for o in opts] == [
		("player1", "1657 rating • Keeper"),
		("player2", "1607 rating • Seeker/Keeper"),
		("player3", "1453 rating • Chaser"),
	]


def test_unrated_queue_shows_positions_only(captain_selection, member, role):
	beater = _player(member, role, 1, "Beater")
	opts = rules.pick_options([beater], _nick, None, positions_of=captain_selection.get_quidditch_roles)
	assert opts[0]["description"] == "Beater"


def test_menu_without_positions_is_unchanged():
	assert rules.pick_options([_Bare(1)], _nick, {1: 1657})[0]["description"] == "1657 rating"
	assert rules.pick_options([_Bare(1)], _nick)[0]["description"] is None


def test_description_stays_within_discords_limit():
	opts = rules.pick_options([_Bare(1)], _nick, {1: 1500}, positions_of=lambda p: ["x" * 60, "y" * 60])
	assert len(opts[0]["description"]) == 100 and opts[0]["description"].endswith("…")


def test_draft_board_hands_the_menu_each_players_positions():
	tree = ast.parse((ROOT / "bot/match/draft.py").read_text(encoding="utf-8"))
	imported = {(n.module, a.name) for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
	assert ("captain_selection", "get_quidditch_roles") in imported
	menu = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "pick_menu")
	assert "positions_of=get_quidditch_roles" in ast.unparse(menu)
