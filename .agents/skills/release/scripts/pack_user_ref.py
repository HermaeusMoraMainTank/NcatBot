"""打包用户参考资料 (user-reference.zip)。

用法（项目根目录）：
    uv run python .agents/skills/release/scripts/pack_user_ref.py [--version X.Y.Z]

不指定 --version 时自动从最新 git tag 推导。
打包内容：examples/  .agents/skills/  docs/（排除 __pycache__）
输出：dist/ncatbot5-{version}-user-reference.zip
"""

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

PACK_DIRS = ["examples", ".agents/skills", "docs"]

# 跳过的目录名（任何层级）
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".vuepress"}

# 跳过的文件名
SKIP_FILES = {"package-lock.json"}

# 跳过的扩展名
SKIP_EXTS: set[str] = set()


def _get_version_from_tag() -> str:
    """从最新 git tag 获取版本号（去掉前缀 v）。"""
    result = subprocess.run(  # noqa: S603
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] 无法获取 git tag: {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip().lstrip("v")


def _collect_files() -> list[Path]:
    """收集待打包文件，排除构建产物和大型媒体文件。"""
    files: list[Path] = []
    for d in PACK_DIRS:
        base = ROOT / d
        if not base.exists():
            print(f"  [SKIP] {d}/ 不存在")
            continue
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            if SKIP_DIRS & set(f.parts):
                continue
            if f.name in SKIP_FILES:
                continue
            if f.suffix.lower() in SKIP_EXTS:
                continue
            files.append(f)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="打包用户参考资料")
    parser.add_argument("--version", help="版本号（默认从 git tag 推导）")
    args = parser.parse_args()

    version = args.version or _get_version_from_tag()
    print(f"版本: {version}")

    # 确保 submodule 已初始化
    subprocess.run(  # noqa: S603
        ["git", "submodule", "update", "--init"],
        cwd=ROOT,
        check=True,
    )

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)

    zip_name = f"ncatbot5-{version}-user-reference.zip"
    zip_path = dist / zip_name

    files = _collect_files()
    print(f"收集到 {len(files)} 个文件")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 打包额外的 README.md 到 zip 根目录
        readme = ROOT / "assets" / "user-ref-README.md"
        if readme.exists():
            zf.write(readme, "README.md")

        for f in files:
            arcname = f.relative_to(ROOT).as_posix()
            zf.write(f, arcname)

    print(
        f"已生成: {zip_path.relative_to(ROOT)}  ({zip_path.stat().st_size / 1024:.0f} KB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
