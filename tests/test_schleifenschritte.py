"""
Tests fuer die einzelnen Schritte der Hintergrundschleife.

WARUM ES DIESE DATEI GIBT:
Die Schleife bestand frueher aus einem einzigen Block. Pruefen liess sich
davon nur, was am Ende auf dem Display landete - und zwar ueber einen echten
Thread mit echten Wartezeiten. Zwei Entscheidungen darin blieben deshalb
ungeprueft, ausgerechnet die beiden, an denen ein Fehler Verschleiss am Panel
bedeutet:

  * die Entprellung der Beruehrung (ein Finger loest vielfach aus)
  * die E-Paper-Schonung (eine statische Meldung wird einmal pro Tag gezeichnet)

Seit die Schleife aus benannten Schritten besteht, laesst sich jeder davon
einzeln aufrufen - ohne Thread, ohne Wartezeit, mit nachsehbarem Zustand.
Genau das steht hier.

Die Datei tests/test_hintergrundschleife.py bleibt daneben bestehen: Sie prueft
die Schleife als Ganzes im Thread und damit das Zusammenspiel. Beides zusammen
ist der Punkt - die Schritte einzeln, das Ganze im Betrieb.
"""
import datetime
import logging

import pytest

import tuerschild as R
from tuerschild import steuerung
from tuerschild.steuerung import (Schleifenzustand, aktualisiere_anzeige,
                                  aktualisierungszeitpunkte, ein_durchlauf,
                                  hole_anzeigedaten, ist_schulzeit,
                                  ist_statischer_tag, pruefe_beruehrung,
                                  zeichnen_ueberspringen)
from tuerschild.zustand import Lesson
from conftest import uhrzeit

STUNDE = Lesson("Ma", "Mathematik", "Ab", "9B", "10:00 - 10:45", "3. Std.", None, "")


def _kein_netz(conf):
    """
    Ersatz fuer den Abruf in Tests, die gar nicht abrufen duerften.

    Er schlaegt Alarm statt still ein Ergebnis zu liefern: Ein Test, der
    "hier wird nicht gezeichnet" zusichert, soll nicht daran scheitern, dass
    der Rechner zufaellig kein Netz hat - er soll daran scheitern, dass
    ueberhaupt abgerufen wurde.
    """
    raise AssertionError("Es haette gar kein Abruf stattfinden duerfen")


@pytest.fixture
def zustand():
    """Ein frischer Schleifenzustand mit fester Startzeit statt der Systemuhr."""
    return Schleifenzustand(letzte_beruehrung=1000.0)


@pytest.fixture
def kein_knopfdruck():
    """
    Setzt das Update-Signal ausdruecklich auf 'nicht gedrueckt'.

    WARUM DAS NOETIG IST: force_update_flag steht beim Programmstart auf True -
    das erste Bild soll ja sofort erscheinen, ohne auf das Abrufintervall zu
    warten. Ein Test, der prueft, dass OHNE Anlass nichts gezeichnet wird,
    faende sonst immer einen Anlass vor und ginge aus dem falschen Grund durch.
    """
    R.app_state.force_update_flag = False


@pytest.fixture
def ohne_warten(monkeypatch):
    """
    Ersetzt das Warten der Schleife durch ein Protokoll der Wartezeiten.

    Ohne das dauerte ein einziger Durchlauf zwei Sekunden - und die Tests
    liefen wieder in Echtzeit, genau der Zustand, den diese Datei aufloest.
    """
    wartezeiten = []
    monkeypatch.setattr(R.app_state.shutdown_event, "wait",
                        lambda dauer=None: wartezeiten.append(dauer))
    return wartezeiten


# ==============================================================================
# aktualisierungszeitpunkte() - wann das Schild auf jeden Fall neu zeichnet
# ==============================================================================
def test_stundenbeginn_und_ende_loesen_aus(conf):
    zeitpunkte = aktualisierungszeitpunkte(conf["SCHEDULE"])
    assert "09:55" in zeitpunkte, "Der Stundenbeginn fehlt"
    assert "10:40" in zeitpunkte, "Das Stundenende fehlt"


def test_fuenf_minuten_vorlauf_vor_jeder_stunde(conf):
    """
    Der Vorlauf ist der Grund, warum die naechste Stunde schon an der Tuer
    steht, wenn die Klasse ankommt. Faellt er weg, wechselt das Schild erst,
    wenn alle davorstehen.
    """
    zeitpunkte = aktualisierungszeitpunkte(conf["SCHEDULE"])
    for beginn, vorlauf in [("08:00", "07:55"), ("09:55", "09:50"),
                            ("13:55", "13:50")]:
        assert vorlauf in zeitpunkte, f"Kein Vorlauf zu {beginn}"


def test_vorlauf_rechnet_ueber_die_volle_stunde_hinweg():
    """
    Eine reine Subtraktion der Minuten ergaebe "10:-3". Die Rechnung laeuft
    deshalb ueber ein echtes Datum.
    """
    zeitpunkte = aktualisierungszeitpunkte({"LESSONS": [{"start": "10:02"}]})
    assert "09:57" in zeitpunkte


def test_schulgrenzen_loesen_aus(conf):
    zeitpunkte = aktualisierungszeitpunkte(conf["SCHEDULE"])
    assert conf["SCHEDULE"]["DAY_START"] in zeitpunkte
    assert conf["SCHEDULE"]["DAY_END"] in zeitpunkte


def test_pausen_loesen_aus():
    """
    Mit einem eigens gebauten Raster statt der Beispielkonfiguration - und das
    ist hier keine Bequemlichkeit, sondern noetig: Im Beispiel faellt JEDER
    Pausenbeginn mit einem Stundenende zusammen und jedes Pausenende mit einem
    Stundenbeginn oder dessen Vorlauf. Dieselbe Zusicherung an der
    Beispielkonfiguration bestuende also auch dann noch, wenn die Pausen gar
    nicht mehr ausgewertet wuerden - ein Mutationstest hat genau das gezeigt.
    """
    zeitpunkte = aktualisierungszeitpunkte(
        {"LESSONS": [], "BREAKS": [{"start": "09:40", "end": "09:49"}]})

    assert "09:40" in zeitpunkte, "Der Pausenbeginn loest nicht aus"
    assert "09:49" in zeitpunkte, "Das Pausenende loest nicht aus"


def test_schulgrenzen_haben_eine_vorgabe():
    """Ohne Eintrag in der config.json gilt das uebliche Raster."""
    zeitpunkte = aktualisierungszeitpunkte({})
    assert "07:55" in zeitpunkte and "15:30" in zeitpunkte


def test_unbrauchbare_uhrzeit_haelt_das_schild_nicht_an():
    """
    Die config.json laesst sich von Hand bearbeiten. Ein Tippfehler darf nicht
    dazu fuehren, dass die Schleife in die Ausnahme laeuft - dann stuende das
    Schild still, waehrend der Webserver weiterlaeuft und alles gesund aussieht.
    """
    zeitpunkte = aktualisierungszeitpunkte(
        {"LESSONS": [{"start": "acht Uhr", "end": "09:35"},
                     {"start": "10:00", "end": None}],
         "BREAKS": [{"start": None, "end": None}]})

    assert "09:35" in zeitpunkte, "Der brauchbare Eintrag wurde mit verworfen"
    assert "10:00" in zeitpunkte and "09:55" in zeitpunkte
    assert "acht Uhr" in zeitpunkte  # unveraendert uebernommen, nur ohne Vorlauf


def test_stundenliste_die_keine_liste_ist_wird_uebergangen():
    zeitpunkte = aktualisierungszeitpunkte({"LESSONS": "08:00", "BREAKS": []})
    assert zeitpunkte == {"07:55", "15:30"}


# ==============================================================================
# ist_schulzeit() - wann das feste Abrufintervall laeuft
# ==============================================================================
@pytest.mark.parametrize("stunde,minute,erwartet", [
    (10, 0, True),      # mitten am Vormittag
    (6, 55, True),      # genau eine Stunde vor Schulbeginn - die Grenze zaehlt
    (6, 54, False),     # eine Minute davor
    (16, 30, True),     # genau eine Stunde nach Schulende
    (16, 31, False),    # eine Minute danach
    (3, 0, False),      # nachts
])
def test_zeitfenster_mit_einer_stunde_puffer(conf, stunde, minute, erwartet):
    """
    Der Puffer ist kein Zufallswert: Vor der ersten Stunde soll der Plan schon
    stehen, nach der letzten koennen noch Nachtraege kommen.
    """
    zeit = datetime.time(stunde, minute)
    assert ist_schulzeit(conf["SCHEDULE"], zeit) is erwartet


def test_kaputtes_raster_ruft_lieber_zu_oft_ab():
    """
    Ein Tippfehler in der config.json darf nicht dazu fuehren, dass das Schild
    den ganzen Tag nichts mehr holt.
    """
    assert ist_schulzeit({"DAY_START": "morgens", "DAY_END": "abends"},
                         datetime.time(3, 0)) is True


@pytest.mark.parametrize("zeit,erwartet", [
    (datetime.time(12, 0), True),    # mitten drin
    (datetime.time(0, 10), False),   # vor 00:30, dem geklemmten Beginn
    (datetime.time(23, 45), False),  # nach 23:30, dem geklemmten Ende
])
def test_puffer_laeuft_nicht_ueber_mitternacht_hinaus(zeit, erwartet):
    """
    Eine Stunde vor 00:30 waere -1 Uhr, eine Stunde nach 23:30 waere 24 Uhr -
    beides gibt es als Uhrzeit nicht. Ohne die Begrenzung waere das eine
    Ausnahme.

    WARUM DIE BEIDEN RANDZEITEN MITGEPRUEFT WERDEN: Ohne sie genuegte der
    Auffangzweig, um den Test zu bestehen - der antwortet im Zweifel ebenfalls
    mit 'ja'. Die Begrenzung koennte also ersatzlos fehlen, und der Test bliebe
    gruen. Erst 00:10 und 23:45 unterscheiden die geklemmte Grenze von der
    Ausnahme: Mit Begrenzung lautet die Antwort 'nein', ohne sie 'ja'.
    """
    assert ist_schulzeit({"DAY_START": "00:30", "DAY_END": "23:30"},
                         zeit) is erwartet


# ==============================================================================
# pruefe_beruehrung() - die Entprellung
# ==============================================================================
@pytest.fixture
def beruehrt(monkeypatch):
    """Der Sensor meldet eine Beruehrung; zaehlt mit, wie oft er gelesen wird."""
    gelesen = []

    def sensor():
        gelesen.append(1)
        return True

    monkeypatch.setattr(steuerung, "check_touch_via_i2c", sensor)
    return gelesen


def test_beruehrung_loest_ein_update_aus(conf, zustand, beruehrt):
    R.app_state.force_update_flag = False
    assert pruefe_beruehrung(conf, zustand, 1000.0 + R.TOUCH_COOLDOWN + 0.1) is True
    assert R.app_state.force_update_flag is True, \
        "Die Beruehrung hat kein Update angestossen"


def test_beruehrung_innerhalb_der_sperrfrist_wird_verworfen(conf, zustand, beruehrt):
    """
    Ein Finger auf dem Sensor loest nicht einmal aus, sondern viele Male
    hintereinander. Ohne Sperrfrist waere jede Beruehrung eine ganze Reihe von
    Abrufen und vollen Refresh-Zyklen auf dem E-Paper.
    """
    R.app_state.force_update_flag = False
    assert pruefe_beruehrung(conf, zustand, 1000.0 + R.TOUCH_COOLDOWN - 0.1) is False
    assert R.app_state.force_update_flag is False


def test_ein_liegender_finger_verlaengert_die_sperre(conf, zustand, beruehrt):
    """
    Der feine Punkt: Der Zeitstempel wird bei JEDER erkannten Beruehrung
    fortgeschrieben, auch bei einer unterdrueckten. Wuerde er nur beim
    Ausloesen gesetzt, liefe die Sperre unter einem liegengebliebenen Finger
    ab und das Schild zeichnete alle fuenf Sekunden neu.
    """
    R.app_state.force_update_flag = False
    schrittweite = R.TOUCH_COOLDOWN * 0.9

    for schritt in range(1, 6):
        ausgeloest = pruefe_beruehrung(conf, zustand, 1000.0 + schrittweite * schritt)
        assert ausgeloest is False, f"Schritt {schritt} hat die Sperre durchbrochen"

    assert R.app_state.force_update_flag is False
    assert zustand.letzte_beruehrung == 1000.0 + schrittweite * 5


def test_abgeschalteter_sensor_wird_gar_nicht_erst_gelesen(conf, zustand, beruehrt):
    """
    Bei TOUCH_ACTIVE=false darf der I2C-Bus nicht angefasst werden - auf einem
    Geraet ohne Touch-Platine bringt jeder Lesezugriff nur Fehlermeldungen.
    """
    aus = {**conf, "TOUCH_ACTIVE": False}
    assert pruefe_beruehrung(aus, zustand, 9999.0) is False
    assert beruehrt == [], "Der Sensor wurde trotz TOUCH_ACTIVE=false gelesen"


def test_ohne_beruehrung_bleibt_der_zeitstempel_stehen(conf, zustand, monkeypatch):
    """
    Sonst wuerde jeder Schleifendurchlauf die Sperrfrist neu starten, und eine
    echte Beruehrung koennte nie mehr ausloesen.
    """
    monkeypatch.setattr(steuerung, "check_touch_via_i2c", lambda: False)
    assert pruefe_beruehrung(conf, zustand, 5000.0) is False
    assert zustand.letzte_beruehrung == 1000.0


# ==============================================================================
# E-Paper-Schonung: statische Meldungen nur einmal pro Tag zeichnen
# ==============================================================================
@pytest.mark.parametrize("meldung,statisch", [
    ("Schönes Wochenende!", True),
    ("Unterrichtsfrei", True),
    ("Unterrichtsfrei!\n(Ferienzeit)", True),
    ("", False),
    ("Kein WLAN/Internet", False),
    (None, False),
])
def test_welche_meldungen_sich_bis_morgen_nicht_aendern(meldung, statisch):
    assert ist_statischer_tag(meldung) is statisch


def test_eine_stoerungsmeldung_wird_weiter_gezeichnet(zustand):
    """
    "Kein WLAN/Internet" darf nicht als statisch gelten: Sobald das Netz
    zurueck ist, soll das Schild wieder den Plan zeigen - und zwar beim
    naechsten Abruf, nicht erst morgen.
    """
    assert zeichnen_ueberspringen(zustand, "Kein WLAN/Internet",
                                  "2026-08-31", False) is False


def test_statische_meldung_wird_nur_einmal_pro_tag_gezeichnet(zustand):
    """
    Jedes Zeichnen ist auf E-Paper ein vollstaendiger Refresh-Zyklus. Ueber ein
    Wochenende kaemen im Viertelstundentakt einige hundert zusammen - fuer ein
    Bild, das sich nie aendert.
    """
    erstes = zeichnen_ueberspringen(zustand, "Schönes Wochenende!", "2026-08-31", False)
    assert erstes is False, "Das erste Bild des Tages muss gezeichnet werden"

    for _ in range(20):
        assert zeichnen_ueberspringen(zustand, "Schönes Wochenende!",
                                      "2026-08-31", False) is True


def test_am_naechsten_tag_wird_wieder_gezeichnet(zustand):
    """Sonst bliebe der Samstag bis zum naechsten Neustart stehen."""
    zeichnen_ueberspringen(zustand, "Schönes Wochenende!", "2026-08-31", False)
    assert zeichnen_ueberspringen(zustand, "Schönes Wochenende!",
                                  "2026-09-01", False) is False


def test_der_knopf_zeichnet_auch_eine_statische_meldung(zustand):
    """
    Bewusste Ausnahme: Ueber den Knopf im Web-Interface soll sich ein
    verschmutztes Panel von Hand bereinigen lassen.
    """
    zeichnen_ueberspringen(zustand, "Schönes Wochenende!", "2026-08-31", False)
    assert zeichnen_ueberspringen(zustand, "Schönes Wochenende!",
                                  "2026-08-31", True) is False


def test_ein_schultag_dazwischen_setzt_die_notiz_zurueck(zustand):
    """
    Ohne das Zuruecksetzen wuerde das naechste Wochenende uebersprungen, weil
    die Notiz noch auf einem alten Datum stuende - das Schild zeigte dann den
    Freitagsplan durch das ganze Wochenende.
    """
    zeichnen_ueberspringen(zustand, "Schönes Wochenende!", "2026-08-31", False)
    zeichnen_ueberspringen(zustand, "", "2026-08-31", False)
    assert zustand.letzter_statischer_tag is None
    assert zeichnen_ueberspringen(zustand, "Schönes Wochenende!",
                                  "2026-08-31", False) is False


def test_schonung_greift_im_ganzen_durchlauf(conf, monkeypatch, zustand,
                                              display_attrappe, ohne_warten, kein_knopfdruck):
    """
    Dieselbe Zusicherung nicht am einzelnen Schritt, sondern am vollstaendigen
    Durchlauf: Nur so faellt auf, wenn der Schritt zwar richtig entscheidet,
    die Schleife seine Antwort aber gar nicht mehr benutzt.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: (None, "Schönes Wochenende!"))
    monkeypatch.setattr(steuerung, "get_update_interval", lambda c: 0)
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(10, 0))

    for _ in range(5):
        ein_durchlauf(zustand)

    assert display_attrappe.anzahl_anzeigen == 1, (
        f"Das Wochenende wurde {display_attrappe.anzahl_anzeigen}-mal gezeichnet "
        "statt genau einmal")


# ==============================================================================
# hole_anzeigedaten() - Abruf, Ruecklage, Demo
# ==============================================================================
def test_demo_ueberspringt_den_abruf(conf, monkeypatch):
    """
    Der Knopf "Lokale Dummy-Daten laden" soll auch dann etwas zeigen, wenn
    WebUntis gar nicht erreichbar ist - etwa beim Aufhaengen.
    """
    def darf_nicht_aufgerufen_werden(c):
        raise AssertionError("Bei der Demo darf nicht abgerufen werden")

    monkeypatch.setattr(steuerung, "get_current_lesson", darf_nicht_aufgerufen_werden)
    R.app_state.show_demo_once = True

    data, err, veraltet = hole_anzeigedaten(conf, True)

    assert data["current"].fach == "Informatik"
    assert err == "" and veraltet is False
    assert R.app_state.show_demo_once is False, \
        "Die Demo bliebe sonst dauerhaft stehen"


def test_ruecklage_wird_als_veraltet_gekennzeichnet(conf, monkeypatch):
    """
    Ohne die Kennzeichnung saehe der Plan von vor drei Stunden genauso aus wie
    ein frisch abgerufener - und niemand wuesste, dass Vertretungen fehlen.
    """
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: (None, R.ERR_NO_NETWORK))
    monkeypatch.setattr(steuerung, "get_offline_fallback",
                        lambda c: ({"current": STUNDE, "next": None}, ""))

    data, err, veraltet = hole_anzeigedaten(conf, False)

    assert data["current"] is STUNDE
    assert veraltet is True


def test_ohne_ruecklage_bleibt_die_fehlermeldung_stehen(conf, monkeypatch):
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: (None, R.ERR_NO_NETWORK))
    monkeypatch.setattr(steuerung, "get_offline_fallback", lambda c: None)

    data, err, veraltet = hole_anzeigedaten(conf, False)

    assert data is None and err == R.ERR_NO_NETWORK and veraltet is False


def test_ein_dauerhafter_fehler_greift_nicht_auf_die_ruecklage(conf, monkeypatch):
    """
    Die Ruecklage ist fuer voruebergehende Stoerungen gedacht. Bei falschen
    Zugangsdaten waere sie ein Schild, das tagelang einen alten Plan zeigt und
    dabei voellig gesund aussieht.
    """
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: (None, "Login fehlgeschlagen"))

    def darf_nicht_aufgerufen_werden(c):
        raise AssertionError("Kein Rueckgriff auf die Ruecklage bei Dauerfehlern")

    monkeypatch.setattr(steuerung, "get_offline_fallback", darf_nicht_aufgerufen_werden)

    data, err, veraltet = hole_anzeigedaten(conf, False)
    assert err == "Login fehlgeschlagen" and veraltet is False


# ==============================================================================
# aktualisiere_anzeige() - zeichnen oder loeschen
# ==============================================================================
def test_abgeschaltetes_display_wird_genau_einmal_geloescht(conf, zustand,
                                                            display_attrappe):
    aus = {**conf, "DISPLAY_ACTIVE": False}

    for _ in range(5):
        aktualisiere_anzeige(aus, zustand, uhrzeit(10, 0), False, False)

    assert display_attrappe.anzahl_loeschen == 1, (
        f"Das leere Panel wurde {display_attrappe.anzahl_loeschen}-mal geloescht - "
        "jedes Mal ein vollstaendiger Refresh-Zyklus")
    assert display_attrappe.anzahl_anzeigen == 0


def test_der_knopf_loescht_auch_ein_bereits_leeres_panel(conf, zustand,
                                                          display_attrappe):
    aus = {**conf, "DISPLAY_ACTIVE": False}
    aktualisiere_anzeige(aus, zustand, uhrzeit(10, 0), False, False)
    aktualisiere_anzeige(aus, zustand, uhrzeit(10, 0), True, False)

    assert display_attrappe.anzahl_loeschen == 2


def test_das_abschalten_selbst_loescht(conf, zustand, monkeypatch, display_attrappe):
    """Der Wechsel von an auf aus muss das Panel einmal leeren."""
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": STUNDE, "next": None}, ""))

    aktualisiere_anzeige(conf, zustand, uhrzeit(10, 0), False, False)
    assert zustand.display_war_aktiv is True

    aktualisiere_anzeige({**conf, "DISPLAY_ACTIVE": False}, zustand,
                         uhrzeit(10, 0), False, False)
    assert display_attrappe.anzahl_loeschen == 1


def test_die_web_oberflaeche_bekommt_dieselben_daten(conf, zustand, monkeypatch):
    """
    Die Vorschau im Browser verspricht "genau das steht auf dem Schild". Wird
    der Zwischenspeicher nicht gefuellt, zeigt sie etwas anderes - und zwar
    unauffaellig.
    """
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": STUNDE, "next": None}, ""))

    aktualisiere_anzeige(conf, zustand, uhrzeit(10, 0), False, False)

    assert R.app_state.current_display_data["current"] is STUNDE
    assert R.app_state.current_display_msg == ""
    assert R.app_state.data_is_stale is False


# ==============================================================================
# ein_durchlauf() - das Zusammenspiel, ohne Thread und ohne Wartezeit
# ==============================================================================
def test_ein_testlauf_haelt_die_schleife_zurueck(conf, zustand, monkeypatch,
                                                  display_attrappe, ohne_warten):
    """
    Waehrend der Testlauf seine Bilder abspielt, darf die Schleife nicht
    dazwischenfunken - sonst ueberschreibt sie das Bild, das gerade geprueft
    wird.
    """
    def darf_nicht_aufgerufen_werden():
        raise AssertionError("Die Schleife hat waehrend des Testlaufs gearbeitet")

    monkeypatch.setattr(steuerung, "get_cached_config", darf_nicht_aufgerufen_werden)
    R.app_state.test_mode_active = True

    ein_durchlauf(zustand)

    assert display_attrappe.anzahl_anzeigen == 0
    assert ohne_warten == [1]


def test_ohne_konfiguration_wird_geduldig_gewartet(zustand, monkeypatch, ohne_warten):
    """Fehlt die config.json, soll das Schild warten statt abzustuerzen."""
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: {})
    ein_durchlauf(zustand)
    assert ohne_warten == [5]


def test_ohne_anlass_wird_nicht_gezeichnet(conf, zustand, monkeypatch,
                                            display_attrappe, ohne_warten, kein_knopfdruck):
    """
    Zwischen zwei Abrufen soll das Panel in Ruhe gelassen werden. Die kurze
    Pause verhindert dabei, dass die Schleife den Pi zu 100% auslastet.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(10, 0))
    monkeypatch.setattr(steuerung, "get_current_lesson", _kein_netz)
    zustand.letztes_update = 1e12  # gerade eben aktualisiert

    ein_durchlauf(zustand)

    assert display_attrappe.anzahl_anzeigen == 0
    assert ohne_warten == [0.5], "Die Pause gegen CPU-Spam fehlt"


def test_eine_exakte_stundenzeit_loest_nur_einmal_aus(conf, zustand, monkeypatch,
                                                       display_attrappe, ohne_warten, kein_knopfdruck):
    """
    Eine Minute dauert sechzig Sekunden, die Schleife dreht sich darin rund
    hundertmal. Ohne die Notiz der ausloesenden Minute wuerde das Panel
    hundertmal neu gezeichnet.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": STUNDE, "next": None}, ""))
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(9, 55))
    zustand.letztes_update = 1e12  # das Intervall darf nicht dazwischenkommen

    for _ in range(10):
        ein_durchlauf(zustand)

    assert display_attrappe.anzahl_anzeigen == 1
    assert zustand.letzte_ausloesende_minute == "09:55"


def test_der_knopf_wird_nach_dem_update_zurueckgesetzt(conf, zustand, monkeypatch,
                                                        display_attrappe, ohne_warten):
    """
    Bleibt das Signal stehen, zeichnet die Schleife von da an ununterbrochen
    neu - der Pi kaeme aus dem Refresh-Zyklus nicht mehr heraus.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": STUNDE, "next": None}, ""))
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(10, 0))
    zustand.letztes_update = 1e12
    R.app_state.force_update_flag = True

    ein_durchlauf(zustand)
    assert R.app_state.force_update_flag is False
    assert display_attrappe.anzahl_anzeigen == 1

    ein_durchlauf(zustand)
    assert display_attrappe.anzahl_anzeigen == 1, \
        "Ohne Anlass wurde ein zweites Mal gezeichnet"


def test_nach_dem_zeichnen_wird_der_touch_alarm_quittiert(conf, zustand, monkeypatch,
                                                           ohne_warten):
    """
    Das Zeichnen selbst loest den Sensor aus (Spannungswechsel am Panel). Ohne
    Quittung bliebe dieser Alarm stehen und die naechste Schleifenrunde hielte
    ihn fuer eine Beruehrung - das Schild zeichnete endlos weiter.
    """
    quittiert = []
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": STUNDE, "next": None}, ""))
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(10, 0))
    monkeypatch.setattr(steuerung, "clear_touch_interrupt_via_i2c",
                        lambda: quittiert.append(1))
    R.app_state.force_update_flag = True

    ein_durchlauf(zustand)

    assert quittiert == [1], "Der Touch-Alarm wurde nach dem Zeichnen nicht quittiert"
    assert ohne_warten == [1.5, 0.5]


def test_ausserhalb_der_schulzeit_laeuft_das_intervall_nicht(conf, zustand, monkeypatch,
                                                              display_attrappe, ohne_warten, kein_knopfdruck):
    """
    Nachts jede Viertelstunde WebUntis zu fragen, braucht niemand - und auf
    einem Schild im dunklen Flur sieht es ohnehin keiner.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(3, 0))
    monkeypatch.setattr(steuerung, "get_current_lesson", _kein_netz)

    for _ in range(5):
        ein_durchlauf(zustand)

    assert display_attrappe.anzahl_anzeigen == 0


def test_der_knopf_wirkt_auch_nachts(conf, zustand, monkeypatch,
                                      display_attrappe, ohne_warten):
    """
    Wer nachts am Schild arbeitet, soll sein Ergebnis sehen - die Sperre gilt
    nur dem selbsttaetigen Intervall.
    """
    monkeypatch.setattr(steuerung, "get_cached_config", lambda: conf)
    monkeypatch.setattr(steuerung, "get_current_lesson",
                        lambda c: ({"current": STUNDE, "next": None}, ""))
    monkeypatch.setattr(steuerung, "get_now", lambda: uhrzeit(3, 0))
    R.app_state.force_update_flag = True

    ein_durchlauf(zustand)

    assert display_attrappe.anzahl_anzeigen == 1
