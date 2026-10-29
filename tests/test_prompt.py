"""整体自定义提示词测试。"""
from booktran.prompt.builder import OUTPUT_FORMAT, build_system


def test_custom_prompt_replaces_template():
    out = build_system("japanese", "chinese_simplified", custom_prompt="你是轻小说翻译家，把{source_language}译成{target_language}。")
    assert "轻小说翻译家" in out
    assert "专业译者，正在将一本" not in out  # 内置模板被替换
    assert "把日语译成简体中文" in out
    # 协议段强制追加
    assert '"translations"' in out and OUTPUT_FORMAT.splitlines()[1] in out


def test_custom_prompt_with_own_output_format():
    out = build_system(
        "japanese", "chinese_simplified",
        custom_prompt="自定义指令。\n\n## 输出格式\n只输出 JSON。",
    )
    assert out.count("## 输出格式") == 1  # 不重复追加


def test_custom_prompt_keeps_glossary():
    out = build_system(
        "japanese", "chinese_simplified",
        glossary_rows=[{"src": "アレン", "dst": "艾伦", "note": "主角"}],
        custom_prompt="自定义指令。",
    )
    assert "自定义指令" in out
    assert "## 术语表" in out
    assert "アレン|艾伦|主角" in out


def test_custom_prompt_keeps_style_and_notes():
    out = build_system("japanese", "chinese_simplified", style="轻小说文风", notes="保留敬语",
                       custom_prompt="自定义指令。")
    assert "## 翻译风格" in out and "轻小说文风" in out
    assert "## 其他要求" in out and "保留敬语" in out


def test_default_template_unchanged():
    out = build_system("japanese", "chinese_simplified")
    assert "专业译者" in out
    assert '"translations"' in out
