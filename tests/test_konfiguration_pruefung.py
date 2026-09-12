"""
Tests fuer die Pruefung der config.json.

WARUM DIESE PRUEFUNG NOETIG IST:
Beide Werte wurden bisher ungeprueft uebernommen. Das faellt nicht sofort auf,
sondern erst am Geraet - und dann als etwas ganz anderes:

  * Ein leerer Raumname geht als Suchbegriff an WebUntis. Auf dem Schild steht
    dann "Raum None fehlt.", ohne dass irgendwo der Grund vermerkt waere.
  * Ein Stundenplan mit "8:00" statt "08:00" fuehrt zu keiner Fehlermeldung.
    Die Namen der Stunden ("1. Std.") bleiben einfach leer, weil parse_lesson()
    die Uhrzeiten als Zeichenketten vergleicht.

Beide Fehler entstehen beim Bearbeiten der Datei von Hand - so, wie es die
Installationsanleitung beschreibt. Deshalb wird beim LADEN geprueft und
gewarnt, nicht nur beim Speichern ueber das Formular.
"""
import pytest

from tuerschild import konfiguration as K
from tuerschild.konstanten import (ROOM_NAME_MAX_LEN, SCHEDULE_MAX_BREAKS,
                                   SCHEDULE_MAX_LESSONS, SCHEDULE_NAME_MAX_LEN)


# ==============================================================================
# Raumname
# ==============================================================================
def test_gueltiger_raumname_wird_uebernommen():
    name, fehler = K.pruefe_raumname("Chemie 2")
    assert name == "Chemie 2"
    assert fehler is None


def test_raumname_wird_von_leerzeichen_befreit():
    """Beim Einfuegen aus der Zwischenablage haengt oft ein Leerzeichen an."""
    name, fehler = K.pruefe_raumname("  Raum 101  ")
    assert name == "Raum 101"
    assert fehler is None


@pytest.mark.parametrize("eingabe", [None, "", "   ", "\t\n"])
def test_leerer_raumname_wird_abgelehnt(eingabe):
    name, fehler = K.pruefe_raumname(eingabe)
    assert name is None
    assert fehler


def test_zu_langer_raumname_wird_abgelehnt():
    name, fehler = K.pruefe_raumname("R" * (ROOM_NAME_MAX_LEN + 1))
    assert name is None
    assert str(ROOM_NAME_MAX_LEN) in fehler


def test_raumname_an_der_laengengrenze_ist_erlaubt():
    """Die Grenze selbst muss noch durchgehen - sonst ist sie um eins daneben."""
    name, fehler = K.pruefe_raumname("R" * ROOM_NAME_MAX_LEN)
    assert fehler is None
    assert len(name) == ROOM_NAME_MAX_LEN


def test_raumname_mit_zeilenumbruch_wird_abgelehnt():
    """Ein Umbruch mitten im Namen zerlegt die Kopfzeile des Displays."""
    name, fehler = K.pruefe_raumname("Raum\n101")
    assert name is None
    assert fehler


# ==============================================================================
# Stundenplan
# ==============================================================================
def plan(**abweichungen):
    """Ein gueltiger Stundenplan, den einzelne Tests gezielt verbiegen."""
    grundlage = {
        "DAY_START": "07:55",
        "DAY_END": "15:30",
        "LESSONS": [{"start": "08:00", "end": "08:45", "name": "1. Std."}],
        "BREAKS": [{"start": "09:35", "end": "09:50", "name": "1. Pause"}],
    }
    grundlage.update(abweichungen)
    return grundlage


def test_gueltiger_stundenplan_wird_uebernommen():
    ergebnis, fehler = K.pruefe_stundenplan(plan())
    assert fehler is None
    assert ergebnis["DAY_START"] == "07:55"
    assert ergebnis["LESSONS"][0]["name"] == "1. Std."
    assert ergebnis["BREAKS"][0]["start"] == "09:35"


def test_die_beispielkonfiguration_wird_angenommen(conf):
    """
    Der mitgelieferte Stundenplan muss die eigene Pruefung bestehen. Ohne diesen
    Test koennte die Pruefung strenger sein als die eigene Vorlage - der erste
    Speicherversuch nach der Installation wuerde dann fehlschlagen.
    """
    ergebnis, fehler = K.pruefe_stundenplan(conf["SCHEDULE"])
    assert fehler is None, fehler
    assert len(ergebnis["LESSONS"]) == len(conf["SCHEDULE"]["LESSONS"])


def test_einstellige_stunde_wird_beanstandet():
    """
    Der wichtigste Fall dieser Datei. "8:00" und "08:00" sind fuer
    parse_lesson() verschiedene Zeiten, weil dort Zeichenketten verglichen
    werden - der Stundenname bliebe leer, ohne jede Fehlermeldung.

    Die Pruefung biegt das NICHT zurecht, sondern meldet es: Sie schreibt die
    config.json nicht um. Wuerde sie die kurze Schreibweise durchwinken, bliebe
    der Fehler in der Datei stehen und das Schild weiter stumm.
    """
    ergebnis, fehler = K.pruefe_stundenplan(
        plan(LESSONS=[{"start": "8:00", "end": "08:45", "name": "1. Std."}]))
    assert ergebnis is None
    assert "08:00" in fehler, "Die Meldung muss die richtige Schreibweise nennen"


def test_einstellige_stunde_faellt_auch_im_tagesbeginn_auf():
    ergebnis, fehler = K.pruefe_stundenplan(plan(DAY_START="7:55"))
    assert ergebnis is None
    assert "07:55" in fehler


def test_was_kein_objekt_ist_wird_abgelehnt():
    ergebnis, fehler = K.pruefe_stundenplan(["08:00"])
    assert ergebnis is None
    assert fehler


@pytest.mark.parametrize("kaputt", ["800", "8.00", "25:00", "08:60", "", "acht Uhr"])
def test_unbrauchbare_uhrzeiten_werden_abgelehnt(kaputt):
    ergebnis, fehler = K.pruefe_stundenplan(
        plan(LESSONS=[{"start": kaputt, "end": "08:45", "name": "1. Std."}]))
    assert ergebnis is None
    assert "HH:MM" in fehler


def test_tagesende_muss_nach_tagesbeginn_liegen():
    ergebnis, fehler = K.pruefe_stundenplan(plan(DAY_START="15:30", DAY_END="07:55"))
    assert ergebnis is None
    assert "DAY_END" in fehler


def test_stundenende_muss_nach_stundenbeginn_liegen():
    ergebnis, fehler = K.pruefe_stundenplan(
        plan(LESSONS=[{"start": "08:45", "end": "08:00", "name": "1. Std."}]))
    assert ergebnis is None
    assert fehler


def test_fehlermeldung_nennt_den_betroffenen_eintrag():
    """
    Bei zwanzig Stunden ist "irgendeine Uhrzeit ist falsch" wertlos. Die Meldung
    muss sagen, welcher Eintrag gemeint ist.
    """
    ergebnis, fehler = K.pruefe_stundenplan(plan(LESSONS=[
        {"start": "08:00", "end": "08:45", "name": "1. Std."},
        {"start": "08:50", "end": "09:35", "name": "2. Std."},
        {"start": "0955", "end": "10:40", "name": "3. Std."},
    ]))
    assert ergebnis is None
    assert "Eintrag 3" in fehler


def test_zu_viele_stunden_werden_abgelehnt():
    zuviel = [{"start": "08:00", "end": "08:45", "name": "x"}] * (SCHEDULE_MAX_LESSONS + 1)
    ergebnis, fehler = K.pruefe_stundenplan(plan(LESSONS=zuviel))
    assert ergebnis is None
    assert str(SCHEDULE_MAX_LESSONS) in fehler


def test_zu_viele_pausen_werden_abgelehnt():
    zuviel = [{"start": "09:35", "end": "09:50", "name": "x"}] * (SCHEDULE_MAX_BREAKS + 1)
    ergebnis, fehler = K.pruefe_stundenplan(plan(BREAKS=zuviel))
    assert ergebnis is None
    assert str(SCHEDULE_MAX_BREAKS) in fehler


def test_zu_langer_stundenname_wird_abgelehnt():
    ergebnis, fehler = K.pruefe_stundenplan(plan(LESSONS=[
        {"start": "08:00", "end": "08:45", "name": "N" * (SCHEDULE_NAME_MAX_LEN + 1)}]))
    assert ergebnis is None
    assert fehler


def test_liste_statt_objekt_wird_abgelehnt():
    ergebnis, fehler = K.pruefe_stundenplan(plan(LESSONS=["08:00"]))
    assert ergebnis is None
    assert fehler


def test_lessons_muss_eine_liste_sein():
    ergebnis, fehler = K.pruefe_stundenplan(plan(LESSONS={"08:00": "1. Std."}))
    assert ergebnis is None
    assert fehler


def test_fehlende_listen_gelten_als_leer():
    """
    Eine Schule ohne eingetragene Pausen ist zulaessig. Sie soll nicht gezwungen
    sein, ein leeres BREAKS-Feld hinzuschreiben.
    """
    ergebnis, fehler = K.pruefe_stundenplan({"DAY_START": "08:00", "DAY_END": "13:00"})
    assert fehler is None
    assert ergebnis["LESSONS"] == []
    assert ergebnis["BREAKS"] == []


def test_unbekannte_felder_werden_verworfen():
    """
    Die Rueckgabe enthaelt nur bekannte Felder. Sonst landete alles, was jemand
    ins Textfeld schreibt, ungeprueft und dauerhaft in der config.json.
    """
    ergebnis, fehler = K.pruefe_stundenplan(
        {"DAY_START": "08:00", "DAY_END": "13:00", "ADMIN_PASS": "geheim"})
    assert fehler is None
    assert "ADMIN_PASS" not in ergebnis
    assert set(ergebnis) == {"DAY_START", "DAY_END", "LESSONS", "BREAKS"}


def test_ueberzaehlige_felder_in_einer_stunde_werden_verworfen():
    ergebnis, _ = K.pruefe_stundenplan(plan(LESSONS=[
        {"start": "08:00", "end": "08:45", "name": "1. Std.", "raum": "unerwartet"}]))
    assert set(ergebnis["LESSONS"][0]) == {"start", "end", "name"}


# ==============================================================================
# pruefe_intervall: das Abrufintervall aus dem Formular
# ==============================================================================
def test_gueltiges_intervall_kommt_als_zahl_zurueck():
    """
    Aus dem Formular kommt Text. Wuerde er als Text weitergereicht, stuende in
    der config.json "900" statt 900 - und der Vergleich in der Schleife
    (now - last_update >= intervall) verglichen Zahl mit Text.
    """
    wert, fehler = K.pruefe_intervall("900")
    assert fehler is None
    assert wert == 900 and isinstance(wert, int)


def test_intervall_mit_leerzeichen_wird_angenommen():
    wert, fehler = K.pruefe_intervall("  900  ")
    assert fehler is None and wert == 900


@pytest.mark.parametrize("eingabe", ["abc", "15 Minuten", "900.0", "0x10", "1e3"])
def test_unbrauchbare_eingabe_wird_mit_grund_abgelehnt(eingabe):
    """
    Frueher stand an der aufrufenden Stelle ein 'except Exception: pass'. Der
    Wert wurde verworfen, die Seite meldete trotzdem Erfolg.
    """
    wert, fehler = K.pruefe_intervall(eingabe)
    assert wert is None
    assert fehler, f"{eingabe!r} wurde ohne Begruendung abgelehnt"
    assert eingabe in fehler, "Die Meldung nennt die abgelehnte Eingabe nicht"


@pytest.mark.parametrize("eingabe", ["", "   "])
def test_ein_leeres_feld_wird_als_leer_benannt(eingabe):
    """
    Die Meldung ist hier die ganze Zusicherung, nicht die Ablehnung: int("")
    scheitert ohnehin, das Feld waere also auch ohne eigene Pruefung abgelehnt
    worden - nur mit der Begruendung "'' ist keine ganze Zahl", die niemandem
    weiterhilft. Ein Mutationstest hat gezeigt, dass genau diese Zeile
    ersatzlos entfallen koennte, ohne dass ein Test es merkt.
    """
    wert, fehler = K.pruefe_intervall(eingabe)
    assert wert is None
    assert "leer" in fehler, f"Unpassende Begruendung: {fehler!r}"


def test_fehlende_angabe_wird_als_fehlend_benannt():
    """
    Auch hier zaehlt die Begruendung: Ohne eigene Pruefung liefe None in
    str(None) und die Meldung lautete "'None' ist keine ganze Zahl." - eine
    Eingabe, die niemand gemacht hat.
    """
    wert, fehler = K.pruefe_intervall(None)
    assert wert is None
    assert "fehlt" in fehler, f"Unpassende Begruendung: {fehler!r}"


@pytest.mark.parametrize("eingabe", ["5", "60", "299", "-1", "0"])
def test_zu_kurzes_intervall_wird_abgelehnt(eingabe):
    """
    Nicht hochgesetzt, sondern abgelehnt: Ein Wert, der klammheimlich zu einem
    anderen wird, ist von einem uebernommenen Wert nicht zu unterscheiden.
    """
    wert, fehler = K.pruefe_intervall(eingabe)
    assert wert is None
    assert str(K.MIN_UPDATE_SECONDS) in fehler, \
        "Die Meldung nennt den erlaubten Mindestwert nicht"


def test_zu_langes_intervall_wird_abgelehnt():
    wert, fehler = K.pruefe_intervall(str(K.MAX_UPDATE_SECONDS + 1))
    assert wert is None
    assert str(K.MAX_UPDATE_SECONDS) in fehler


@pytest.mark.parametrize("eingabe,erwartet", [
    (str(K.MIN_UPDATE_SECONDS), K.MIN_UPDATE_SECONDS),
    (str(K.MAX_UPDATE_SECONDS), K.MAX_UPDATE_SECONDS),
])
def test_die_grenzwerte_selbst_sind_erlaubt(eingabe, erwartet):
    """
    Ein Fehler um eins waere hier besonders aergerlich: Die Seite bietet den
    Mindestwert selbst als erlaubt an ("Intervall (Sekunden, mind. 300)").
    """
    wert, fehler = K.pruefe_intervall(eingabe)
    assert fehler is None and wert == erwartet


def test_eine_zahl_wird_auch_als_zahl_angenommen():
    """
    Aus dem Formular kommt Text, aus einer von Hand bearbeiteten Datei aber
    eine Zahl. Die Pruefung soll beides annehmen.
    """
    wert, fehler = K.pruefe_intervall(1800)
    assert fehler is None and wert == 1800
