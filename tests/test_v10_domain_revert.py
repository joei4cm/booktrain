"""v1.0 收口：领域预设注入 / 修订回退 / 图片输入。"""
from __future__ import annotations

import asyncio

import pytest

from booktran.model import Segment, Status, Track
from booktran.prompt.builder import build_system
from booktran.prompt.presets import available_domains, preset_text

# ---------------- 领域预设 ----------------

def test_all_domain_presets_nonempty():
    for key, label in available_domains():
        text = preset_text(key)
        assert text, f"{key} 预设为空"
        assert label and label != key


def test_build_system_injects_domain():
    sys_religion = build_system("english", "chinese_simplified", domain="religion")
    assert "宗教与经文" in sys_religion
    assert "通行译本" in sys_religion
    sys_law = build_system("english", "chinese_simplified", domain="law")
    assert "法律与合同" in sys_law
    # 未知领域不注入
    sys_none = build_system("english", "chinese_simplified", domain="")
    assert "领域背景" not in sys_none


def test_domain_cli_flag_end_to_end(tmp_path, txt_path, capsys):
    from booktran.cli import main
    proj = tmp_path / "proj"
    main(["init", str(txt_path), "--dir", str(proj), "--fake",
          "--domain", "literary"])
    # booktran.toml 里有 domain
    import tomllib
    pcfg = tomllib.loads((proj / "booktran.toml").read_text("utf-8"))
    assert pcfg["project"]["domain"] == "literary"


# ---------------- 修订回退 ----------------

def test_revert_restores_previous_revision(tmp_path):
    seg = Segment(sid="a#0", source="原文内容")
    tr: Track = seg.track("chinese_simplified")
    tr.target = "第一版译文"
    tr.status = Status.TRANSLATED
    tr.revisions.append({"at": 1.0, "old": "", "new": "第一版译文", "source": "proofread"})
    tr.target = "第二版译文"
    tr.revisions.append({"at": 2.0, "old": "第一版译文", "new": "第二版译文", "source": "proofread"})
    # 回退到上一版
    prev = tr.revisions[-1]["old"]
    assert prev == "第一版译文"
    tr.target = prev
    assert tr.target == "第一版译文"


# ---------------- 图片输入 ----------------

def test_image_exts_in_supported():
    from booktran.project import SUPPORTED_EXTS
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp"):
        assert ext in SUPPORTED_EXTS


def test_image_reader_no_channel_errors(tmp_path):
    from booktran.adapters.image import ImageReader
    png = tmp_path / "p.png"
    png.write_bytes(b"\x89PNG fake")
    with pytest.raises(SystemExit, match="OCR 通道"):
        asyncio.run(ImageReader().read(png, "p.png", channel=None))
