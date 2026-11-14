"""Phase 3: voice types, voice-file names and reference building.
The tests that need the player's game files are skipped when the game isn't found."""

from __future__ import annotations

import numpy as np
import pytest

from living_cyrodiil import esm
from living_cyrodiil.game_files import find_data_folder
from living_cyrodiil.voices import VoiceLibrary, VoiceType, _topic


def test_voice_type_folder_and_key():
    v = VoiceType.for_npc("High Elf", female=True)
    assert v.folder == "sound\\voice\\oblivion.esm\\high elf\\f"
    assert v.key == "oblivion.esm_high-elf_f"


def test_file_name_parts():
    name = "nqdleyawiin_greeting_000355c3_4.mp3"
    assert _topic(name) == "greeting"
    assert esm.info_id(name) == "000355c3"
    assert esm.info_id("sound\\voice\\oblivion.esm\\nord\\m\\" + name) == "000355c3"


GAME = find_data_folder()
needs_game = pytest.mark.skipif(GAME is None, reason="Oblivion isn't installed here")


@needs_game
def test_reference_uses_generic_lines_only(tmp_path):
    lib = VoiceLibrary(GAME, 24000, cache_dir=tmp_path)
    clip, used = lib.build_reference(VoiceType("oblivion.esm", "imperial", "m"))
    assert 10.0 <= clip.size / 24000 <= 30.0 and np.abs(clip).max() <= 1.0
    for name in used:
        assert _topic(name) in ("infogeneral", "greeting", "hello", "goodbye")
        assert esm.info_id(name) not in lib.speakers          # no named NPC's own lines


@needs_game
def test_named_npc_uses_own_lines_or_falls_back(tmp_path):
    lib = VoiceLibrary(GAME, 24000, cache_dir=tmp_path)
    baurus = "00023F2A"
    built = lib.build_npc_reference(baurus, "redguard", "m")
    assert built is not None
    assert all(lib.speakers[esm.info_id(n)] == baurus for n in built[1])
    assert lib.npc_reference_path("00034E69", "argonian", "m") is None   # Tun-Zeeus: too few, use his voice type


@needs_game
def test_named_npc_lines_only_from_the_folder_the_game_plays(tmp_path):
    lib = VoiceLibrary(GAME, 24000, cache_dir=tmp_path)
    traven = "00034E16"            # a Breton; his lines are also recorded under Imperial male
    assert esm.npc_voices(GAME / "Oblivion.esm")[traven] == ["Breton", "", False]
    lines = lib.npc_lines(traven, "breton", "m")
    assert lines and all("\\breton\\m\\" in p for _, p in lines)
    choice = lib.choose(base_id=traven, race="Breton", female=False, name="Hannibal Traven")
    assert choice.own_voice and choice.key == "npc_00034E16_breton_m"


@needs_game
def test_choose_voice_order(tmp_path):
    lib = VoiceLibrary(GAME, 24000, cache_dir=tmp_path)
    own = lib.choose(base_id="0002C8D8", race="Argonian", female=True, name="Witseidutsei")
    assert own.own_voice and own.key == "npc_0002C8D8_argonian_f"
    # Tun-Zeeus has too few lines of his own: his race's voice
    assert lib.choose(base_id="00034E69", race="Argonian", female=False).key == "oblivion.esm_argonian_m"
    # borrowed voices, as the game's race records say
    assert lib.choose(race="Dark Elf", voice_race="High Elf").key == "oblivion.esm_high-elf_m"
    assert lib.choose(race="Breton", voice_race="Imperial", female=True).key == "oblivion.esm_imperial_f"
    # an unknown modded race without a voice race: Imperial of the same sex
    odd = lib.choose(race="Cathay", female=True)
    assert odd.fallback and odd.key == "oblivion.esm_imperial_f"


class _FakeModel:
    """Stands in for Pocket TTS: records what it was asked to load."""
    def __init__(self):
        self.loaded = []

    def get_state_for_audio_prompt(self, source):
        import torch
        self.loaded.append(str(source))
        return {"layer": {"cache": torch.ones(3)}}


def test_voice_bank_saves_and_reloads_states(tmp_path):
    from living_cyrodiil.voices import VoiceBank, VoiceChoice
    ref = tmp_path / "nord.wav"
    ref.write_bytes(b"RIFF")
    choice = VoiceChoice("oblivion.esm_nord_m", "Nord male", ref)

    first = _FakeModel()
    VoiceBank(first, "test", cache_dir=tmp_path).state(choice)
    assert first.loaded == [str(ref)]                       # built from the reference
    saved = tmp_path / "states" / "test" / "oblivion.esm_nord_m.safetensors"
    assert saved.exists()

    second = _FakeModel()
    bank = VoiceBank(second, "test", cache_dir=tmp_path)
    bank.state(choice)
    bank.state(choice)                                      # second call: kept in memory
    assert second.loaded == [str(saved)]                    # reloaded from the saved state, once
