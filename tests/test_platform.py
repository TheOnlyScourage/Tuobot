# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 6 platform upgrade: nextcord 3.2 on Python 3.13.

- the Dockerfile, CI and ruff all name the same Python version
- requirements.txt matches what the code imports (no dead deps, none missing)
- PUBobot2.py runs everything on the Discord client's own loop
- no Python APIs that are deprecated for removal (load_module,
  asyncio.iscoroutinefunction)"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(
	[p for d in ("bot", "core") for p in (ROOT / d).rglob("*.py")] + [ROOT / "PUBobot2.py", ROOT / "start.py"]
)


def _read(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def _requirements():
	"""{distribution name (lower case): version} from requirements.txt."""
	out = {}
	for line in _read("requirements.txt").splitlines():
		line = line.split("#", 1)[0].strip()
		if line:
			name, _, version = line.partition("==")
			out[name.strip().lower()] = version.strip()
	return out


def test_one_python_version_everywhere():
	docker = re.search(r"^FROM python:(\d+\.\d+)", _read("Dockerfile"), re.M).group(1)
	ci = set(re.findall(r'python-version:\s*"(\d+\.\d+)"', _read(".github/workflows/ci.yml")))
	ruff = re.search(r'^target-version\s*=\s*"py(\d)(\d+)"', _read("ruff.toml"), re.M)
	assert ci == {docker}, f"ci.yml runs {ci}, the Dockerfile ships {docker}"
	assert f"{ruff.group(1)}.{ruff.group(2)}" == docker, "ruff.toml's target-version should match the Dockerfile"


def test_nextcord_3_on_a_python_it_supports():
	reqs = _requirements()
	assert reqs["nextcord"].split(".")[0] == "3", "nextcord should be pinned to a 3.x release"
	docker = re.search(r"^FROM python:(\d+)\.(\d+)", _read("Dockerfile"), re.M)
	assert (int(docker.group(1)), int(docker.group(2))) >= (3, 12), "nextcord 3 needs Python 3.12 or newer"


# import name -> distribution name, where they differ
_DIST = {"PIL": "pillow", "sentry_sdk": "sentry-sdk"}
# imported, but installed by something else / only on Windows
_PROVIDED = {"pymysql": "comes with aiomysql", "pyreadline": "Windows-only fallback in core/console.py"}
# required, but never imported by name
_INDIRECT = {"cryptography": "PyMySQL needs it to log in to MySQL 8 (caching_sha2_password)"}


def _third_party_imports():
	found = {}
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			names = []
			if isinstance(n, ast.Import):
				names = [a.name for a in n.names]
			elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
				names = [n.module]
			for name in names:
				top = name.split(".")[0]
				if top not in sys.stdlib_module_names and top not in ("bot", "core"):
					found.setdefault(top, str(path.relative_to(ROOT)))
	return found


def test_every_import_is_in_requirements():
	reqs = _requirements()
	missing = {
		name: where for name, where in _third_party_imports().items()
		if name not in _PROVIDED and _DIST.get(name, name).lower() not in reqs
	}
	assert not missing, f"imported but not in requirements.txt: {missing}"


def test_every_requirement_is_used():
	used = {_DIST.get(name, name).lower() for name in _third_party_imports()}
	unused = set(_requirements()) - used - set(_INDIRECT)
	assert not unused, f"in requirements.txt but nothing imports it: {unused}"


def test_the_bot_runs_on_the_discord_clients_loop():
	"""nextcord 3 builds and sets its own loop when `dc` is created and keeps
	using dc.loop; a second loop would leave Discord events unhandled."""
	tree = ast.parse(_read("PUBobot2.py"))
	assigns = [ast.unparse(n) for n in tree.body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "loop"]
	assert assigns == ["loop = dc.loop"], assigns
	calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
	assert not calls & {"asyncio.get_event_loop", "asyncio.new_event_loop", "asyncio.set_event_loop"}


def test_no_apis_on_their_way_out():
	config = ast.parse(_read("core/config.py"))
	calls = [ast.unparse(n.func) for n in ast.walk(config) if isinstance(n, ast.Call)]
	assert not [c for c in calls if c.endswith(".load_module")], "load_module() is removed in Python 3.15"
	client = ast.parse(_read("core/client.py"))
	sources = {n.module for n in ast.walk(client) if isinstance(n, ast.ImportFrom) and any(a.name == "iscoroutinefunction" for a in n.names)}
	assert sources == {"inspect"}, "asyncio.iscoroutinefunction is deprecated from Python 3.14"


_EMBED_TEXT = {
	"Embed": ("title", "description", "url"),
	"add_field": ("name", "value"), "insert_field_at": ("name", "value"), "set_field_at": ("name", "value"),
	"set_footer": ("text", "icon_url"), "set_author": ("name", "url", "icon_url"),
}
_NUMBERY = {"len", "sum", "int", "round", "float", "max", "min", "abs", "count"}


def _surely_not_text(node):
	"""Values that can't be a string: numbers/None/bools, arithmetic, len()..."""
	if isinstance(node, ast.Constant):
		return not isinstance(node.value, str)
	if isinstance(node, ast.BinOp) and not isinstance(node.op, (ast.Add, ast.Mod)):
		return True
	if isinstance(node, ast.Call):
		name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
		return name in _NUMBERY
	return False


def test_embed_text_is_always_text():
	"""nextcord 2.x ran str() over embed titles, descriptions, field names and
	values and footers; nextcord 3 sends them as given, so a number or None
	there is rejected by Discord (or breaks JSON encoding)."""
	hits = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if not isinstance(n, ast.Call):
				continue
			name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
			for kw in n.keywords:
				if kw.arg in _EMBED_TEXT.get(name, ()) and _surely_not_text(kw.value):
					hits.append(f"{path.relative_to(ROOT)}:{n.lineno} {name}({kw.arg}={ast.unparse(kw.value)})")
	assert not hits, f"wrap these in str(...) or an f-string: {hits}"
