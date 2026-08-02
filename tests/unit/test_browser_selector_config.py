import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.app.core.browser_config import load_browser_selectors

CONFIG_DIRECTORY = Path(__file__).parents[2] / "config" / "browser-selectors"


def _copy_configs(target: Path) -> None:
    for source in CONFIG_DIRECTORY.glob("*.json"):
        shutil.copyfile(source, target / source.name)


def test_loads_independently_versioned_platform_configs() -> None:
    config = load_browser_selectors(CONFIG_DIRECTORY)

    assert set(config.platforms) == {"BOSS", "MAIMAI", "LIEPIN"}
    assert config.platforms["BOSS"].version == "2026-07-29-v14"
    assert config.platforms["LIEPIN"].version == "2026-08-02-v8"
    assert (
        config.platforms["LIEPIN"].conversation_entry_button
        == "#im-c-entry .im-ui-basic-entry"
    )
    assert config.platforms["LIEPIN"].conversation_dialog_close_button
    assert config.version.startswith("bundle-")


def test_missing_platform_config_fails(tmp_path: Path) -> None:
    _copy_configs(tmp_path)
    (tmp_path / "liepin.json").unlink()

    with pytest.raises(ValueError, match="缺少浏览器平台配置: LIEPIN"):
        load_browser_selectors(tmp_path)


def test_duplicate_platform_config_fails(tmp_path: Path) -> None:
    _copy_configs(tmp_path)
    shutil.copyfile(tmp_path / "boss.json", tmp_path / "zz-boss.json")

    with pytest.raises(ValueError, match="重复的浏览器平台配置: BOSS"):
        load_browser_selectors(tmp_path)


def test_platform_filename_mismatch_fails(tmp_path: Path) -> None:
    _copy_configs(tmp_path)
    maimai = tmp_path / "maimai.json"
    maimai.rename(tmp_path / "wrong.json")

    with pytest.raises(ValueError, match="平台配置文件名不匹配"):
        load_browser_selectors(tmp_path)


def test_invalid_selector_field_fails(tmp_path: Path) -> None:
    _copy_configs(tmp_path)
    path = tmp_path / "liepin.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session"]["unexpected_selector"] = ".unexpected"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_browser_selectors(tmp_path)


def test_empty_selector_fails(tmp_path: Path) -> None:
    _copy_configs(tmp_path)
    path = tmp_path / "liepin.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["job_list"]["job_list_root"] = ""
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="不得使用空字符串"):
        load_browser_selectors(tmp_path)
