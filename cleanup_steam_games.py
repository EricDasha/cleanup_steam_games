#!/usr/bin/env python3
"""
清理已卸载 Steam 游戏遗留的目录，并移动到系统回收站。

脚本会自脚本所在（或指定）目录开始，寻找名为 Steam / SteamLibrary /
steamapps 的子目录，并结合 `libraryfolders.vdf` 自动发现所有库路径，再解析
对应的 `appmanifest_XXXX.acf` 文件。Steam 卸载某游戏时会删除 manifest，
因此在 `steamapps/common` 中找不到匹配 manifest 的目录即可判定为孤儿目录，
从而将其移入回收站供用户最终决定是否彻底删除。
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List


MANIFEST_PATTERN = re.compile(r'"(?P<key>[^"]+)"\s+"(?P<value>[^"]*)"')


@dataclass(frozen=True)
class ManifestInfo:
    appid: str
    installdir: str
    name: str


def parse_manifest(path: Path) -> ManifestInfo | None:
    """Extract the minimal info we need from a manifest file."""
    appid = installdir = name = None
    for match in MANIFEST_PATTERN.finditer(path.read_text(encoding="utf-8", errors="ignore")):
        key = match.group("key").lower()
        value = match.group("value").strip()
        if key == "appid":
            appid = value
        elif key == "installdir":
            installdir = value
        elif key == "name":
            name = value
    if appid and installdir:
        return ManifestInfo(appid=appid, installdir=installdir, name=name or "")
    return None


def load_installed_dirs(manifest_dir: Path) -> Dict[str, ManifestInfo]:
    """Return mapping of install directory name -> manifest info."""
    mapping: Dict[str, ManifestInfo] = {}
    for manifest_path in manifest_dir.glob("appmanifest_*.acf"):
        info = parse_manifest(manifest_path)
        if info:
            mapping[info.installdir] = info
    return mapping


def human_bytes(num: int) -> str:
    """Convert a byte count into something readable."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024.0


def folder_size(path: Path) -> int:
    """Recursively sum file sizes for reporting."""
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def find_orphans(common_dir: Path, installed_dirs: Iterable[str]) -> List[Path]:
    installed_set = {name.lower() for name in installed_dirs}
    orphans: List[Path] = []
    for child in common_dir.iterdir():
        if child.is_dir() and child.name.lower() not in installed_set:
            orphans.append(child)
    return sorted(orphans)


def discover_library_paths(search_root: Path) -> List[Path]:
    """Search the given root for Steam/SteamLibrary folders and expand via manifest info."""
    discovered: Dict[Path, None] = {}
    queue: List[Path] = scan_for_local_libraries(search_root)

    while queue:
        steamapps = queue.pop()
        steamapps = steamapps.resolve()
        if not steamapps.is_dir() or steamapps in discovered:
            continue
        discovered[steamapps] = None
        queue.extend(read_libraryfolders(steamapps))
    return sorted(discovered.keys())


def scan_for_local_libraries(search_root: Path) -> List[Path]:
    """Find directories named Steam/SteamLibrary/steamapps under search_root."""
    libraries: Dict[Path, None] = {}
    if not search_root.is_dir():
        return []

    def consider(path: Path) -> None:
        if not path.is_dir():
            return
        name = path.name.lower()
        if name in {"steam", "steamlibrary"}:
            steamapps_dir = path / "steamapps"
            if steamapps_dir.is_dir():
                libraries[steamapps_dir.resolve()] = None
        elif name == "steamapps":
            libraries[path.resolve()] = None

    consider(search_root)
    for entry in search_root.iterdir():
        consider(entry)

    return list(libraries.keys())


def read_libraryfolders(steamapps: Path) -> List[Path]:
    """Parse libraryfolders.vdf to find additional Steam library paths."""
    results: List[Path] = []
    library_vdf = steamapps / "libraryfolders.vdf"
    if not library_vdf.is_file():
        return results
    text = library_vdf.read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        path = Path(match.group(1).strip()).expanduser()
        candidate = path / "steamapps"
        if candidate.is_dir():
            results.append(candidate)
    return results


def summarize_orphans(orphan_groups: Dict[Path, List[Path]]) -> None:
    if not orphan_groups:
        print("未发现孤立游戏目录。")
        return
    print("以下目录疑似为已卸载但未清理的游戏：\n")
    for steamapps, paths in orphan_groups.items():
        print(f"[库]: {steamapps}")
        for path in paths:
            size = human_bytes(folder_size(path))
            print(f"  - {path} ({size})")
        print()


def trash_orphans(orphan_groups: Dict[Path, List[Path]]) -> List[Path]:
    trashed: List[Path] = []
    total = sum(len(paths) for paths in orphan_groups.values())
    if total == 0:
        print("未发现孤立游戏目录。")
        return trashed

    summarize_orphans(orphan_groups)

    trash_func = resolve_send2trash()
    if trash_func is None:
        print("缺少 send2trash 库，无法移动到回收站。请先执行 `pip install send2trash` 再重试。")
        return trashed

    for paths in orphan_groups.values():
        for path in paths:
            try:
                trash_func(str(path))
                trashed.append(path)
                print(f"已将 {path} 移动到回收站。")
            except Exception as exc:  # pylint: disable=broad-except
                print(f"移动到回收站失败：{path} ({exc})")
    return trashed


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove leftover Steam game directories that no longer have manifests."
    )
    parser.add_argument(
        "--search-root",
        type=Path,
        default=resolve_default_root(),
        help="从该目录开始查找 Steam/SteamLibrary 文件夹（默认脚本所在目录）。",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="执行结束后不暂停等待输入。",
    )
    parser.add_argument(
        "--keep",
        nargs="*",
        default=[],
        help="Optional list of directory names to always keep.",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    search_root = args.search_root.resolve()
    libraries = discover_library_paths(search_root)
    if not libraries:
        print(f"未在 {search_root} 下找到任何 Steam/SteamLibrary/steamapps 目录。", file=sys.stderr)
        return 1

    orphan_groups: Dict[Path, List[Path]] = {}
    keep_lower = {name.lower() for name in args.keep}

    for steamapps in libraries:
        common_dir = steamapps / "common"
        if not common_dir.is_dir():
            continue
        manifest_map = load_installed_dirs(steamapps)
        installed_dirs = set(manifest_map.keys()) | keep_lower
        orphans = find_orphans(common_dir, installed_dirs)
        if orphans:
            orphan_groups[steamapps] = orphans

    trashed = trash_orphans(orphan_groups)
    if trashed:
        print("\n以下目录已移入回收站：")
        for path in trashed:
            print(f"- {path}")
    else:
        print("没有需要处理的目录。")
    if not args.no_pause:
        try:
            input("\n操作完成，按回车退出...")
        except EOFError:
            pass
    return 0


@lru_cache(maxsize=1)
def resolve_send2trash():
    try:
        module = importlib.import_module("send2trash")
        return getattr(module, "send2trash")
    except ImportError:  # pragma: no cover - optional dependency
        return None


@lru_cache(maxsize=1)
def resolve_default_root() -> Path:
    """Return the directory where the script/executable resides."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

