"""开箱插件的清单与入口（CM-01）。"""

from pathlib import Path

from ncatbot.plugin.loader.indexer import PluginIndexer


# CM-01 两个插件能被 loader 正确索引，且声明了 GroupEconomy 依赖
def test_case_plugins_indexed():
    manifests = PluginIndexer().scan(Path("plugins"))
    manifest = manifests.get("CaseOpening")
    assert manifest is not None, "CaseOpening 未被索引"
    assert manifest.entry_class == "CaseOpening"
    assert "GroupEconomy" in manifest.dependencies
    # 旧的两个插件已合并，不应再出现
    assert "CsgoCase" not in manifests and "DotaTreasure" not in manifests
