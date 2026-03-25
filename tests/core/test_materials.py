"""Tests for material properties database."""

import pytest
from printopt.core.materials import MaterialProfile, get_profile, list_profiles


def test_builtin_petg():
    profile = get_profile("petg")
    assert profile.name == "petg"
    assert profile.density == 1.27
    assert profile.glass_transition == 78
    assert profile.thermal_conductivity == 0.20
    assert profile.specific_heat == 1.2


def test_builtin_pla():
    profile = get_profile("pla")
    assert profile.name == "pla"
    assert profile.density == 1.24


def test_builtin_abs():
    profile = get_profile("abs")
    assert profile.name == "abs"
    assert profile.glass_transition == 105


def test_list_profiles():
    profiles = list_profiles()
    assert "pla" in profiles
    assert "petg" in profiles
    assert "abs" in profiles
    assert "asa" in profiles
    assert "tpu" in profiles
    assert len(profiles) == 5


def test_case_insensitive():
    profile = get_profile("PETG")
    assert profile.name == "petg"


def test_unknown_profile():
    with pytest.raises(KeyError):
        get_profile("unobtanium")


def test_custom_profile_save_load(tmp_path):
    custom = MaterialProfile(
        name="petg-elegoo", density=1.27, specific_heat=1.2,
        thermal_conductivity=0.20, glass_transition=78, cte=60e-6,
    )
    path = tmp_path / "petg-elegoo.json"
    custom.save(path)
    loaded = MaterialProfile.load(path)
    assert loaded.name == "petg-elegoo"
    assert loaded.density == 1.27
    assert loaded.cte == 60e-6
