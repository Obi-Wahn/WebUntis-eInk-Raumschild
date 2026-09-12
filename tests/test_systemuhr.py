"""
Tests fuer die Plausibilitaet der Systemuhr.

WORUM ES GEHT:
Der Raspberry Pi Zero 2 W hat keine Echtzeituhr. Nach einem Stromausfall
beginnt er mit der zuletzt gespeicherten Zeit und stellt sie erst, wenn er im
Netz ist.

Ist gar kein Netz da, faellt das nicht ins Gewicht - dann steht ohnehin
"Kein WLAN/Internet" auf dem Schild. Der gefaehrliche Fall ist der andere:
WLAN da, NTP aber blockiert, wie es in einem Schulnetz vorkommt. WebUntis
antwortet dann bereitwillig, gefragt wird es nur nach dem falschen Tag - und
auf dem Schild steht ein vollkommen plausibler Plan von gestern.

Das ist dieselbe Sorte Ausfall wie eine veraltete Ruecklage, nur schlimmer:
Dort stimmt wenigstens der Tag.
"""
import logging
import os
import time

import pytest

import tuerschild as R
from tuerschild import konfiguration, steuerung, web
from tuerschild.konfiguration import uhr_synchronisiert
from tuerschild.steuerung import melde_uhrzustand


@pytest.fixture
def zeitdienst(tmp_path, monkeypatch):
    """
    Baut das Verzeichnis von systemd-timesyncd nach.

    Liefert eine Funktion, die den Zustand setzt:
      "gestellt"      - Verzeichnis und Marke vorhanden
      "ungestellt"    - Verzeichnis vorhanden, Marke fehlt
      "kein timesyncd" - Verzeichnis fehlt
    """
    verzeichnis = tmp_path / "timesync"
    marke = verzeichnis / "synchronized"
    monkeypatch.setattr(konfiguration, "TIMESYNC_VERZEICHNIS", str(verzeichnis))
    monkeypatch.setattr(konfiguration, "TIMESYNC_MARKE", str(marke))

    def setzen(zustand):
        if zustand == "kein timesyncd":
            return
        verzeichnis.mkdir(exist_ok=True)
        if zustand == "gestellt":
            marke.touch()

    return setzen


# ==============================================================================
# uhr_synchronisiert() - die Auskunft selbst
# ==============================================================================
def test_gestellte_uhr_wird_erkannt(zeitdienst):
    zeitdienst("gestellt")
    assert uhr_synchronisiert() is True


def test_ungestellte_uhr_wird_erkannt(zeitdienst):
    """
    systemd-timesyncd legt sein Verzeichnis beim Start an, die Marke aber erst
    nach dem ersten geglueckten Abgleich. Genau diese Luecke ist der Zustand,
    um den es geht.
    """
    zeitdienst("ungestellt")
    assert uhr_synchronisiert() is False


def test_ohne_timesyncd_lautet_die_antwort_weiss_nicht(zeitdienst):
    """
    Der wichtigste der drei Faelle. Ohne diese Unterscheidung muesste
    "Datei fehlt" als "Uhr ist falsch" gelten - und jede Anlage, die ihre Zeit
    ueber chrony holt, bekaeme dauerhaft eine Warnung, die nicht stimmt. Eine
    Warnung, die immer dasteht, liest nach drei Tagen niemand mehr.
    """
    zeitdienst("kein timesyncd")
    assert uhr_synchronisiert() is None, \
        "Ohne timesyncd wird faelschlich eine falsche Uhr gemeldet"


def test_die_auskunft_kostet_keinen_unterprozess(monkeypatch, zeitdienst):
    """
    Die Schleife fragt das regelmaessig. Ein Aufruf von 'timedatectl' waere auf
    einem Pi Zero jedes Mal ein neuer Prozess - os.path.exists() ist ein
    Systemaufruf.
    """
    import subprocess

    def verboten(*args, **kwargs):
        raise AssertionError("Die Uhrpruefung startet einen Unterprozess")

    monkeypatch.setattr(subprocess, "run", verboten)
    monkeypatch.setattr(subprocess, "Popen", verboten)
    monkeypatch.setattr(subprocess, "check_output", verboten)

    zeitdienst("ungestellt")
    assert uhr_synchronisiert() is False


# ==============================================================================
# melde_uhrzustand() - die Meldung ins Protokoll
# ==============================================================================
@pytest.fixture
def uhr_zuruecksetzen():
    R.app_state.uhr_unsynchron_seit = None
    R.app_state.uhr_gemeldet = False
    yield
    R.app_state.uhr_unsynchron_seit = None
    R.app_state.uhr_gemeldet = False


def test_kurz_nach_dem_start_wird_nicht_geklagt(zeitdienst, caplog, uhr_zuruecksetzen):
    """
    Beim Hochfahren ist die Uhr einen Moment lang ungestellt - das Tuerschild
    startet, bevor das WLAN steht. Ohne die Frist stuende nach jedem Neustart
    eine Fehlermeldung im Journal, die nichts bedeutet.
    """
    zeitdienst("ungestellt")

    with caplog.at_level(logging.ERROR):
        melde_uhrzustand()

    assert caplog.text == "", "Schon beim ersten Durchlauf wurde Alarm geschlagen"
    assert R.app_state.uhr_unsynchron_seit is not None, \
        "Ohne den Zeitstempel beginnt die Frist bei jedem Durchlauf von vorn"


def test_kurz_vor_der_frist_wird_noch_nicht_geklagt(zeitdienst, caplog,
                                                     uhr_zuruecksetzen):
    zeitdienst("ungestellt")
    R.app_state.uhr_unsynchron_seit = time.time() - (R.UHR_ALERT_SECONDS - 5)

    with caplog.at_level(logging.ERROR):
        melde_uhrzustand()

    assert caplog.text == ""


def test_nach_der_frist_steht_die_meldung_im_protokoll(zeitdienst, caplog,
                                                        uhr_zuruecksetzen):
    zeitdienst("ungestellt")
    R.app_state.uhr_unsynchron_seit = time.time() - R.UHR_ALERT_SECONDS

    with caplog.at_level(logging.ERROR):
        melde_uhrzustand()

    assert "UNGESTELLTE UHR" in caplog.text
    assert "falschen Tag" in caplog.text, \
        "Die Meldung nennt die Folge nicht - ohne sie klingt sie nach einer Randnotiz"


def test_die_meldung_steht_genau_einmal_da(zeitdienst, caplog, uhr_zuruecksetzen):
    """
    Die Schleife ruft das bei jedem Abruf auf. Ohne das Merkzeichen schriebe
    ein ungestelltes Geraet den ganzen Tag dieselbe Zeile ins Journal.
    """
    zeitdienst("ungestellt")
    R.app_state.uhr_unsynchron_seit = time.time() - R.UHR_ALERT_SECONDS

    with caplog.at_level(logging.ERROR):
        for _ in range(20):
            melde_uhrzustand()

    assert caplog.text.count("UNGESTELLTE UHR") == 1, \
        f"Die Meldung steht {caplog.text.count('UNGESTELLTE UHR')}-mal im Protokoll"


def test_eine_gestellte_uhr_loest_nichts_aus(zeitdienst, caplog, uhr_zuruecksetzen):
    zeitdienst("gestellt")
    R.app_state.uhr_unsynchron_seit = time.time() - 10 * R.UHR_ALERT_SECONDS

    with caplog.at_level(logging.ERROR):
        melde_uhrzustand()

    assert caplog.text == ""
    assert R.app_state.uhr_unsynchron_seit is None, "Die Frist laeuft weiter"


def test_ohne_timesyncd_wird_nie_geklagt(zeitdienst, caplog, uhr_zuruecksetzen):
    """Nicht feststellbar ist kein Anlass zur Klage."""
    zeitdienst("kein timesyncd")
    R.app_state.uhr_unsynchron_seit = time.time() - 10 * R.UHR_ALERT_SECONDS

    with caplog.at_level(logging.ERROR):
        melde_uhrzustand()

    assert caplog.text == ""


def test_das_wiedergestellte_stellen_wird_vermerkt(zeitdienst, caplog,
                                                    uhr_zuruecksetzen, tmp_path):
    """
    Ohne diese Zeile bliebe im Journal eine Fehlermeldung ohne Aufloesung
    stehen - wer sie spaeter liest, weiss nicht, ob das Problem noch besteht.
    """
    zeitdienst("ungestellt")
    R.app_state.uhr_unsynchron_seit = time.time() - R.UHR_ALERT_SECONDS
    melde_uhrzustand()
    assert R.app_state.uhr_gemeldet is True

    (tmp_path / "timesync" / "synchronized").touch()

    with caplog.at_level(logging.INFO):
        melde_uhrzustand()

    assert "wieder gestellt" in caplog.text
    assert R.app_state.uhr_gemeldet is False, \
        "Beim naechsten Ausfall bliebe die Meldung sonst aus"


def test_ohne_vorherige_meldung_wird_die_erholung_nicht_vermerkt(zeitdienst, caplog,
                                                                  uhr_zuruecksetzen):
    """
    Eine Uhr, die beim Start kurz ungestellt war und sich dann stellte, ist der
    Normalfall - darueber muss nichts im Journal stehen.
    """
    zeitdienst("gestellt")

    with caplog.at_level(logging.INFO):
        melde_uhrzustand()

    assert "wieder gestellt" not in caplog.text


def test_die_schleife_fragt_die_uhr_wirklich_ab(conf, monkeypatch):
    """
    Die Verdrahtung: Die Pruefung koennte fehlerfrei sein und trotzdem nie
    aufgerufen werden.
    """
    gefragt = []
    monkeypatch.setattr(steuerung, "uhr_synchronisiert",
                        lambda: gefragt.append(1) or True)
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": None, "next": None}, ""))
    monkeypatch.setattr(R.app_state.shutdown_event, "wait", lambda dauer=None: None)
    R.app_state.force_update_flag = True

    steuerung.ein_durchlauf(steuerung.Schleifenzustand())

    assert gefragt, "Die Hintergrundschleife prueft die Systemuhr nie"


# ==============================================================================
# Die Anzeige im Web-Interface
# ==============================================================================
def seite(webclient, zustand):
    client, kopf = webclient
    web.uhr_synchronisiert = lambda: zustand
    try:
        return client.get("/", headers=kopf).get_data(as_text=True)
    finally:
        web.uhr_synchronisiert = konfiguration.uhr_synchronisiert


def test_eine_ungestellte_uhr_steht_gross_auf_der_seite(webclient):
    """
    Nicht nur in der Statusliste: Wer die Seite oeffnet, weil das Schild
    seltsam aussieht, soll die Ursache lesen, ohne danach zu suchen.
    """
    inhalt = seite(webclient, False)
    assert "Die Systemuhr wurde seit dem Start nicht gestellt" in inhalt
    assert "falschen Tag" in inhalt


def test_eine_gestellte_uhr_erzeugt_keinen_hinweis(webclient):
    assert "Systemuhr" not in seite(webclient, True)


def test_ohne_timesyncd_erzeugt_die_seite_keinen_hinweis(webclient):
    """
    Sonst stuende auf jeder Anlage mit chrony dauerhaft eine rote Meldung, die
    nicht stimmt.
    """
    assert "Systemuhr" not in seite(webclient, None)


@pytest.mark.parametrize("zustand,erwartet", [
    (True, "Echtzeit"),
    (None, "Echtzeit"),
    (False, "nicht gestellt"),
])
def test_die_statusliste_nennt_den_zustand_der_uhr(webclient, zustand, erwartet):
    inhalt = seite(webclient, zustand)
    zeile = inhalt.split("<dt>Uhr</dt>")[1].split("</dd>")[0]
    assert erwartet in zeile, f"In der Statuszeile steht nicht {erwartet!r}: {zeile!r}"
