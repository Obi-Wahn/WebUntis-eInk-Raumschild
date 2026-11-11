"""
Tests, die verhindern, dass Zugangsdaten im Repository landen.

WARUM DAS NOETIG IST:
In der config.json stehen das WebUntis-Passwort der Schule und der
Admin-Zugang. Claude Code legt dauerhaft erlaubte Befehle im Klartext in
.claude/settings.local.json ab - stand im Befehl ein Passwort, steht es dann
dort. Beide Dateien duerfen nie hochgeladen werden.

Eine Zeile in der .gitignore ist schnell versehentlich geloescht oder durch
eine Ausnahme ("!...") ausgehebelt. Diese Tests pruefen deshalb nicht den
Text der .gitignore, sondern fragen Git selbst, ob die Dateien ignoriert
werden. Zusaetzlich wird geprueft, dass keine solche Datei schon eingecheckt
ist und dass in den eingecheckten Dateien nichts steht, das wie ein echter
Schluessel aussieht.
"""
import os
import re
import shutil
import subprocess

import pytest


def projektverzeichnis():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*argumente):
    return subprocess.run(["git", *argumente], cwd=projektverzeichnis(),
                          capture_output=True, text=True)


@pytest.fixture(scope="module", autouse=True)
def nur_mit_git():
    if shutil.which("git") is None or git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("Kein Git-Arbeitsverzeichnis")


GEHEIME_DATEIEN = [
    "config.json",
    "config.json.bak",
    "config.json.alt",
    "tmpa1b2c3d4",                  # Zwischendatei von save_config
    ".claude/settings.local.json",
    "CLAUDE.local.md",
    ".env",
    ".env.local",
    "zugang.pem",
    "zugang.key",
    "id_rsa",
    "id_ed25519.pub",
    "stundenraster_bericht.txt",    # enthaelt Server und Schulkuerzel
]


@pytest.mark.parametrize("pfad", GEHEIME_DATEIEN)
def test_git_ignoriert_dateien_mit_zugangsdaten(pfad):
    # --no-index: auch dann pruefen, wenn die Datei schon eingecheckt waere
    ergebnis = git("check-ignore", "-q", "--no-index", pfad)
    assert ergebnis.returncode == 0, \
        f"{pfad} wird von Git nicht ignoriert - bitte in die .gitignore aufnehmen"


def test_die_vorlage_wird_nicht_ignoriert():
    """Sonst faellt config.example.json still aus dem Repository."""
    for pfad in ("config.example.json", "tests/test_web.py", "tuerschild/web.py"):
        assert git("check-ignore", "-q", "--no-index", pfad).returncode == 1, \
            f"{pfad} wird faelschlich ignoriert"


def test_keine_datei_mit_zugangsdaten_ist_eingecheckt():
    eingecheckt = git("ls-files").stdout.splitlines()
    ignoriert = git("ls-files", "--cached", "--ignored", "--exclude-standard").stdout.split()
    assert not ignoriert, f"Eingecheckt, obwohl ignoriert: {ignoriert}"
    assert ".claude/settings.local.json" not in eingecheckt
    assert "config.json" not in eingecheckt


# Muster echter Schluessel. Platzhalter wie "passwort" oder "geheim" in der
# Vorlage und in den Tests fallen bewusst nicht darunter.
SCHLUESSELMUSTER = {
    "GitHub-Token": r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
    "Anthropic-Schluessel": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "AWS-Schluessel": r"\bAKIA[0-9A-Z]{16}\b",
    "Privater Schluessel": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


def test_in_den_eingecheckten_dateien_steht_kein_schluessel():
    funde = []
    for name in git("ls-files").stdout.splitlines():
        pfad = os.path.join(projektverzeichnis(), name)
        try:
            with open(pfad, encoding="utf-8") as datei:
                text = datei.read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for art, muster in SCHLUESSELMUSTER.items():
            if re.search(muster, text):
                funde.append(f"{name}: {art}")
    assert not funde, f"Moegliche Zugangsdaten gefunden: {funde}"


def test_die_vorlage_enthaelt_nur_platzhalter():
    import json
    with open(os.path.join(projektverzeichnis(), "config.example.json"),
              encoding="utf-8") as datei:
        vorlage = json.load(datei)
    assert vorlage["UNTIS_SERVER"] == "demo.webuntis.com"
    assert vorlage["UNTIS_USER"] == "webuntis_benutzername"
    assert vorlage["UNTIS_PASS"] == "webuntis_passwort"
    assert vorlage["ADMIN_PASS"] == "passwort"
