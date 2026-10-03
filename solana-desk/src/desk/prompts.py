"""Role system prompts: the shared global ban followed by the role's own text."""

from pathlib import Path

ROLES = ("head", "search", "risk", "sniper", "approver", "exit")
MAX_WORDS = 400


class PromptBook:
    def __init__(self, directory: Path) -> None:
        self.global_ban = (directory / "global_ban.md").read_text().strip()
        self._prompts: dict[str, str] = {}
        for role in ROLES:
            text = f"{self.global_ban}\n\n{(directory / f'{role}.md').read_text().strip()}"
            words = len(text.split())
            if words >= MAX_WORDS:
                raise ValueError(f"the {role} prompt is {words} words; roles must stay under {MAX_WORDS}")
            self._prompts[role] = text

    def system(self, role: str) -> str:
        return self._prompts[role]
