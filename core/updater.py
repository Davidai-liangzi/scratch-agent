"""版本检测：通过 GitHub Releases API 检测最新版本，不做自动更新。

个人开发者无服务器，用 GitHub 免费的 Releases API 匿名检测即可。
检测到新版本时由 gui 弹窗提示，用户手动跳转 GitHub 发行版页面下载覆盖。
"""
import re

import requests

# GitHub 仓库（owner/repo），改这里即可切换检测目标
GITHUB_OWNER = "Davidai-liangzi"
GITHUB_REPO = "scratch-agent"

# 当前程序版本号（每次发版手动 +1 即可）
APP_VERSION = "1.1.0"

_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> str:
    """从 tag 里提取版本号（如 v1.0.0 → 1.0.0）。"""
    m = re.search(r"(\d+\.\d+\.\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def _version_tuple(v: str) -> tuple:
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_update():
    """检测 GitHub 最新版本。

    返回 (latest_version, download_url)；无更新、无 release 或网络失败时返回 None。
    """
    try:
        r = requests.get(_API_URL, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        tag = data.get("tag_name") or ""
        latest = _parse_version(tag)
        html_url = data.get("html_url") or (
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases")
        if latest and _version_tuple(latest) > _version_tuple(APP_VERSION):
            return latest, html_url
    except Exception:
        pass
    return None
