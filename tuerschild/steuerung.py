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
from dataclasses import replace

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


def background_loop() -> None:
    """
    Der Kernprozess (Endlosschleife), der asynchron im Hintergrund läuft.
    Er vergleicht die aktuelle Uhrzeit mit dem Stundenplan und feuert ein 
    Update-Event, wenn eine neue Stunde beginnt oder das Display berührt wurde.
    """
    last_update = 0
    last_touch_time = time.time()
    last_minute_triggered = None
    last_static_date = None
    # None = noch unbekannt. Dadurch wird beim ersten Durchlauf mit
    # abgeschaltetem Display einmal geloescht, danach nicht mehr.
    display_war_aktiv = None

    while not app_state.shutdown_event.is_set():
        try:
            with app_state.state_lock:
                is_testing = app_state.test_mode_active
            
            if is_testing:
                app_state.shutdown_event.wait(1)
                continue

            conf = get_cached_config()
            if not conf:
                app_state.shutdown_event.wait(5)
                continue

            schedule = conf.get("SCHEDULE", {})
            lessons_conf = schedule.get("LESSONS", [])
        
            # PÄDAGOGISCH: Wir nutzen 'Sets' anstelle von Listen für die Suchzeiten. 
            # Sets garantieren extrem schnelle Zugriffszeiten (O(1)), was den Pi entlastet.
            dyn_update_times = set() 
        
            if isinstance(lessons_conf, list):
                for l in lessons_conf:
                    start_t = l.get("start")
                    end_t = l.get("end")
                    if start_t: 
                        dyn_update_times.add(start_t)
                        try:
                            # Berechne den 5-Minuten-Vorlauf
                            h, m = map(int, str(start_t).split(":"))
                            dt = datetime.datetime(2000, 1, 1, h, m) - datetime.timedelta(minutes=5)
                            dyn_update_times.add(dt.strftime("%H:%M"))
                        except Exception: 
                            pass 
                    if end_t: 
                        dyn_update_times.add(end_t)
        
            for b in schedule.get("BREAKS", []):
                if b.get("start"): dyn_update_times.add(b.get("start"))
                if b.get("end"): dyn_update_times.add(b.get("end"))
            
            dyn_update_times.add(schedule.get("DAY_START", "07:55"))
            dyn_update_times.add(schedule.get("DAY_END", "15:30"))
        
            now_time_system = time.time() 
            current_dt = get_now()
            current_hm = current_dt.strftime("%H:%M")
            current_time_obj = current_dt.time()
        
            # Laufzeit-Prüfung: Außerhalb der Schulzeiten updaten wir seltener
            try:
                ds_h, ds_m = map(int, schedule.get("DAY_START", "07:55").split(":"))
                de_h, de_m = map(int, schedule.get("DAY_END", "15:30").split(":"))
                active_start = datetime.time(max(0, ds_h - 1), ds_m)
                active_end = datetime.time(min(23, de_h + 1), de_m)
                is_active_hours = active_start <= current_time_obj <= active_end
            except Exception:
                is_active_hours = True 

            # Touch-Erkennung
            if conf.get('TOUCH_ACTIVE', True) and check_touch_via_i2c():
                if now_time_system - last_touch_time > TOUCH_COOLDOWN:
                    logging.info(f"Display beruehrt! Update wird vorbereitet...")
                    with app_state.state_lock:
                        app_state.force_update_flag = True
                last_touch_time = now_time_system

            with app_state.state_lock:
                current_force_update = app_state.force_update_flag
                current_show_demo = app_state.show_demo_once

            # Logik: Update erforderlich?
            is_exact_time = (current_hm in dyn_update_times) and (last_minute_triggered != current_hm)
            is_interval_reached = (now_time_system - last_update >= get_update_interval(conf)) and is_active_hours

            # Update ausführen
            if current_force_update or is_interval_reached or is_exact_time:
                if is_exact_time: last_minute_triggered = current_hm 
                is_manual = current_force_update 
            
                with app_state.state_lock:
                    app_state.force_update_flag = False
            
                if conf.get('DISPLAY_ACTIVE', True):
                    is_stale = False

                    if current_show_demo:
                        data = demo_daten()
                        err = ""
                        with app_state.state_lock:
                            app_state.show_demo_once = False
                    else:
                        data, err = get_current_lesson(conf)

                        # Vor dem Rueckgriff auf die Ruecklage festhalten, ob der
                        # Abruf geglueckt ist: Danach steht in 'err' die Meldung
                        # der Ruecklage und die Stoerung waere nicht mehr erkennbar.
                        melde_stoerungsdauer(data is None and err in TRANSIENT_ERRORS, err)

                        # Ausfallsicherheit: Bei einer vorübergehenden Störung lieber den
                        # zuletzt abgerufenen Tagesplan weiterzeigen als eine Fehlermeldung.
                        if data is None and err in TRANSIENT_ERRORS:
                            fallback = get_offline_fallback(conf)
                            if fallback is not None:
                                logging.warning(f"WebUntis nicht erreichbar ({err}) - "
                                                "zeige zuletzt abgerufene Plandaten.")
                                data, err = fallback
                                is_stale = True

                    # Cachen der Ergebnisse für das Webinterface
                    with app_state.state_lock:
                        app_state.current_display_data = data
                        app_state.current_display_msg = err
                        app_state.data_is_stale = is_stale

                    current_date = current_dt.strftime("%Y-%m-%d")
                    is_static_day = err in ["Schönes Wochenende!", "Unterrichtsfrei"] or (isinstance(err, str) and "Ferien" in err)
                
                    # E-Paper Schonung: Statische Meldungen (z.B. Ferien) zeichnen wir nur einmal pro Tag neu
                    skip_update = False
                    if is_static_day and not is_manual:
                        if last_static_date == current_date: skip_update = True
                        else: last_static_date = current_date 
                    else: last_static_date = None 
                    
                    if not skip_update:
                        update_display_logic(data, err, conf, stale=is_stale)
                else:
                    # E-PAPER SCHONEN: Nur beim Abschalten einmal loeschen.
                    # Frueher lief dieser Zweig bei jedem Intervall und an jeder
                    # Stundengrenze - also ein vollstaendiger Loeschzyklus auf
                    # einem bereits leeren Panel, alle paar Minuten, den ganzen
                    # Tag. Ein manuelles Update loescht weiterhin, damit sich
                    # ein verschmutztes Bild von Hand bereinigen laesst.
                    if display_war_aktiv is not False or is_manual:
                        clear_display_once()

                display_war_aktiv = conf.get('DISPLAY_ACTIVE', True)
                
                last_update = time.time()
                app_state.shutdown_event.wait(1.5)
                clear_touch_interrupt_via_i2c()
                last_touch_time = time.time()
            
            # Kurze Pause verhindert CPU-Spam (100% Auslastung)
            app_state.shutdown_event.wait(0.5)
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

