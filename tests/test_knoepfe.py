"""
Tests fuer die Knoepfe des Dashboards.

WARUM ES DIESE DATEI GIBT:
Der Routen-Rundumschlag in tests/test_web.py prueft fuer JEDE Route, dass sie
ohne Anmeldung und ohne CSRF-Token abgewiesen wird. Genau deshalb laeuft der
Rumpf der Route dort nie: Die Anfrage endet vorher mit 401 oder 403.

Geprueft war damit, dass niemand ohne Anmeldung drankommt - nicht, dass der
Knopf tut, was daraufsteht. Vier Routen hatten dadurch keinen einzigen Test,
der ihren Rumpf ausfuehrt:

    /demo  /test_all  /toggle  /toggle_touch

Werden in /toggle zwei Zeilen vertauscht, schaltet der Knopf "Display" den
Touch-Sensor. Die Seite funktioniert weiter, die Anmeldung greift weiter, und
auffallen wuerde es erst im Flur.

Die beiden Systemknoepfe (/sys_reboot, /sys_shutdown) bleiben bewusst
ungetestet: Auf dem Raspberry Pi laesst update.sh diese Suite laufen, und dort
ist die Sudoers-Regel fuer poweroff eingerichtet. Ein Test, der versehentlich
durchlaeuft, faehrt das Geraet herunter.
"""
import pytest

import tuerschild as R
from tuerschild import web


@pytest.fixture
def ohne_thread(monkeypatch):
    """
    Faengt den Hintergrund-Thread ab, statt ihn zu starten.

    Ohne das spielte /test_all wirklich sieben Bilder mit je vier Sekunden
    Wartezeit ab - und zwar in einem Thread, der noch liefe, wenn der Test
    laengst vorbei ist. Die Vorrichtung merkt sich stattdessen, WAS gestartet
    werden sollte; genau darum geht es in den Tests unten.
    """
    gestartet = []

    class Scheinthread:
        def __init__(self, *args, **kwargs):
            gestartet.append(kwargs.get("target"))

        def start(self):
            pass

    monkeypatch.setattr(web.threading, "Thread", Scheinthread)
    return gestartet


def _ruhender_stand(client, kopf):
    """
    Liefert die Konfiguration, nachdem sich alles gesetzt hat, was sich beim
    ersten angemeldeten Zugriff ohnehin aendert.

    Hintergrund: Die erste erfolgreiche Anmeldung wandelt ein
    Klartext-Passwort in der config.json einmalig in einen Hash um und
    schreibt die Datei. Wer den Stand vorher aufnimmt, sieht diesen Unterschied
    spaeter dem Knopf an, den er gerade prueft.
    """
    client.get("/", headers=kopf)
    return dict(R.get_cached_config())


def druecke(client, kopf, knopf):
    """Loest einen Knopf so aus, wie es das Formular auf der Seite tut."""
    return client.post(knopf, headers=kopf,
                       data={"csrf_token": R.app_state.csrf_token})


# ==============================================================================
# /toggle und /toggle_touch - die beiden Schalter
# ==============================================================================
@pytest.mark.parametrize("knopf,feld,anderes", [
    ("/toggle", "DISPLAY_ACTIVE", "TOUCH_ACTIVE"),
    ("/toggle_touch", "TOUCH_ACTIVE", "DISPLAY_ACTIVE"),
])
def test_ein_schalter_kippt_genau_sein_eigenes_feld(webclient, knopf, feld, anderes):
    """
    Der eigentliche Punkt ist die zweite Zusicherung: dass der Knopf NICHT das
    jeweils andere Feld umlegt. Beide Routen sehen bis auf einen Namen gleich
    aus - eine kopierte Zeile, in der der Name stehenblieb, faellt sonst
    niemandem auf.
    """
    vorher = R.get_cached_config()
    vorher_feld = vorher.get(feld, True)
    vorher_anderes = vorher.get(anderes, True)

    druecke(webclient[0], webclient[1], knopf)

    nachher = R.get_cached_config()
    assert nachher[feld] is not vorher_feld, f"{knopf} hat {feld} nicht umgelegt"
    assert nachher.get(anderes, True) is vorher_anderes, \
        f"{knopf} hat nebenbei {anderes} veraendert"


@pytest.mark.parametrize("knopf", ["/toggle", "/toggle_touch"])
def test_ein_schalter_laesst_sich_zurueckstellen(webclient, knopf):
    """Zweimal druecken muss wieder den Ausgangszustand ergeben."""
    client, kopf = webclient
    vorher = dict(R.get_cached_config())

    druecke(client, kopf, knopf)
    druecke(client, kopf, knopf)

    nachher = R.get_cached_config()
    assert nachher["DISPLAY_ACTIVE"] == vorher.get("DISPLAY_ACTIVE", True)
    assert nachher["TOUCH_ACTIVE"] == vorher.get("TOUCH_ACTIVE", True)


@pytest.mark.parametrize("knopf", ["/toggle", "/toggle_touch"])
def test_ein_schalter_wirkt_sofort_auf_dem_schild(webclient, knopf):
    """
    Ohne das Update-Signal stuende die Aenderung zwar in der config.json, das
    Panel zeigte aber bis zum naechsten Abrufintervall - also bis zu einer
    Viertelstunde lang - noch den alten Zustand. Wer den Knopf drueckt und vor
    dem Schild steht, haelt das fuer einen Defekt.
    """
    client, kopf = webclient
    R.app_state.force_update_flag = False

    druecke(client, kopf, knopf)

    assert R.app_state.force_update_flag is True, \
        f"{knopf} loest keine Aktualisierung des Schildes aus"


@pytest.mark.parametrize("knopf", ["/toggle", "/toggle_touch"])
def test_ein_schalter_tastet_den_stundenplan_nicht_an(webclient, knopf):
    """
    Die Schalter schreiben die ganze config.json zurueck. Ginge dabei etwas
    verloren, faende man es erst am naechsten Schultag heraus.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()
    plan, raum = vorher["SCHEDULE"], vorher["ROOM_NAME"]

    druecke(client, kopf, knopf)

    nachher = R.get_cached_config()
    assert nachher["SCHEDULE"] == plan
    assert nachher["ROOM_NAME"] == raum


# ==============================================================================
# /demo - lokale Dummy-Daten
# ==============================================================================
def test_der_demo_knopf_zeigt_die_beispieldaten_einmalig(webclient):
    """
    'einmalig' ist die ganze Zusicherung: Bliebe das Signal stehen, zeigte das
    Schild dauerhaft erfundene Stunden - und zwar voellig unauffaellig, denn
    Beispieldaten sehen aus wie echte.
    """
    client, kopf = webclient
    R.app_state.show_demo_once = False
    R.app_state.force_update_flag = False

    druecke(client, kopf, "/demo")

    assert R.app_state.show_demo_once is True
    assert R.app_state.force_update_flag is True, \
        "Die Demo erschiene erst beim naechsten Abrufintervall"


def test_der_demo_knopf_aendert_die_konfiguration_nicht(webclient):
    """Die Demo ist eine Anzeige, keine Einstellung."""
    client, kopf = webclient
    vorher = _ruhender_stand(client, kopf)

    druecke(client, kopf, "/demo")

    assert R.get_cached_config() == vorher


# ==============================================================================
# /test_all - der Display-Testlauf
# ==============================================================================
def test_der_testlauf_startet_die_bilderfolge(webclient, ohne_thread):
    client, kopf = webclient
    R.app_state.test_mode_active = False

    druecke(client, kopf, "/test_all")

    assert ohne_thread == [R.run_display_test_sequence], \
        "Der Knopf startet nicht den Testlauf"


def test_ein_zweiter_druck_startet_keinen_zweiten_testlauf(webclient, ohne_thread):
    """
    Zwei Testlaeufe gleichzeitig wuerden sich gegenseitig ins Bild schreiben -
    und am Ende setzt der erste test_mode_active zurueck, waehrend der zweite
    noch laeuft. Die Hintergrundschleife faenge dann mitten im Testlauf wieder
    an zu zeichnen.
    """
    client, kopf = webclient
    R.app_state.test_mode_active = True

    druecke(client, kopf, "/test_all")

    assert ohne_thread == [], "Ein zweiter Testlauf wurde gestartet"


def test_der_testlauf_aendert_die_konfiguration_nicht(webclient, ohne_thread):
    client, kopf = webclient
    vorher = _ruhender_stand(client, kopf)

    druecke(client, kopf, "/test_all")

    assert R.get_cached_config() == vorher


# ==============================================================================
# Alle vier leiten zurueck auf die Bedienseite
# ==============================================================================
@pytest.mark.parametrize("knopf", ["/demo", "/test_all", "/toggle", "/toggle_touch"])
def test_jeder_knopf_fuehrt_zurueck_aufs_dashboard(webclient, knopf, ohne_thread):
    """
    Ohne die Weiterleitung bliebe der Browser auf einer leeren Antwortseite
    stehen, und ein Neuladen wuerde den Knopf erneut ausloesen.
    """
    antwort = druecke(webclient[0], webclient[1], knopf)
    assert antwort.status_code == 302
    assert antwort.headers["Location"] == "/"
