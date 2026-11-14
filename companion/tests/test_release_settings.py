"""Alpha release (Nov 13): the key file in the mod folder, a copy in settings."""

import tomllib

from living_cyrodiil import settings as st
from living_cyrodiil.paths import SETTINGS_EXAMPLE


def _saved(path):
    return tomllib.loads(path.read_text(encoding="utf-8")).get("llm", {}).get("api_key")


def test_key_file_template_and_reading(tmp_path):
    key_file, cfg = tmp_path / "key.txt", tmp_path / "user" / "settings.toml"
    st.prepare_release_files(cfg, key_file)
    assert cfg.exists() and key_file.read_text(encoding="utf-8") == st.KEY_FILE_TEMPLATE
    assert st.read_key_file(key_file) == ""                      # still the placeholder
    key_file.write_text(st.KEY_FILE_TEMPLATE.replace(st.KEY_FILE_PLACEHOLDER, "sk-or-v1-abc123"), encoding="utf-8")
    assert st.read_key_file(key_file) == "sk-or-v1-abc123"
    st.prepare_release_files(cfg, key_file)
    assert _saved(cfg) == "sk-or-v1-abc123"                      # a copy kept in settings


def test_key_restored_after_the_mod_folder_is_replaced(tmp_path):
    key_file, cfg = tmp_path / "key.txt", tmp_path / "settings.toml"
    cfg.write_text(SETTINGS_EXAMPLE.read_text(encoding="utf-8").replace(st.PLACEHOLDER_KEY, "sk-or-v1-saved"),
                   encoding="utf-8")
    key_file.write_text(st.KEY_FILE_TEMPLATE, encoding="utf-8")   # an update shipped the empty template
    st.prepare_release_files(cfg, key_file)
    assert st.read_key_file(key_file) == "sk-or-v1-saved"
    assert st.read_key_file(None) == "" and st.read_key_file(tmp_path / "missing.txt") == ""
