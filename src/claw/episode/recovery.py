"""Crash detection and safe recovery-state marking for episode manifests."""

from __future__ import annotations

from pathlib import Path
from typing import List, Union

from .state import EpisodeManifest, EpisodeState


_INTERRUPTIBLE_STATES = {
    EpisodeState.PREPARING,
    EpisodeState.RUNNING,
    EpisodeState.VERIFYING,
    EpisodeState.RESETTING,
}


class EpisodeRecoveryManager:
    def __init__(self, episodes_root: Union[str, Path]):
        self.episodes_root = Path(episodes_root).resolve()

    def scan_and_mark(self) -> List[EpisodeManifest]:
        """Mark interrupted manifests without mutating their workspaces."""
        recovered = []
        if not self.episodes_root.is_dir():
            return recovered
        for path in sorted(self.episodes_root.glob("*/episode.json")):
            try:
                manifest = EpisodeManifest.load(path)
            except (OSError, ValueError):
                continue
            if manifest.current_state not in _INTERRUPTIBLE_STATES:
                continue
            previous = manifest.current_state.value
            manifest.transition(
                EpisodeState.RECOVERY_REQUIRED,
                recovery_state=f"interrupted_from:{previous}",
            )
            manifest.save(path)
            recovered.append(manifest)
        return recovered
