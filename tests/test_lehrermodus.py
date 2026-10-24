"""
Tests fuer den Lehrer-Modus: Das Schild zeigt den Plan einer Lehrkraft statt
eines Raums.

Geprueft wird, dass der Modus die richtige Stelle bei WebUntis fragt, dass auf
dem Bild der Raum an die Stelle der Lehrkraft tritt, und dass bestehende
Konfigurationen ohne "MODE" unveraendert als Raumschild weiterlaufen.
"""
import pytest
from PIL import Image, ImageDraw

import tuerschild as R
from tuerschild import untis
from conftest import Referenz, RohStunde, uhrzeit
from test_webuntis_abruf import Raumliste, SitzungsAttrappe


class RohStundeMitRaum(RohStunde):
    """Eine rohe Stunde, die zusaetzlich ihren Raum kennt."""

    def __init__(self, *args, raum="B204", **kwargs):
        super().__init__(*args, **kwargs)
        self._raum = raum

    @property
    def rooms(self):
        self._pruefe_sitzung()
        return [Referenz(self._raum)] if self._raum else []


class LehrerSitzung(SitzungsAttrappe):
    """Die Sitzungsattrappe, ergaenzt um die Lehrerliste."""

    def __init__(self, notizblock, lehrer_gefunden=True, **kwargs):
        super().__init__(notizblock, **kwargs)
        self._lehrer_gefunden = lehrer_gefunden

    def teachers(self):
        self.notizen["lehrer_gesucht"] = True
        return Raumliste(["Ah"] if self._lehrer_gefunden else [])

    def rooms(self):
        self.notizen["raum_gesucht"] = True
        return super().rooms()

    def timetable_extended(self, start, end, **element):
        self.notizen["element"] = element
        return super().timetable_extended(start=start, end=end, **element)


@pytest.fixture
def lehrerconf(conf):
    return {**conf, "MODE": "lehrer", "TEACHER_NAME": "Ah", "ROOM_NAME": ""}


@pytest.fixture
def lehrersitzung(monkeypatch):
    notizen = {}

    def einrichten(**kwargs):
        monkeypatch.setattr(untis.webuntis, "Session",
                            lambda **_: LehrerSitzung(notizen, **kwargs))
        R.app_state.simulated_datetime = uhrzeit(8, 20)
        return notizen

    return einrichten


# ==============================================================================
# Abruf
# ==============================================================================
def test_lehrermodus_fragt_den_plan_der_lehrkraft_ab(lehrersitzung, lehrerconf):
    notizen = lehrersitzung(stunden=[RohStundeMitRaum(8, 0, 8, 45, "Mathematik")])

    daten, _ = R.get_current_lesson(lehrerconf)

    assert notizen.get("lehrer_gesucht")
    assert not notizen.get("raum_gesucht")
    assert notizen["element"] == {"teacher": "Ah"}
    assert daten["current"].raum == "B204"


def test_raummodus_bleibt_beim_raum(lehrersitzung, conf):
    """Eine config.json ohne MODE ist ein Raumschild - wie bisher."""
    assert "MODE" not in conf or conf["MODE"] == "raum"
    notizen = lehrersitzung(stunden=[RohStundeMitRaum(8, 0, 8, 45, "Mathematik")])

    daten, _ = R.get_current_lesson(conf)

    assert notizen.get("raum_gesucht")
    assert not notizen.get("lehrer_gesucht")
    assert notizen["element"] == {"room": "Raum101"}
    # Im Raum-Modus wird der Raum gar nicht erst gelesen.
    assert daten["current"].raum == ""


def test_unbekannte_lehrkraft_wird_gemeldet(lehrersitzung, lehrerconf):
    lehrersitzung(lehrer_gefunden=False)
    daten, meldung = R.get_current_lesson(lehrerconf)
    assert daten is None
    assert meldung == "Lehrkraft Ah fehlt."


def test_ohne_lehrerkuerzel_ist_die_konfiguration_unvollstaendig(lehrerconf):
    daten, meldung = R.get_current_lesson({**lehrerconf, "TEACHER_NAME": ""})
    assert daten is None
    assert meldung == "Konfiguration unvollständig."


def test_luecke_im_lehrerplan_heisst_freistunde(lehrerconf, conf):
    stunde = R.TimedLesson(uhrzeit(8, 0), uhrzeit(8, 45),
                           R.Lesson("M", "Mathe", "Ah", "9B", "", "", None, ""))
    jetzt = uhrzeit(11, 0)
    assert R.select_lessons([stunde], lehrerconf, jetzt)[1] == "Freistunde"
    assert R.select_lessons([stunde], conf, jetzt)[1] == "Raum ist frei"


# ==============================================================================
# Anzeige
# ==============================================================================
def detailzeile(lesson, lehrermodus, breite=240):
    R.init_fonts()
    draw = ImageDraw.Draw(Image.new('1', (250, 122), 255))
    return R.build_detail_line(draw, lesson, R.app_state.global_fonts['small'],
                               breite, lehrermodus)


def test_detailzeile_zeigt_im_lehrermodus_den_raum():
    stunde = R.Lesson("M", "Mathe", "Ah", "9B", "", "", None, "", raum="B204")
    assert detailzeile(stunde, True) == "Raum: B204 | Kl: 9B"
    assert detailzeile(stunde, False) == "Kl: 9B | Lehrkraft: Ah"


def test_der_raum_faellt_im_lehrermodus_erst_nach_der_klasse_weg():
    stunde = R.Lesson("M", "Mathe", "Ah", "9B", "", "", None,
                      "Achtung: heute im Musiksaal, bitte Noten mitbringen",
                      raum="B204")
    zeile = detailzeile(stunde, True)
    assert zeile.startswith("B204")
    assert "Kl:" not in zeile


def test_kopfzeile_und_beschreibung_folgen_dem_modus(lehrerconf):
    assert R.schildname(lehrerconf) == "Ah"
    assert R.schildname({**lehrerconf, "MODE": "raum", "ROOM_NAME": "Raum101"}) == "Raum101"
    # Das Zeichnen selbst darf im Lehrer-Modus nicht scheitern.
    stunde = R.Lesson("M", "Mathe", "Ah", "9B", "", "", None, "", raum="B204")
    bild = R.zeichne_anzeige({"current": stunde, "next": None}, "", lehrerconf)
    assert bild.size == (R.UI_WIDTH, R.UI_HEIGHT)

    from tuerschild.web import vorschau_beschreibung
    text = vorschau_beschreibung({"current": stunde, "next": None}, "", True)
    assert "Raum B204" in text
    assert "Ah" not in text


# ==============================================================================
# Konfiguration
# ==============================================================================
@pytest.mark.parametrize("wert, erwartet", [("raum", "raum"), ("Lehrer", "lehrer"),
                                            (" lehrer ", "lehrer")])
def test_modus_wird_erkannt(wert, erwartet):
    assert R.pruefe_modus(wert) == (erwartet, None)


def test_unbekannter_modus_wird_beanstandet_und_faellt_auf_raum_zurueck(conf):
    conf = {**conf, "MODE": "klasse"}
    assert not R.ist_lehrermodus(conf)
    assert any(z.startswith("MODE:") for z in R.pruefe_konfiguration(conf))


def test_im_lehrermodus_wird_das_kuerzel_geprueft_nicht_der_raum(lehrerconf):
    assert R.pruefe_konfiguration(lehrerconf) == []
    beanstandet = R.pruefe_konfiguration({**lehrerconf, "TEACHER_NAME": ""})
    assert any(z.startswith("TEACHER_NAME:") for z in beanstandet)


# ==============================================================================
# Formular
# ==============================================================================
def test_formular_schaltet_auf_lehrermodus(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token, "MODE": "lehrer",
        "ROOM_NAME": "", "TEACHER_NAME": " Ah ", "AUTO_UPDATE_SECONDS": "900"})

    gespeichert = R.get_cached_config()
    assert gespeichert["MODE"] == "lehrer"
    assert gespeichert["TEACHER_NAME"] == "Ah"
    # Der leere Raumname ueberschreibt den alten nicht.
    assert gespeichert["ROOM_NAME"] == "Raum101"


def test_lehrermodus_ohne_kuerzel_wird_abgelehnt(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token, "MODE": "lehrer",
        "ROOM_NAME": "Raum101", "TEACHER_NAME": "", "AUTO_UPDATE_SECONDS": "900"})

    assert R.get_cached_config().get("MODE", "raum") == "raum"
    assert "Nicht gespeichert" in client.get("/", headers=kopf).get_data(as_text=True)


def test_dashboard_zeigt_die_lehrkraft_im_kopf(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token, "MODE": "lehrer",
        "TEACHER_NAME": "Ah", "AUTO_UPDATE_SECONDS": "900"})
    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Ah | Lehreranzeige" in seite
