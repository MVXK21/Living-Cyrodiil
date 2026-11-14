"""Speech-to-text comparison tool (Phase 2 step 2). No game needed.

Press Enter, speak, press Enter again. The same clip goes through each engine
and you see what each one heard and how long it took. Clips are saved to
companion/recordings/ so they can be replayed later with -replay.

  stt_test.ps1             record and compare
  stt_test.ps1 -devices    list microphones
  stt_test.ps1 -keepopen   keep the mic open between clips (Bluetooth test)
  stt_test.ps1 -replay     re-run every saved clip through the engines
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import sys

from .logs import setup_logging
from .paths import USER_DIR
from .recorder import Recorder, list_input_devices, load_wav, save_wav
from .settings import SettingsError, load_settings
from .setting_profile import load_profile
from .stt import MoonshineEngine, SpeechToText, WhisperEngine

log = logging.getLogger("stt_test")
REC_DIR = USER_DIR / "recordings"
RESULTS = REC_DIR / "results.csv"


def load_engines(vocabulary: list[str]) -> list[SpeechToText]:
    print("Loading speech engines. The first run downloads the models (about 850 MB in all)...")
    engines: list[SpeechToText] = []
    for make in (lambda: WhisperEngine("base.en", vocabulary),
                 lambda: WhisperEngine("small.en", vocabulary),
                 lambda: MoonshineEngine("base")):
        try:
            e = make()
            engines.append(e)
            print(f"  ready: {e.name}")
        except Exception as ex:  # one broken engine shouldn't stop the others
            print(f"  FAILED to load an engine: {ex}")
            log.exception("Engine failed to load")
    return engines


def compare(engines, audio, clip_name: str, writer) -> None:
    seconds = audio.size / 16_000
    print(f"\n  Clip: {seconds:.1f}s of audio")
    for e in engines:
        t = e.transcribe(audio)
        heard = t.text or "(nothing)"
        print(f"  {e.name:18} {t.seconds:5.2f}s   {heard}")
        writer.writerow([dt.datetime.now().isoformat(timespec="seconds"), clip_name,
                         f"{seconds:.2f}", e.name, f"{t.seconds:.2f}", t.text])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-devices", action="store_true")
    ap.add_argument("-keepopen", action="store_true")
    ap.add_argument("-replay", action="store_true")
    args = ap.parse_args()

    if args.devices:
        print("Microphones (use the number as mic_device in settings.local.toml):")
        for i, name in list_input_devices():
            print(f"  {i}: {name}")
        return 0

    try:
        settings = load_settings(require_key=False)
    except SettingsError as e:
        print(f"\n{e}\n")
        return 1
    setup_logging(settings.log_level)
    profile = load_profile(settings.setting_profile)
    REC_DIR.mkdir(parents=True, exist_ok=True)

    engines = load_engines(list(profile.stt_vocabulary))
    if not engines:
        return 1

    new_file = not RESULTS.exists()
    with RESULTS.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["time", "clip", "audio_seconds", "engine", "stt_seconds", "text"])

        if args.replay:
            clips = sorted(REC_DIR.glob("clip_*.wav"))
            print(f"\nReplaying {len(clips)} saved clips.")
            for clip in clips:
                print(f"\n{clip.name}")
                compare(engines, load_wav(clip), clip.name, writer)
            return 0

        keep_open = args.keepopen or settings.speech.keep_mic_open
        rec = Recorder(settings.speech.mic_device, keep_open=keep_open)
        print(f"\nMicrophone: {rec.device_name}   (mic kept open: {'yes' if keep_open else 'no'})")
        print("Press Enter to start talking, Enter again to stop. Type q then Enter to quit.")
        try:
            n = len(list(REC_DIR.glob("clip_*.wav")))
            while True:
                if input("\n[Enter] to talk > ").strip().lower() == "q":
                    break
                rec.start()
                input("  ...recording. [Enter] to stop ")
                audio = rec.stop()
                n += 1
                clip = REC_DIR / f"clip_{n:03d}.wav"
                save_wav(clip, audio)
                compare(engines, audio, clip.name, writer)
                f.flush()
        finally:
            rec.close()

    print(f"\nResults saved to {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
