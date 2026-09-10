"""
Tests fuer wlan-waechter.sh.

Anders als bei update.sh laesst sich dieses Skript wirklich ausfuehren: Es ruft
nur drei Werkzeuge auf (ip, nmcli, logger), und die lassen sich durch
Attrappen im Suchpfad ersetzen. Geprueft wird deshalb nicht der Wortlaut des
Skripts, sondern sein Verhalten.

WAS HIER AUF DEM SPIEL STEHT:
Der Waechter laeuft alle zwei Minuten und fasst dabei die Netzwerkverbindung
des Geraets an. Zwei Fehler waeren teuer und beide fallen im Betrieb nicht auf:

  * Er greift, obwohl alles in Ordnung ist, und wirft eine gesunde Verbindung
    im Zwei-Minuten-Takt weg.
  * Er greift nicht, obwohl die Verbindung weg ist - dann ist er wirkungslos,
    sieht aber installiert aus.
"""
import os
import re
import stat
import subprocess

import pytest


def projektverzeichnis():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SKRIPT = os.path.join(projektverzeichnis(), "wlan-waechter.sh")


@pytest.fixture
def waechter(tmp_path):
    """
    Fuehrt den Waechter mit nachgebauten Werkzeugen aus.

    'ip' meldet je nach Wunsch eine Adresse oder keine, 'nmcli' meldet Erfolg
    oder Misserfolg, und beide schreiben mit, dass sie aufgerufen wurden. Nur
    so laesst sich die eigentliche Frage pruefen: Fasst der Waechter die
    Verbindung an, und wann?
    """
    protokoll = tmp_path / "protokoll.txt"
    stubs = tmp_path / "stubs"
    stubs.mkdir()

    def schreibe(name, inhalt):
        pfad = stubs / name
        pfad.write_text("#!/bin/bash\n" + inhalt, encoding="utf-8")
        pfad.chmod(pfad.stat().st_mode | stat.S_IEXEC)

    schreibe("ip", 'if [ "$HAT_ADRESSE" = "1" ]; then\n'
                   '  echo "    inet 192.0.2.17/24 scope global dynamic $3"\n'
                   '  exit 0\n'
                   'fi\n'
                   'exit 1\n')
    schreibe("nmcli", 'echo "NMCLI $*" >> "$PROTOKOLL"\nexit "$NMCLI_ERGEBNIS"\n')
    schreibe("logger", 'shift 2\necho "LOG $*" >> "$PROTOKOLL"\n')

    def ausfuehren(hat_adresse, nmcli_ergebnis=0, argumente=()):
        umgebung = {
            **os.environ,
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "PROTOKOLL": str(protokoll),
            "HAT_ADRESSE": "1" if hat_adresse else "0",
            "NMCLI_ERGEBNIS": str(nmcli_ergebnis),
        }
        lauf = subprocess.run(["bash", SKRIPT, *argumente], env=umgebung,
                              capture_output=True, text=True)
        mitschnitt = (protokoll.read_text(encoding="utf-8")
                      if protokoll.exists() else "")
        return lauf.returncode, mitschnitt

    return ausfuehren


# ==============================================================================
# Das Skript selbst
# ==============================================================================
def test_das_skript_existiert_und_ist_ausfuehrbar():
    assert os.path.isfile(SKRIPT)
    assert os.access(SKRIPT, os.X_OK), \
        "Ohne Ausführungsrecht startet der systemd-Dienst nicht"


# ==============================================================================
# Verhalten
# ==============================================================================
def test_bei_bestehender_verbindung_bleibt_er_still(waechter):
    """
    Der wichtigste Test: Er darf eine gesunde Verbindung nicht anfassen. Ein
    Waechter, der alle zwei Minuten neu verbindet, waere schlimmer als keiner.
    """
    code, mitschnitt = waechter(hat_adresse=True)

    assert code == 0
    assert "NMCLI" not in mitschnitt, "Er hat in eine bestehende Verbindung eingegriffen"
    assert mitschnitt == "", "Er schreibt ins Journal, obwohl nichts zu tun war"


def test_ohne_adresse_stoesst_er_einen_neuen_versuch_an(waechter):
    code, mitschnitt = waechter(hat_adresse=False)

    assert code == 0
    assert "NMCLI" in mitschnitt, "Er hat gar nicht versucht, neu zu verbinden"
    assert "device connect" in mitschnitt


def test_der_erfolg_steht_im_journal(waechter):
    """Ohne Eintrag im Journal liesse sich spaeter nicht nachvollziehen, ob er greift."""
    _, mitschnitt = waechter(hat_adresse=False, nmcli_ergebnis=0)

    assert "LOG Keine IPv4-Adresse" in mitschnitt
    assert "LOG Verbindung wiederhergestellt" in mitschnitt


def test_auch_der_fehlschlag_steht_im_journal(waechter):
    _, mitschnitt = waechter(hat_adresse=False, nmcli_ergebnis=4)

    assert "LOG Versuch fehlgeschlagen" in mitschnitt


def test_ein_fehlschlag_ist_kein_dienstfehler(waechter):
    """
    Scheitert der Versuch, muss das Skript trotzdem sauber enden. Sonst stuende
    die Einheit dauerhaft auf "failed" - und ein Geraet, das immer alarmiert,
    alarmiert am Ende gar nicht mehr.
    """
    code, _ = waechter(hat_adresse=False, nmcli_ergebnis=4)

    assert code == 0


def test_die_schnittstelle_laesst_sich_uebergeben(waechter):
    """Nicht ueberall heisst das WLAN-Geraet wlan0."""
    _, mitschnitt = waechter(hat_adresse=False, argumente=("wlp2s0",))

    assert "wlp2s0" in mitschnitt


def test_ohne_argument_gilt_wlan0(waechter):
    _, mitschnitt = waechter(hat_adresse=False)

    assert "wlan0" in mitschnitt


# ==============================================================================
# Zusammenspiel mit der Installationsanleitung
# ==============================================================================
def test_die_anleitung_beschreibt_den_waechter():
    """
    Ein Skript, das im Projekt liegt, aber nirgends erklaert wird, richtet
    keinen Schaden an - es hilft nur niemandem.
    """
    with open(os.path.join(projektverzeichnis(), "Installationsanleitung.md"),
              encoding="utf-8") as datei:
        anleitung = datei.read()

    assert "wlan-waechter.sh" in anleitung
    assert "tuerschild-wlan-waechter.timer" in anleitung


def test_der_waechter_liegt_im_selben_verzeichnis_wie_das_programm():
    """
    Der Waechter wird aus dem Projektverzeichnis gestartet, damit update.sh ihn
    mitpflegt. Zeigt ExecStart woandershin als das Hauptprogramm, laeuft
    entweder ein alter Stand weiter, oder der Dienst scheitert mit 203/EXEC -
    einer Fehlermeldung, die man nur findet, wenn man gezielt danach sucht.
    """
    with open(os.path.join(projektverzeichnis(), "Installationsanleitung.md"),
              encoding="utf-8") as datei:
        anleitung = datei.read()

    waechter = re.search(r"ExecStart=(\S*)/wlan-waechter\.sh", anleitung)
    assert waechter, "In der Anleitung fehlt die ExecStart-Zeile des Waechters"

    programm = re.search(r"WorkingDirectory=(\S+)", anleitung)
    assert programm, "In der Anleitung fehlt das Arbeitsverzeichnis des Hauptdienstes"

    assert waechter.group(1).rstrip("/") == programm.group(1).rstrip("/"), (
        f"Waechter liegt laut Anleitung in {waechter.group(1)}, das Programm "
        f"aber in {programm.group(1)}")
