"""
Tests fuer die beiden Knoepfe "Lokale Dummy-Daten laden" und "Display-Testlauf".

Beide zeigen erfundene Daten auf dem Schild, verfolgen aber verschiedene Zwecke:
Die Dummy-Daten beantworten "wie sieht das Schild im Regelfall aus?", der
Testlauf spielt die Sonderfaelle durch, die im Alltag selten zusammenkommen.

Bis hierher war keine der beiden Funktionen von einem Test beruehrt. Das faellt
nicht auf, weil beide nur auf Knopfdruck laufen - und wer sie drueckt, sieht
zwar ein Bild, aber nicht, ob es das richtige ist.
"""
import datetime
import re

import pytest

import tuerschild as R
from tuerschild import steuerung
from tuerschild.konstanten import PRUEFUNG_KLASSENARBEIT
from tuerschild.zustand import Lesson
from conftest import MONTAG


# ==============================================================================
# Lokale Dummy-Daten
# ==============================================================================
def test_die_dummy_daten_zeigen_gewoehnlichen_unterricht():
    """
    Der Knopf beantwortet die Frage "wie sieht das Schild mit Daten aus?".
    Frueher stand dort eine Vertretung - wer den Regelfall sehen wollte, bekam
    also ein Etikett zu sehen und den Alltag nie. Die Sonderfaelle spielt der
    Testlauf ohnehin durch.
    """
    daten = steuerung.demo_daten()

    assert daten["current"].status_code is None, \
        "Die Dummy-Daten zeigen einen Sonderfall statt des Regelfalls"
    assert daten["next"].status_code is None


def test_die_dummy_daten_fuellen_beide_bloecke():
    """
    JETZT und DANACH sollen beide belegt sein - sonst zeigt der Knopf ein
    halbleeres Schild und beantwortet die Frage nur zur Haelfte.
    """
    daten = steuerung.demo_daten()

    assert isinstance(daten["current"], Lesson)
    assert isinstance(daten["next"], Lesson)
    assert daten["current"].fach and daten["next"].fach


def test_die_dummy_daten_zeigen_auch_eine_bemerkung():
    """
    Die Detailzeile mit dem Bemerkungstext ist der Teil, der am ehesten zu eng
    wird. Ohne Text im Beispiel sieht man beim Aufhaengen genau den nicht.
    """
    assert steuerung.demo_daten()["current"].stunden_info


# ==============================================================================
# Display-Testlauf
# ==============================================================================
@pytest.fixture
def testlauf(monkeypatch):
    """
    Laesst den Testlauf ohne die Wartezeiten durchlaufen und schneidet mit, was
    an das Display ging - und was zur selben Zeit in der Web-Vorschau stand.
    """
    mitschnitt = []

    def aufzeichnen(data, msg, conf):
        mitschnitt.append({
            "daten": data,
            "meldung": msg,
            "vorschau_meldung": R.app_state.current_display_msg,
            "vorschau_daten": R.app_state.current_display_data,
            "testmodus": R.app_state.test_mode_active,
        })

    monkeypatch.setattr(steuerung, "update_display_logic", aufzeichnen)
    monkeypatch.setattr(R.app_state.shutdown_event, "wait", lambda _s: False)
    # Vorher ausdruecklich loeschen: Der Wert steht beim Start ohnehin auf True,
    # eine Zusicherung darauf waere sonst erfuellt, ohne etwas zu pruefen.
    R.app_state.force_update_flag = False
    steuerung.run_display_test_sequence()
    return mitschnitt


def test_die_vorschau_zeigt_dasselbe_wie_das_display(testlauf):
    """
    DER EIGENTLICHE PUNKT: Unter der Vorschau im Web-Interface steht "genau das
    steht auf dem Schild". Frueher stand dort waehrend des Testlaufs
    "TESTLAUF (3/6)...", waehrend das Panel eine Ferienmeldung zeigte - also
    ausgerechnet dann etwas anderes, wenn jemand die Darstellung prueft.
    """
    for schritt in testlauf:
        assert schritt["vorschau_meldung"] == schritt["meldung"]
        assert schritt["vorschau_daten"] == schritt["daten"]


def test_der_testlauf_deckt_die_sonderfaelle_ab(testlauf):
    """
    Der Zweck des Knopfes: Faelle vorfuehren, die im Alltag selten
    zusammenkommen. Faellt einer davon aus der Liste, faellt das niemandem auf.
    """
    zustaende = [s["daten"]["current"].status_code
                 for s in testlauf if s["daten"] and s["daten"].get("current")]
    meldungen = [s["meldung"] for s in testlauf if not s["daten"]]

    assert "cancelled" in zustaende, "Kein Ausfall im Testlauf"
    assert "irregular" in zustaende, "Keine Vertretung im Testlauf"
    assert None in zustaende, "Kein gewoehnlicher Unterricht im Testlauf"
    assert any("Ferien" in m for m in meldungen)
    assert any("Wochenende" in m for m in meldungen)
    assert any("WLAN" in m for m in meldungen)


def test_eine_stunde_ohne_folgestunde_ist_dabei(testlauf):
    """
    Der Fall, in dem der DANACH-Block leer bleibt, hat ein eigenes Layout - und
    faellt sonst nur am letzten Schultag vor den Ferien auf.
    """
    assert any(s["daten"] and s["daten"].get("next") is None for s in testlauf)


def test_waehrend_des_laufs_ist_der_testmodus_gesetzt(testlauf):
    """
    Der Testmodus haelt die Hintergrundschleife an. Ohne ihn wuerde sie
    mitten im Testlauf echte Daten dazwischenzeichnen.
    """
    assert all(s["testmodus"] for s in testlauf)


def test_danach_laeuft_der_normalbetrieb_wieder_an(testlauf):
    """
    Bliebe der Testmodus stehen, waere das Schild dauerhaft eingefroren - und
    zwar mit dem letzten Testbild, also einer Falschauskunft an der Tuer.
    """
    assert R.app_state.test_mode_active is False
    assert R.app_state.force_update_flag is True, \
        "Ohne erzwungenes Update bliebe das letzte Testbild bis zum naechsten Abruf stehen"


# ==============================================================================
# Das Web-Interface sagt, dass gerade getestet wird
# ==============================================================================
def displayzeile(inhalt):
    """
    Liest den Wert der Zeile "Display" aus der Statusliste.

    Bewusst nicht "Testlauf" in der ganzen Seite suchen: Das Wort steht dort
    ohnehin, im Knopf "Display-Testlauf (ca. 30 Sek)". Eine solche Pruefung
    bestuende auch dann, wenn die Statuszeile gar nichts anzeigte - sie hat
    diesen Test beim Schreiben schon einmal in die Irre gefuehrt.
    """
    treffer = re.search(r"<dt>Display</dt>\s*<dd[^>]*>(.*?)</dd>", inhalt, re.S)
    assert treffer, "Die Zeile 'Display' fehlt in der Statusliste"
    return treffer.group(1).strip()


def test_die_statusliste_weist_auf_den_laufenden_testlauf_hin(webclient):
    """
    Die Vorschau zeigt waehrend des Laufs Testbilder. Sie ist damit ehrlich,
    aber sie zeigt nicht den Betrieb - ohne Hinweis koennte jemand einen
    Ausfall fuer echt halten, den gerade der Testlauf durchspielt.
    """
    client, kopf = webclient
    R.app_state.test_mode_active = True
    try:
        wert = displayzeile(client.get("/", headers=kopf).get_data(as_text=True))
    finally:
        R.app_state.test_mode_active = False

    assert wert == "Testlauf"


def test_ohne_testlauf_steht_dort_der_zustand_des_displays(webclient):
    client, kopf = webclient

    wert = displayzeile(client.get("/", headers=kopf).get_data(as_text=True))

    assert wert in ("an", "aus")


# ==============================================================================
# Echte Namen im Testlauf
# ==============================================================================
# Die Zustaende bleiben fest - ein gewoehnlicher Schultag enthaelt weder Ausfall
# noch Vertretung noch Klassenarbeit, und wer den Knopf drueckt, um genau die zu
# pruefen, saehe sonst sechsmal gewoehnlichen Unterricht. Die NAMEN dagegen
# sollen echt sein: Ihre Laenge entscheidet darueber, wo gekuerzt wird, und mit
# erfundenen Namen sieht man die eigene engste Stelle nie.
def plan_mit(*faecher):
    """Ein Tagesplan aus TimedLesson-Eintraegen mit den genannten Faechern."""
    from tuerschild.zustand import TimedLesson
    eintraege = []
    for nummer, fach in enumerate(faecher):
        beginn = datetime.datetime.combine(MONTAG, datetime.time(8 + nummer, 0))
        stunde = Lesson(fach[:2], fach, "Ab", "9B",
                        f"{beginn:%H:%M} - {beginn.hour:02d}:45",
                        f"{nummer + 1}. Std.", None, "")
        eintraege.append(TimedLesson(start=beginn,
                                     end=beginn + datetime.timedelta(minutes=45),
                                     lesson=stunde))
    return eintraege


def faecher_im_testlauf(szenarien):
    return {s[0]["current"].fach_lang for s in szenarien if s[0]}


def test_der_testlauf_benutzt_echte_fachnamen():
    szenarien = steuerung.testlauf_szenarien(plan_mit("Mathematik", "Kunst"))

    assert faecher_im_testlauf(szenarien) == {"Mathematik"}, \
        "Der Testlauf zeigt erfundene Faecher, obwohl ein Tagesplan vorliegt"


def test_gewaehlt_wird_der_laengste_fachname():
    """
    Neben dem Etikett wird der Platz knapp. Die engste Stelle zeigt sich nur am
    laengsten Namen - einen kurzen zu nehmen hiesse, die Pruefung zu verfehlen.
    """
    szenarien = steuerung.testlauf_szenarien(
        plan_mit("Kunst", "Naturwissenschaften", "Sport"))

    assert faecher_im_testlauf(szenarien) == {"Naturwissenschaften"}


def test_der_danach_block_zeigt_eine_andere_stunde():
    """
    Nimmt der Testlauf zweimal dieselbe Stunde, steht dasselbe Fach unter JETZT
    und DANACH - das sieht nach einem Fehler aus und prueft ausserdem nur eine
    Namenslaenge statt zweier.
    """
    szenarien = steuerung.testlauf_szenarien(plan_mit("Naturwissenschaften", "Kunst"))
    mit_folgestunde = [s[0] for s in szenarien if s[0] and s[0]["next"]]

    assert mit_folgestunde
    for daten in mit_folgestunde:
        assert daten["next"].fach_lang != daten["current"].fach_lang


def test_ohne_tagesplan_greifen_die_ersatzstunden():
    """
    Beim Aufhaengen gibt es noch keinen Plan - genau dann wird der Knopf aber
    gebraucht. Er muss auch ohne Netz und ohne Ruecklage etwas zeigen.
    """
    szenarien = steuerung.testlauf_szenarien(None)

    assert faecher_im_testlauf(szenarien) == {steuerung.ERSATZSTUNDE_JETZT.fach_lang}


def test_ein_leerer_tagesplan_zaehlt_wie_keiner():
    szenarien = steuerung.testlauf_szenarien([])

    assert faecher_im_testlauf(szenarien) == {steuerung.ERSATZSTUNDE_JETZT.fach_lang}


def test_die_ersatzstunde_zeigt_die_kuerzung_auch_ohne_echte_daten():
    """
    Der Sinn der Ersatzstunde ist nicht, irgendetwas zu zeigen, sondern die
    engste Stelle: Fachname neben dem breitesten Etikett. Passt ihr Name dort
    bequem hinein, bleibt genau der Fall ungezeigt, fuer den der Knopf gedrueckt
    wird - und zwar unbemerkt, denn zu sehen ist ja ein sauberes Bild.
    """
    from PIL import Image, ImageDraw
    from tuerschild.anzeige import get_text_width
    from tuerschild.konstanten import (UI_BADGE_GAP, UI_BADGE_PADDING,
                                       UI_MARGIN, UI_WIDTH)

    R.init_fonts()
    flaeche = ImageDraw.Draw(Image.new("1", (UI_WIDTH, R.UI_HEIGHT), 255))
    etikett = get_text_width(flaeche, PRUEFUNG_KLASSENARBEIT,
                             R.app_state.global_fonts["small"])
    platz = (UI_WIDTH - UI_MARGIN
             - (UI_MARGIN + etikett + 2 * UI_BADGE_PADDING + UI_BADGE_GAP))
    breite = get_text_width(flaeche, steuerung.ERSATZSTUNDE_JETZT.fach_lang,
                            R.app_state.global_fonts["reg"])

    assert breite > platz, (
        f"'{steuerung.ERSATZSTUNDE_JETZT.fach_lang}' braucht {breite} px und "
        f"passt damit in die {platz} px neben dem Etikett - die Kuerzung "
        f"kommt im Testlauf ohne echte Daten gar nicht vor")


def test_die_sonderfaelle_bleiben_auch_mit_echten_namen():
    """
    Der eigentliche Zweck des Knopfes darf durch die echten Namen nicht
    verlorengehen.
    """
    szenarien = steuerung.testlauf_szenarien(plan_mit("Mathematik", "Kunst"))
    zustaende = [s[0]["current"].status_code for s in szenarien if s[0]]
    etiketten = [s[0]["current"].pruefung for s in szenarien if s[0]]

    assert None in zustaende
    assert "cancelled" in zustaende
    assert "irregular" in zustaende
    assert PRUEFUNG_KLASSENARBEIT in etiketten
    assert any(s[0] and s[0]["next"] is None for s in szenarien)


def test_jedes_szenario_traegt_eine_bemerkung():
    """
    Echte Stunden haben meistens gar keine Bemerkung. Uebernaehme der Testlauf
    auch die, bliebe die Detailzeile mit ihrer gestaffelten Kuerzung ungeprueft.
    """
    szenarien = steuerung.testlauf_szenarien(plan_mit("Mathematik", "Kunst"))

    assert all(s[0]["current"].stunden_info for s in szenarien if s[0])


def test_die_klassenarbeit_steht_neben_dem_laengsten_fach():
    """
    "KLASSENARBEIT" ist das breiteste Etikett und laesst dem Fachnamen am
    wenigsten Platz. Diese Kombination ist die engste, die das Schild zeigen
    kann - sie gehoert in den Testlauf, nicht in den Betrieb.
    """
    szenarien = steuerung.testlauf_szenarien(plan_mit("Naturwissenschaften"))
    arbeit = [s[0]["current"] for s in szenarien
              if s[0] and s[0]["current"].pruefung]

    assert len(arbeit) == 1
    assert arbeit[0].fach_lang == "Naturwissenschaften"
    assert arbeit[0].status_code is None, \
        "Ein Ausfall wuerde das Klassenarbeits-Etikett verdraengen"


def test_der_lauf_nimmt_den_plan_aus_der_ruecklage(monkeypatch):
    """
    Ohne diese Verbindung liefe der Testlauf zwar, zeigte aber weiterhin
    erfundene Namen - und niemand haette es bemerkt.
    """
    gesehen = []
    monkeypatch.setattr(steuerung, "update_display_logic",
                        lambda data, msg, conf: gesehen.append(data))
    monkeypatch.setattr(R.app_state.shutdown_event, "wait", lambda _s: False)
    R.app_state.cached_lessons = plan_mit("Naturwissenschaften")

    steuerung.run_display_test_sequence()

    assert any(d and d["current"].fach_lang == "Naturwissenschaften"
               for d in gesehen)
