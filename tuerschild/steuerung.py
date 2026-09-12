"""
==============================================================================
Steuerungs-Ebene: Hintergrundschleife und Testlauf
==============================================================================
Der Kernprozess des Tuerschilds. Er vergleicht die Uhrzeit mit dem Stundenplan,
holt bei Bedarf neue Daten und laesst das Display neu zeichnen.

Die Namen aus anderen Ebenen werden hier als Modul-Namen eingebunden, damit die
Testsuite sie ersetzen kann - siehe den Hinweis in anzeige.py.
"""
import datetime
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Optional

from .anzeige import update_display_logic
from .hardware import (check_touch_via_i2c, clear_display_once,
                       clear_touch_interrupt_via_i2c)
from .konfiguration import (formatiere_dauer, get_cached_config, get_now,
                            get_update_interval)
from .konstanten import (BACKGROUND_ERROR_PAUSE, PRUEFUNG_KLASSENARBEIT,
                         STALE_ALERT_SECONDS, TOUCH_COOLDOWN,
                         TRANSIENT_ERRORS)
from .untis import get_current_lesson, get_offline_fallback
from .zustand import Lesson, app_state

def demo_daten():
    """
    Ein gewoehnlicher Schultag, wie ihn das Schild im Regelfall zeigt.

    WOFUER: Der Knopf "Lokale Dummy-Daten laden" beantwortet die Frage "wie
    sieht das Schild mit Daten aus?" - etwa beim Aufhaengen, bevor WebUntis
    ueberhaupt erreichbar ist.

    WARUM GEWOEHNLICHER UNTERRICHT: Frueher stand hier eine Vertretung. Wer
    den Knopf drueckt, um den Regelfall zu sehen, bekam dann ein Schild mit
    Etikett zu sehen und haette den Alltag nie zu Gesicht bekommen. Die
    Sonderfaelle - Ausfall, Vertretung, Ferien, kein Netz - spielt der
    Testlauf ohnehin nacheinander durch.
    """
    return {
        "current": Lesson("Informatik", "Informatik", "Ab", "11B",
                          "09:55 - 10:40", "3. Std.", None,
                          "Theorieunterricht - Netzwerktechnik"),
        "next": Lesson("Geschichte", "Geschichte", "Cd", "9B",
                       "10:45 - 11:30", "4. Std.", None, ""),
    }

# Erfundene Stunden fuer den Fall, dass noch kein Tagesplan vorliegt - etwa
# beim Aufhaengen, bevor WebUntis ueberhaupt erreichbar war.
#
# Der Name der ersten ist mit Bedacht lang: Neben dem breitesten Etikett
# ("KLASSENARBEIT") bleiben 140 Pixel, "Gesellschaftswissenschaften" braucht
# 195. So zeigt der Testlauf die Kuerzung auch dann, wenn noch keine echten
# Namen vorliegen. Ein kuerzerer Name wuerde schlicht passen - und die engste
# Stelle des Layouts bliebe ungezeigt. tests/test_demo_und_testlauf.py haelt
# das nach, damit die Zusicherung nicht beim naechsten Umbenennen verlorengeht.
ERSATZSTUNDE_JETZT = Lesson("GeWi", "Gesellschaftswissenschaften", "Gk", "8C",
                            "11:45 - 12:30", "5. Std.", None, "")
ERSATZSTUNDE_DANACH = Lesson("WuN", "Werte u. Normen", "Ab", "9B",
                             "12:35 - 13:20", "6. Std.", None, "")

# Bemerkungstexte der Szenarien. Sie bleiben erfunden, auch wenn die Stunden
# echt sind: Echte Stunden tragen meistens gar keine Bemerkung, und dann bliebe
# die Detailzeile mit ihrer gestaffelten Kuerzung im Testlauf ungeprueft.
TESTLAUF_BEMERKUNGEN = ("Buch auf Seite 12 aufschlagen",
                        "Aufgaben in IServ bearbeiten",
                        "Achtung: Raumaenderung nach In2",
                        "Bitte Zirkel und Geodreieck mitbringen")

def _vorlagen_aus_plan(plan):
    """
    Sucht aus dem Tagesplan zwei Stunden als Vorlage fuer den Testlauf.

    Gewaehlt wird die mit dem LAENGSTEN Fachnamen. Genau dort wird es im Layout
    eng: Der Fachname steht neben dem Etikett ("AUSFALL", "KLASSENARBEIT"), und
    was nicht mehr passt, wird gekuerzt. Mit erfundenen Namen sieht man die
    eigene engste Stelle nie - bis sie im Betrieb auftritt.

    Gibt None zurueck, wenn kein Plan vorliegt; dann greifen die Ersatzstunden.
    """
    stunden = [eintrag.lesson for eintrag in (plan or []) if eintrag.lesson]
    if not stunden:
        return None

    laengste = max(stunden, key=lambda stunde: len(stunde.fach_lang or stunde.fach))
    weitere = next((stunde for stunde in stunden if stunde is not laengste), laengste)
    return laengste, weitere

def testlauf_szenarien(plan=None):
    """
    Baut die Bilderfolge des Testlaufs - moeglichst mit echten Fachnamen.

    WARUM NICHT EINFACH DER ECHTE PLAN: Ein gewoehnlicher Schultag enthaelt
    weder Ausfall noch Vertretung noch eine Klassenarbeit. Wer den Knopf
    drueckt, um genau die zu pruefen, saehe sechsmal gewoehnlichen Unterricht.
    Ausserdem waere jeder Durchlauf anders, und zwei Durchlaeufe liessen sich
    nicht mehr vergleichen.

    WARUM TROTZDEM ECHTE NAMEN: Die Laengen sind das, was im Layout zaehlt.
    Heisst das laengste Fach an der Schule anders als in einer erfundenen
    Liste, sieht man dessen Kuerzung erst im Betrieb.

    Die Zustaende werden den echten Stunden also aufgepraegt: dieselbe Stunde
    einmal gewoehnlich, einmal als Ausfall, einmal als Vertretung ohne
    Folgestunde, einmal mit Klassenarbeit.
    """
    vorlagen = _vorlagen_aus_plan(plan)
    jetzt, danach = vorlagen if vorlagen else (ERSATZSTUNDE_JETZT, ERSATZSTUNDE_DANACH)

    def fall(status, bemerkung, folgestunde):
        return ({"current": replace(jetzt, status_code=status, pruefung="",
                                    stunden_info=bemerkung),
                 "next": folgestunde}, "")

    def klassenarbeit(bemerkung):
        # Das laengste Etikett ueberhaupt neben dem laengsten Fachnamen - die
        # engste Kombination, die das Schild zeigen kann.
        return ({"current": replace(jetzt, status_code=None,
                                    pruefung=PRUEFUNG_KLASSENARBEIT,
                                    stunden_info=bemerkung),
                 "next": danach}, "")

    return [
        fall(None, TESTLAUF_BEMERKUNGEN[0], danach),
        fall("cancelled", TESTLAUF_BEMERKUNGEN[1], danach),
        fall("irregular", TESTLAUF_BEMERKUNGEN[2], None),
        klassenarbeit(TESTLAUF_BEMERKUNGEN[3]),
        (None, "Unterrichtsfrei!\n(Ferienzeit)"),
        (None, "Schönes Wochenende!"),
        (None, "Kein WLAN/Internet"),
    ]

def run_display_test_sequence() -> None:
    """
    Spielt die Test-Szenarien nacheinander auf dem Hardware-Display ab.
    Dient zur Überprüfung von Sonderfällen (Ausfall, Vertretung, Klassenarbeit,
    Lauftext) direkt vor Ort im Flur, ohne reale Plandaten manipulieren zu
    müssen - und ohne Netz, denn der Tagesplan kommt aus der Rücklage.
    """
    with app_state.state_lock:
        app_state.test_mode_active = True
        plan = app_state.cached_lessons

    conf = get_cached_config()
    test_cases = testlauf_szenarien(plan)

    for data, msg in test_cases:
        if app_state.shutdown_event.is_set(): break
        # Dieselbe Meldung wie auf dem Display, nicht der Fortschritt des
        # Testlaufs: Die Web-Vorschau liest genau dieses Feld und verspricht
        # darunter "genau das steht auf dem Schild". Frueher stand dort
        # "TESTLAUF (3/6)...", waehrend das Panel etwas ganz anderes zeigte -
        # ausgerechnet in dem Moment, in dem jemand die Darstellung prueft.
        # Dass ein Testlauf laeuft, sagt die Statusliste (test_mode_active).
        with app_state.state_lock:
            app_state.current_display_data = data
            app_state.current_display_msg = msg
        
        update_display_logic(data, msg, conf)
        # .wait() statt sleep() nutzen, um den Vorgang bei einem Shutdown abbrechen zu können
        app_state.shutdown_event.wait(4) 
        
    with app_state.state_lock:
        app_state.test_mode_active = False
        app_state.force_update_flag = True


def melde_stoerungsdauer(stoerung_aktiv: bool, fehler: str) -> None:
    """
    Haelt fest, wie lange WebUntis schon nicht erreichbar ist, und schlaegt
    nach STALE_ALERT_SECONDS einmal Alarm.

    WARUM DAS NOETIG IST:
    Ein kurzer Ausfall ist Alltag und faellt niemandem auf - zu Recht, denn die
    Offline-Ruecklage zeigt den Plan von heute weiter an. Genau das ist bei
    einem laengeren Ausfall aber das Problem: Das Schild sieht vollkommen
    gesund aus. Es zeigt einen plausiblen Stundenplan, nur eben einen, in dem
    seit Stunden keine Vertretung und kein Ausfall mehr nachgetragen wurde.
    Ohne diese Meldung faellt das erst auf, wenn eine Klasse vor der falschen
    Tuer steht.

    Die Meldung geht ins Protokoll (ERROR) und damit ins Journal von systemd -
    dort, wo auch die uebrigen Betriebsmeldungen des Geraets landen. Eine
    Benachrichtigung per Mail wuerde Zugangsdaten eines Mailservers in der
    config.json verlangen; das waere eine Entscheidung der Schule und keine,
    die dieses Programm ungefragt treffen sollte.
    """
    jetzt = time.time()
    with app_state.state_lock:
        if not stoerung_aktiv:
            if app_state.stoerung_gemeldet and app_state.stoerung_seit:
                dauer = formatiere_dauer(jetzt - app_state.stoerung_seit)
                logging.info(f"WebUntis ist wieder erreichbar. Die Störung dauerte {dauer}.")
            app_state.stoerung_seit = None
            app_state.stoerung_gemeldet = False
            return

        if app_state.stoerung_seit is None:
            app_state.stoerung_seit = jetzt

        dauer_sekunden = jetzt - app_state.stoerung_seit
        if dauer_sekunden >= STALE_ALERT_SECONDS and not app_state.stoerung_gemeldet:
            app_state.stoerung_gemeldet = True
            logging.error(
                f"ANHALTENDE STÖRUNG: WebUntis ist seit {formatiere_dauer(dauer_sekunden)} "
                f"nicht erreichbar ({fehler}). Das Schild zeigt weiterhin den zuletzt "
                "abgerufenen Plan von heute - kurzfristige Änderungen fehlen darin. "
                "Bitte Netzwerk und WebUntis-Zugang prüfen."
            )


# ==============================================================================
# Die Hintergrundschleife
# ==============================================================================
# Die Schleife bestand frueher aus einem einzigen Block von rund 170 Zeilen.
# Fachlich steckten darin mehrere voneinander unabhaengige Entscheidungen -
# wann aktualisiert wird, ob eine Beruehrung zaehlt, ob das Panel ueberhaupt
# neu gezeichnet werden muss. Pruefen liess sich davon nur, was am Ende auf
# dem Display landete: Die Schleife war nur als Ganzes aufrufbar, in einem
# eigenen Thread, mit echten Wartezeiten. Die Entprellung der Beruehrung und
# die E-Paper-Schonung blieben deshalb ungeprueft - ausgerechnet die beiden
# Stellen, an denen ein Fehler Verschleiss am Panel bedeutet.
#
# Darum steht jeder dieser Schritte jetzt als eigene Funktion da. Sie sind
# absichtlich klein und ohne Nebenwirkungen ausser den benannten, damit ein
# Test sie einzeln aufrufen kann. Die Schleife selbst reiht sie nur noch
# aneinander. Am Verhalten aendert das nichts - tests/test_schleifenschritte.py
# haelt die Reihenfolge und die Zusicherungen der einzelnen Schritte fest.


@dataclass
class Schleifenzustand:
    """
    Was die Schleife von einem Durchlauf zum naechsten mitnimmt.

    Frueher waren das fuenf lose lokale Variablen im Rumpf der Schleife. Als
    Objekt lassen sie sich einem einzelnen Schritt uebergeben und danach
    nachsehen - genau das braucht ein Test der Entprellung oder der
    E-Paper-Schonung, denn beide bestehen darin, sich etwas zu merken.
    """
    # Zeitpunkt der letzten Aktualisierung (Systemuhr, fuer das Abrufintervall)
    letztes_update: float = 0.0
    # Zeitpunkt der letzten erkannten Beruehrung - Grundlage der Entprellung
    letzte_beruehrung: float = field(default_factory=lambda: time.time())
    # Minute, die zuletzt eine Aktualisierung ausgeloest hat ("09:55"). Ohne
    # diese Notiz wuerde dieselbe Minute sechzig Sekunden lang immer wieder
    # ausloesen.
    letzte_ausloesende_minute: Optional[str] = None
    # Tag, an dem zuletzt eine statische Meldung gezeichnet wurde ("2026-08-31")
    letzter_statischer_tag: Optional[str] = None
    # None = noch unbekannt. Dadurch wird beim ersten Durchlauf mit
    # abgeschaltetem Display einmal geloescht, danach nicht mehr.
    display_war_aktiv: Optional[bool] = None


def aktualisierungszeitpunkte(schedule: dict) -> set:
    """
    Sammelt die Uhrzeiten, zu denen das Schild auf jeden Fall neu zeichnen soll.

    Das sind Stundenbeginn und -ende, die Pausen, der Schulbeginn und das
    Schulende - und fuenf Minuten vor jedem Stundenbeginn, damit die naechste
    Stunde schon an der Tuer steht, wenn die Klasse ankommt.

    PAEDAGOGISCH: Rueckgabe ist ein 'Set' und keine Liste. Sets garantieren
    extrem schnelle Zugriffszeiten (O(1)), was den Pi entlastet - die Schleife
    fragt diese Menge jede halbe Sekunde ab.

    Ein unbrauchbarer Eintrag (Text statt Uhrzeit, fehlendes Feld) wird
    uebergangen statt zu einer Ausnahme zu fuehren: Die config.json laesst
    sich von Hand bearbeiten, und ein Tippfehler darin darf das Schild nicht
    anhalten.
    """
    zeitpunkte = set()
    lessons_conf = schedule.get("LESSONS", [])

    if isinstance(lessons_conf, list):
        for stunde in lessons_conf:
            start_t = stunde.get("start")
            end_t = stunde.get("end")
            if start_t:
                zeitpunkte.add(start_t)
                try:
                    # Berechne den 5-Minuten-Vorlauf
                    h, m = map(int, str(start_t).split(":"))
                    dt = datetime.datetime(2000, 1, 1, h, m) - datetime.timedelta(minutes=5)
                    zeitpunkte.add(dt.strftime("%H:%M"))
                except Exception:
                    pass
            if end_t:
                zeitpunkte.add(end_t)

    for pause in schedule.get("BREAKS", []):
        if pause.get("start"): zeitpunkte.add(pause.get("start"))
        if pause.get("end"): zeitpunkte.add(pause.get("end"))

    zeitpunkte.add(schedule.get("DAY_START", "07:55"))
    zeitpunkte.add(schedule.get("DAY_END", "15:30"))
    return zeitpunkte


def ist_schulzeit(schedule: dict, zeitpunkt: datetime.time) -> bool:
    """
    Sagt, ob der Zeitpunkt in das Zeitfenster faellt, in dem regelmaessig
    abgerufen wird - jeweils eine Stunde vor Schulbeginn bis eine Stunde nach
    Schulende.

    WARUM DER PUFFER: Vor der ersten Stunde soll der Plan schon stehen, und
    nach der letzten koennen noch Nachtraege kommen. Ausserhalb davon laeuft
    das feste Abrufintervall nicht - nachts jede Viertelstunde WebUntis zu
    fragen, brauchte niemand.

    Bei einem unbrauchbaren Eintrag in der config.json lautet die Antwort
    'ja'. Lieber zu oft abrufen als ein Schild, das wegen eines Tippfehlers
    den ganzen Tag nichts mehr holt.
    """
    try:
        ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
        de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
        active_start = datetime.time(max(0, ds_h - 1), ds_m)
        active_end = datetime.time(min(23, de_h + 1), de_m)
        return active_start <= zeitpunkt <= active_end
    except Exception:
        return True


def pruefe_beruehrung(conf: dict, zustand: Schleifenzustand, jetzt: float) -> bool:
    """
    Wertet den Beruehrungssensor aus und setzt bei Bedarf das Update-Signal.

    WARUM DIE ENTPRELLUNG (TOUCH_COOLDOWN): Ein Finger auf dem Sensor loest
    nicht einmal aus, sondern viele Male hintereinander. Ohne Sperrfrist
    wuerde jede Beruehrung eine ganze Reihe von Abrufen und
    Display-Neuzeichnungen anstossen - auf E-Paper jedes Mal ein voller
    Refresh-Zyklus mit Blitzen und Verschleiss.

    Entscheidend und leicht zu uebersehen: Der Zeitstempel wird bei JEDER
    erkannten Beruehrung fortgeschrieben, auch bei einer unterdrueckten. Wer
    den Finger liegen laesst, verlaengert die Sperre also, statt sie nach
    TOUCH_COOLDOWN Sekunden auszuloesen.

    Gibt zurueck, ob diese Beruehrung eine Aktualisierung angestossen hat.
    """
    if not conf.get('TOUCH_ACTIVE', True) or not check_touch_via_i2c():
        return False

    ausgeloest = jetzt - zustand.letzte_beruehrung > TOUCH_COOLDOWN
    if ausgeloest:
        logging.info("Display beruehrt! Update wird vorbereitet...")
        with app_state.state_lock:
            app_state.force_update_flag = True
    zustand.letzte_beruehrung = jetzt
    return ausgeloest


def hole_anzeigedaten(conf: dict, zeige_demo: bool):
    """
    Besorgt, was als naechstes auf dem Schild stehen soll.

    Liefert (Daten, Meldung, veraltet). 'veraltet' heisst: Die Daten stammen
    aus der Offline-Ruecklage, weil WebUntis gerade nicht erreichbar ist - das
    Schild zeigt sie mit einem Hinweis an.
    """
    if zeige_demo:
        data = demo_daten()
        with app_state.state_lock:
            app_state.show_demo_once = False
        return data, "", False

    data, err = get_current_lesson(conf)

    # Vor dem Rueckgriff auf die Ruecklage festhalten, ob der Abruf geglueckt
    # ist: Danach steht in 'err' die Meldung der Ruecklage und die Stoerung
    # waere nicht mehr erkennbar.
    melde_stoerungsdauer(data is None and err in TRANSIENT_ERRORS, err)

    # Ausfallsicherheit: Bei einer vorübergehenden Störung lieber den
    # zuletzt abgerufenen Tagesplan weiterzeigen als eine Fehlermeldung.
    if data is None and err in TRANSIENT_ERRORS:
        fallback = get_offline_fallback(conf)
        if fallback is not None:
            logging.warning(f"WebUntis nicht erreichbar ({err}) - "
                            "zeige zuletzt abgerufene Plandaten.")
            data, err = fallback
            return data, err, True

    return data, err, False


def ist_statischer_tag(err) -> bool:
    """
    Sagt, ob die Meldung sich bis morgen nicht mehr aendern kann - Wochenende,
    Ferien, unterrichtsfreier Tag.
    """
    return (err in ["Schönes Wochenende!", "Unterrichtsfrei"]
            or (isinstance(err, str) and "Ferien" in err))


def zeichnen_ueberspringen(zustand: Schleifenzustand, err, datum: str,
                           ist_manuell: bool) -> bool:
    """
    E-PAPER SCHONUNG: Entscheidet, ob das Neuzeichnen ausfallen darf.

    Eine statische Meldung ("Schönes Wochenende!") aendert sich den ganzen Tag
    nicht. Sie trotzdem alle paar Minuten neu zu zeichnen, ist auf E-Paper
    kein billiger Vorgang: Jedes Zeichnen ist ein vollstaendiger
    Refresh-Zyklus. Ueber ein Wochenende kaemen so einige hundert zusammen -
    fuer ein Bild, das sich nie aendert.

    Deshalb wird pro Tag genau einmal gezeichnet. Ein Druck auf den Knopf im
    Web-Interface zeichnet weiterhin sofort, damit sich ein verschmutztes Bild
    von Hand bereinigen laesst.

    Nebenwirkung: Die Notiz im Zustand wird fortgeschrieben. Sie wird beim
    ersten gewoehnlichen Schultag wieder geloescht, damit das naechste
    Wochenende erneut einmal zeichnet.
    """
    if ist_statischer_tag(err) and not ist_manuell:
        if zustand.letzter_statischer_tag == datum:
            return True
        zustand.letzter_statischer_tag = datum
        return False

    zustand.letzter_statischer_tag = None
    return False


def aktualisiere_anzeige(conf: dict, zustand: Schleifenzustand,
                         current_dt: datetime.datetime, ist_manuell: bool,
                         zeige_demo: bool) -> None:
    """
    Fuehrt eine faellige Aktualisierung aus: Daten holen, fuer die
    Web-Oberflaeche merken, zeichnen - oder bei abgeschaltetem Display das
    Panel leeren.
    """
    if conf.get('DISPLAY_ACTIVE', True):
        data, err, ist_veraltet = hole_anzeigedaten(conf, zeige_demo)

        # Cachen der Ergebnisse für das Webinterface
        with app_state.state_lock:
            app_state.current_display_data = data
            app_state.current_display_msg = err
            app_state.data_is_stale = ist_veraltet

        datum = current_dt.strftime("%Y-%m-%d")
        if not zeichnen_ueberspringen(zustand, err, datum, ist_manuell):
            update_display_logic(data, err, conf, stale=ist_veraltet)
    else:
        # E-PAPER SCHONEN: Nur beim Abschalten einmal loeschen.
        # Frueher lief dieser Zweig bei jedem Intervall und an jeder
        # Stundengrenze - also ein vollstaendiger Loeschzyklus auf
        # einem bereits leeren Panel, alle paar Minuten, den ganzen
        # Tag. Ein manuelles Update loescht weiterhin, damit sich
        # ein verschmutztes Bild von Hand bereinigen laesst.
        if zustand.display_war_aktiv is not False or ist_manuell:
            clear_display_once()

    zustand.display_war_aktiv = conf.get('DISPLAY_ACTIVE', True)


def ein_durchlauf(zustand: Schleifenzustand) -> None:
    """
    Ein einzelner Durchgang der Hintergrundschleife.

    Als eigene Funktion laesst sich der Durchgang im Test einzeln aufrufen -
    ohne Thread, ohne echte Wartezeit und mit nachsehbarem Zustand davor und
    danach.
    """
    with app_state.state_lock:
        is_testing = app_state.test_mode_active

    if is_testing:
        app_state.shutdown_event.wait(1)
        return

    conf = get_cached_config()
    if not conf:
        app_state.shutdown_event.wait(5)
        return

    schedule = conf.get("SCHEDULE", {})
    zeitpunkte = aktualisierungszeitpunkte(schedule)

    now_time_system = time.time()
    current_dt = get_now()
    current_hm = current_dt.strftime("%H:%M")
    schulzeit = ist_schulzeit(schedule, current_dt.time())

    pruefe_beruehrung(conf, zustand, now_time_system)

    with app_state.state_lock:
        ist_manuell = app_state.force_update_flag
        zeige_demo = app_state.show_demo_once

    # Logik: Update erforderlich?
    is_exact_time = (current_hm in zeitpunkte) and (zustand.letzte_ausloesende_minute != current_hm)
    is_interval_reached = (now_time_system - zustand.letztes_update
                           >= get_update_interval(conf)) and schulzeit

    if not (ist_manuell or is_interval_reached or is_exact_time):
        # Kurze Pause verhindert CPU-Spam (100% Auslastung)
        app_state.shutdown_event.wait(0.5)
        return

    if is_exact_time:
        zustand.letzte_ausloesende_minute = current_hm

    with app_state.state_lock:
        app_state.force_update_flag = False

    aktualisiere_anzeige(conf, zustand, current_dt, ist_manuell, zeige_demo)

    zustand.letztes_update = time.time()
    app_state.shutdown_event.wait(1.5)
    clear_touch_interrupt_via_i2c()
    zustand.letzte_beruehrung = time.time()

    # Kurze Pause verhindert CPU-Spam (100% Auslastung)
    app_state.shutdown_event.wait(0.5)


def background_loop() -> None:
    """
    Der Kernprozess (Endlosschleife), der asynchron im Hintergrund läuft.
    Er vergleicht die aktuelle Uhrzeit mit dem Stundenplan und feuert ein
    Update-Event, wenn eine neue Stunde beginnt oder das Display berührt wurde.

    Die eigentliche Arbeit steht in ein_durchlauf(); hier bleibt nur, was eine
    Endlosschleife ausmacht: das Abbruchsignal und das Auffangnetz.
    """
    zustand = Schleifenzustand()

    while not app_state.shutdown_event.is_set():
        try:
            ein_durchlauf(zustand)
        except Exception:
            # AUFFANGNETZ: Ohne diesen Block wuerde eine unerwartete Ausnahme
            # diesen Thread beenden. Das Display bliebe dann fuer immer stehen,
            # waehrend der Webserver in seinem eigenen Thread weiterlaeuft und
            # das System dadurch voellig gesund aussieht - ein Ausfall, der
            # niemandem auffaellt, bis jemand vor der Tuer steht.
            # logging.exception() schreibt den vollstaendigen Aufrufpfad mit,
            # damit die Ursache im Journal nachvollziehbar bleibt.
            logging.exception("Unerwarteter Fehler in der Hintergrundschleife - es wird weitergearbeitet.")
            # Laengere Pause, damit ein dauerhaft auftretender Fehler weder das
            # Log flutet noch den Pi unnoetig belastet.
            app_state.shutdown_event.wait(BACKGROUND_ERROR_PAUSE)
