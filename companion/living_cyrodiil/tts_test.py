"""Voice test tool (Phase 3 step 1): say a sentence with Pocket TTS in a preset
voice and in a voice cloned from the player's own game lines, save WAVs to
companion\\tts_test\\ and print timings. No game needed.

Run with companion\\tts_test.ps1. Options:
  --text "..."          the sentence to say
  --voice race/sex      game voice to clone, e.g. argonian/f (default), imperial/m
  --preset NAME         Pocket TTS preset voice to compare with (default alba)
"""

from __future__ import annotations

import argparse
import random
import sys
import time

import numpy as np

from . import audio
from .bsa import BsaArchive
from .game_files import find_data_folder, voice_archives
from .logs import setup_logging
from .paths import USER_DIR
from .settings import SettingsError, load_settings
from .voices import VoiceLibrary, VoiceType

OUT = USER_DIR / "tts_test"
TEXT = ("Welcome to the Five Claws Lodge, traveller. The rain hasn't stopped all week, "
        "but the stew is hot and the beds are dry.")


def reference_from_game(data, voice: str, sample_rate: int, seconds: float = 10.0) -> np.ndarray:
    """A few of the game's own lines for one voice type, joined into one clip."""
    folder = f"sound\\voice\\oblivion.esm\\{voice.replace('/', chr(92))}"
    lines: list[str] = []
    archives = [BsaArchive(p) for p in voice_archives(data)]
    for a in archives:
        lines += [(a, f) for f in a.files(folder) if f.endswith(".mp3")]
    if not lines:
        raise SystemExit(f"No voice lines found for {voice} in {data}")
    random.Random(433).shuffle(lines)          # same picks every run
    clips, total = [], 0.0
    for archive, path in lines:
        clip = audio.trim_silence(audio.decode(archive.read(path), sample_rate), sample_rate)
        d = audio.duration(clip, sample_rate)
        if not 2.0 <= d <= 6.0 or np.abs(clip).max() > 0.98:   # skip very short, long or clipped lines
            continue
        clips += [clip, np.zeros(int(0.3 * sample_rate), np.float32)]
        total += d
        print(f"  reference line: {path.rsplit(chr(92), 1)[-1]} ({d:.1f}s)")
        if total >= seconds:
            break
    return np.concatenate(clips)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Living Cyrodiil voice test")
    ap.add_argument("--text", default=TEXT)
    ap.add_argument("--voice", default="argonian/f")
    ap.add_argument("--preset", default="alba")
    ap.add_argument("--config", help="a Pocket TTS config file (e.g. data/tts/pocket_english_2026-04.yaml)")
    args = ap.parse_args(argv)
    try:
        settings = load_settings(require_key=False)
    except SettingsError as e:
        print(f"\n{e}\n")
        return 1
    setup_logging(settings.log_level)
    OUT.mkdir(exist_ok=True)

    print("Loading Pocket TTS (the first run downloads the model)...")
    t = time.perf_counter()
    from pocket_tts import TTSModel
    model = TTSModel.load_model(config=args.config) if args.config else TTSModel.load_model()
    sr = model.sample_rate
    print(f"  ready in {time.perf_counter() - t:.1f}s, {sr} Hz")

    voices: list[tuple[str, object]] = []
    try:
        t = time.perf_counter()
        voices.append((f"preset_{args.preset}", model.get_state_for_audio_prompt(args.preset)))
        print(f"Preset voice '{args.preset}' in {time.perf_counter() - t:.1f}s")
    except Exception as e:
        print(f"Preset voice '{args.preset}' unavailable: {e}")

    data = find_data_folder(settings.game_data_folder)
    if data:
        print(f"Cloning {args.voice} from your game ({data}):")
        library = VoiceLibrary(data, sr, cache_dir=OUT)
        if "/" in args.voice:
            race, sex = args.voice.split("/")
            ref, used = library.build_reference(VoiceType("oblivion.esm", race, sex))
        else:                                        # an NPC base id: their own lines
            from . import esm
            known = esm.npc_voices(data / "Oblivion.esm").get(args.voice.upper())
            if not known:
                raise SystemExit(f"NPC {args.voice} isn't in Oblivion.esm")
            race, voice_race, female = known
            heard, sex = voice_race or race, "f" if female else "m"
            print(f"  {race} {'female' if female else 'male'}; their lines from the {heard} {sex} folder")
            built = library.build_npc_reference(args.voice, heard, sex)
            if built is None:
                raise SystemExit(f"NPC {args.voice} has too few clean lines of their own")
            ref, used = built
        for name in used:
            print(f"  reference line: {name}")
        ref_path = OUT / f"reference_{args.voice.replace('/', '_')}.wav"
        audio.write_wav(ref_path, ref, sr)
        t = time.perf_counter()
        try:
            voices.append((f"clone_{args.voice.replace('/', '_')}", model.get_state_for_audio_prompt(str(ref_path))))
            print(f"  voice built in {time.perf_counter() - t:.1f}s (reference saved as {ref_path.name})")
        except ValueError as e:
            # The voice-cloning weights are gated on Hugging Face (accept Kyutai's
            # terms on the model page, then log in with a token).
            print(f"  cloning unavailable: {str(e).splitlines()[0][:160]}")
            print("  (needs Hugging Face sign-in: accept the terms at https://huggingface.co/kyutai/pocket-tts)")

    print(f'\nSaying: "{args.text}"')
    for name, state in voices:
        t = time.perf_counter()
        speech = model.generate_audio(state, args.text).numpy().astype(np.float32).reshape(-1)
        took = time.perf_counter() - t
        length = audio.duration(speech, sr)
        path = OUT / f"{name}.wav"
        audio.write_wav(path, speech, sr)
        t = time.perf_counter()
        mp3 = audio.encode_mp3(speech, sr)
        (OUT / f"{name}.mp3").write_bytes(mp3)
        print(f"  {name}: {length:.1f}s of speech in {took:.2f}s ({length / took:.1f}x real time); "
              f"MP3 {len(mp3) // 1024} KB in {time.perf_counter() - t:.2f}s -> {path.name}")
    print(f"\nListen to the files in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
