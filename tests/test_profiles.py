"""Tests for filament profile management."""

from pathlib import Path

import pytest

from printopt.core.materials import (
    MaterialProfile,
    save_custom_profile,
    load_custom_profiles,
    get_all_profiles,
    list_profiles,
)


def test_save_custom_profile(tmp_path):
    profile = MaterialProfile(
        name="petg-elegoo", density=1.27, specific_heat=1.2,
        thermal_conductivity=0.20, glass_transition=78, cte=60e-6,
    )
    path = save_custom_profile(profile, tmp_path)
    assert path.exists()
    assert "petg-elegoo" in path.name


def test_load_custom_profiles(tmp_path):
    profile = MaterialProfile(
        name="my-pla", density=1.24, specific_heat=1.8,
        thermal_conductivity=0.13, glass_transition=60,
    )
    save_custom_profile(profile, tmp_path)
    loaded = load_custom_profiles(tmp_path)
    assert "my-pla" in loaded
    assert loaded["my-pla"].density == 1.24


def test_load_empty_dir(tmp_path):
    loaded = load_custom_profiles(tmp_path)
    assert len(loaded) == 0


def test_get_all_profiles_includes_builtins(tmp_path):
    all_profiles = get_all_profiles(tmp_path)
    assert "petg" in all_profiles
    assert "pla" in all_profiles


def test_get_all_profiles_includes_custom(tmp_path):
    profile = MaterialProfile(
        name="custom-abs", density=1.05, specific_heat=1.4,
        thermal_conductivity=0.17, glass_transition=105,
    )
    save_custom_profile(profile, tmp_path)
    all_profiles = get_all_profiles(tmp_path)
    assert "custom-abs" in all_profiles
    assert "petg" in all_profiles  # builtins still there


def test_custom_overrides_builtin(tmp_path):
    # Custom profile with same name as builtin should override
    profile = MaterialProfile(
        name="petg", density=1.30, specific_heat=1.3,
        thermal_conductivity=0.22, glass_transition=80,
    )
    save_custom_profile(profile, tmp_path)
    all_profiles = get_all_profiles(tmp_path)
    assert all_profiles["petg"].density == 1.30
