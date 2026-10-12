"""Fail the build if a credential or a real phone number lands in the working tree.

This is a backstop for the pre-commit gitleaks hook, with no dependencies. It
scans the checked-out files, not git history (history needs a rewrite; see
docs/SECURITY.md).
"""
import re
import unittest
from pathlib import Path

import _support as S

SKIP_DIRS = {".git", "exports", "__pycache__", "node_modules", ".venv", "venv"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ino", ".cfg", ".ini",
                 ".example", ".in", ".csv"}

# Patterns are assembled from parts so this file does not match itself.
PATTERNS = {
    "Twilio account SID": re.compile(r"\bA" + r"C[0-9a-fA-F]{32}\b"),
    "Twilio messaging service SID": re.compile(r"\bM" + r"G[0-9a-fA-F]{32}\b"),
    "Twilio API key SID": re.compile(r"\bS" + r"K[0-9a-fA-F]{32}\b"),
    "Groq key": re.compile(r"\bg" + r"sk_[A-Za-z0-9]{20,}\b"),
    "OpenAI-style key": re.compile(r"\bs" + r"k-[A-Za-z0-9_-]{20,}\b"),
    "ElevenLabs key": re.compile(r"\bs" + r"k_[a-f0-9]{30,}\b"),
    "hardcoded secret assignment": re.compile(
        r"(?i)\b(?:auth_?token|api_?key|secret|password)\b\s*[:=]\s*[\"'][A-Za-z0-9/+=_\-]{24,}[\"']"
    ),
    # Real-looking E.164 numbers. 555 test numbers (+1555...) are allowed.
    "phone number": re.compile(r"(?<![\dA-Za-z])\+(?!1555)[1-9]\d{9,13}\b"),
}
# Long hex strings that are not secrets: the model hashes pinned in the lock/docs.
ALLOW_FILES = {"models.lock.json", "MODELS.md", "requirements.txt"}


def tracked_text_files():
    for p in S.ROOT.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(S.ROOT).parts):
            continue
        if p.suffix.lower() in TEXT_SUFFIXES or p.name in {".env.example", ".gitignore"}:
            yield p


class NoSecretsTests(unittest.TestCase):
    def test_no_credentials_or_personal_numbers_in_the_tree(self):
        hits = []
        for p in tracked_text_files():
            if p.name in ALLOW_FILES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for label, rx in PATTERNS.items():
                for m in rx.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{p.relative_to(S.ROOT)}:{line}: {label}")
        self.assertEqual(hits, [], "possible secrets/PII committed:\n" + "\n".join(hits))

    def test_env_file_is_gitignored_and_example_has_only_placeholders(self):
        gi = (S.ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertRegex(gi, r"(?m)^\.env\s*$")
        example = (S.ROOT / ".env.example").read_text(encoding="utf-8")
        for line in example.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, val = line.partition("=")
                if any(w in key for w in ("KEY", "TOKEN", "SID", "PHONE")):
                    self.assertTrue(
                        re.search(r"your_|x{4,}|^$", val.strip()), f"{key} looks like a real value"
                    )


if __name__ == "__main__":
    unittest.main()
