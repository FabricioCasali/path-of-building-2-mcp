"""End-to-end tests driving the real headless engine in Docker.

Skipped automatically when Docker (or the prebuilt image) isn't available, so
the fast unit suite (test_share_code.py) still runs anywhere.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from pob_mcp.engine import DEFAULT_IMAGE, PobEngine  # noqa: E402


def _docker_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        out = subprocess.run(
            ["docker", "image", "inspect", DEFAULT_IMAGE],
            capture_output=True, timeout=30,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_image_available(),
    reason="Docker image not available; skipping engine integration tests",
)

MINIMAL_XML = '<PathOfBuilding2><Build level="1" className="Ranger"/></PathOfBuilding2>'


@pytest.fixture(scope="module")
def engine():
    with PobEngine() as eng:
        yield eng


def test_ping(engine):
    assert engine.call("ping") == {"pong": True}


def test_import_and_base_stats(engine):
    summary = engine.call("import_build", xml=MINIMAL_XML)
    assert summary["level"] == 1
    stats = engine.call("calc_stats")
    assert stats["defense"]["Life"] == 65
    assert stats["defense"]["Mana"] == 50


def test_flat_custom_mod_applies_after_import(engine):
    engine.call("import_build", xml=MINIMAL_XML)
    modded = engine.call("set_config", customMods="+1000 to maximum Mana")
    assert modded["defense"]["Mana"] == 1100


def test_increased_custom_mod_applies(engine):
    engine.call("import_build", xml=MINIMAL_XML)
    modded = engine.call("set_config", customMods="100% increased maximum Mana")
    assert modded["defense"]["Mana"] == 98


def test_clearing_mods_resets_to_base(engine):
    engine.call("import_build", xml=MINIMAL_XML)
    engine.call("set_config", customMods="+1000 to maximum Mana")
    cleared = engine.call("set_config", customMods="")
    assert cleared["defense"]["Mana"] == 50


def test_multi_mod_list(engine):
    engine.call("import_build", xml=MINIMAL_XML)
    modded = engine.call(
        "set_config",
        customMods=["+1000 to maximum Mana", "+500 to maximum Life"],
    )
    assert modded["defense"]["Mana"] == 1100
    assert modded["defense"]["Life"] > 65


def test_get_defenses_shape(engine):
    engine.call("import_build", xml=MINIMAL_XML)
    d = engine.call("get_defenses")
    assert set(d["MaximumHitTaken"]) == {"Physical", "Fire", "Cold", "Lightning", "Chaos"}
    assert d["MaximumHitTaken"]["Physical"] > 0
