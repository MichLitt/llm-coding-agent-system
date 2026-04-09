"""Helpers for task-scoped command budget policies."""

import re


def is_ad_hoc_install_command(command: str) -> bool:
    normalized = " ".join(command.strip().split())
    return bool(
        re.search(
            r"(^|[;&|()]\s*)(pip install|python\s+-m\s+pip\s+install|uv\s+pip\s+install)(\s|$)",
            normalized,
        )
    )
