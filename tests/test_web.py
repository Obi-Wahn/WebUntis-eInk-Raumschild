"""
Tests fuer das Web-Interface: Anmeldung, CSRF-Schutz und Anzeige.

Genutzt wird der Testclient von Flask. Er stellt echte Anfragen an die
Anwendung, ohne dass ein Server laufen muss - die Routen werden also wirklich
ausgefuehrt, samt Decorators fuer Anmeldung und CSRF-Pruefung.
"""
import base64
import statistics
import time

import pytest

import tuerschild as R
from tuerschild import web
from conftest import uhrzeit


# ==============================================================================
# Anmeldung
# ==============================================================================
def test_ohne_zugangsdaten_kein_zutritt(webclient):
    client, _ = webclient
    antwort = client.get("/")
    assert antwort.status_code == 401


def test_mit_richtigen_zugangsdaten_erreichbar(webclient):
    client, kopf = webclient
    assert client.get("/", headers=kopf).status_code == 200


def test_falsches_passwort_wird_abgelehnt(webclient):
    client, _ = webclient
    falsch = {"Authorization": "Basic " + base64.b64encode(b"admin:falsch").decode()}
    assert client.get("/", headers=falsch).status_code == 401


def test_falscher_benutzername_wird_abgelehnt(webclient):
    client, _ = webclient
    falsch = {"Authorization": "Basic " + base64.b64encode(b"root:geheim").decode()}
    assert client.get("/", headers=falsch).status_code == 401


def test_klartextpasswort_wird_beim_ersten_login_gehasht(webclient):
    """
    Auto-Migration: In der config.json darf das Passwort im Klartext stehen,
    danach liegt dort nur noch ein Hash - selbst wenn die SD-Karte in falsche
    Haende geraet.
    """
    client, kopf = webclient
    client.get("/", headers=kopf)

    gespeichert = R.get_cached_config()["ADMIN_PASS"]
    assert gespeichert.startswith("scrypt:") or gespeichert.startswith("pbkdf2:")
    assert "geheim" not in gespeichert


def test_fehlende_angaben_werfen_keine_ausnahme():
    """check_password_hash(hash, None) wuerde ohne Absicherung abstuerzen."""
    assert R.check_auth(None, None) is False
    assert R.check_auth("", "") is False


def test_falscher_name_wird_nicht_schneller_abgelehnt(webclient):
    """
    Schutz gegen Timing-Angriffe: Wuerde die Passwortpruefung bei falschem
    Namen uebersprungen, waere an der Antwortzeit ablesbar, ob ein
    Benutzername existiert. Die Pruefung ist bewusst rechenintensiv, der
    Unterschied waere also deutlich messbar.
    """
    client, kopf = webclient
    client.get("/", headers=kopf)          # loest die Hash-Migration einmalig aus

    def mittlere_dauer(benutzer, passwort, laeufe=5):
        zeiten = []
        for _ in range(laeufe):
            beginn = time.perf_counter()
            R.check_auth(benutzer, passwort)
            zeiten.append(time.perf_counter() - beginn)
        return statistics.median(zeiten)

    falscher_name = mittlere_dauer("voelligAndererName", "geheim")
    falsches_passwort = mittlere_dauer("admin", "falschesPasswort")

    verhaeltnis = falscher_name / falsches_passwort
    assert 0.4 < verhaeltnis < 2.5, f"Verhaeltnis {verhaeltnis:.2f}"


# ==============================================================================
# CSRF-Schutz
# ==============================================================================
def test_post_ohne_token_wird_abgewiesen(webclient):
    client, kopf = webclient
    assert client.post("/update", headers=kopf).status_code == 403


def test_post_mit_falschem_token_wird_abgewiesen(webclient):
    client, kopf = webclient
    antwort = client.post("/update", headers=kopf, data={"csrf_token": "erfunden"})
    assert antwort.status_code == 403


def test_post_mit_richtigem_token_wird_angenommen(webclient):
    client, kopf = webclient
    antwort = client.post("/update", headers=kopf,
                          data={"csrf_token": R.app_state.csrf_token})
    assert antwort.status_code == 302          # Weiterleitung zurueck zur Startseite
    assert R.app_state.force_update_flag is True


def test_csrf_token_wird_zeitkonstant_verglichen(webclient, monkeypatch):
    """
    Der Unterschied zwischen '==' und secrets.compare_digest() ist reines
    Zeitverhalten und liesse sich bei einem 64-Zeichen-Token nicht zuverlaessig
    messen - der Vergleich ist dafuer viel zu schnell.

    Statt der Zeit pruefen wir deshalb die Zusammenarbeit: Wird beim Vergleich
    des Tokens ueberhaupt compare_digest aufgerufen? Ein Rueckfall auf '=='
    faellt damit auf.
    """
    aufrufe = []
    echt = web.secrets.compare_digest

    def mitschreiben(a, b):
        aufrufe.append((a, b))
        return echt(a, b)

    monkeypatch.setattr(web.secrets, "compare_digest", mitschreiben)

    client, kopf = webclient
    client.post("/update", headers=kopf, data={"csrf_token": R.app_state.csrf_token})

    # check_auth vergleicht ebenfalls zeitkonstant - uns interessiert der
    # Aufruf, an dem der CSRF-Token beteiligt war.
    assert any(R.app_state.csrf_token in paar for paar in aufrufe), \
        "Der CSRF-Token wurde nicht mit compare_digest verglichen"


def alle_routen():
    """
    Jede Route der Anwendung, ausser dem statischen Dateiausgang von Flask.

    Die Tests darunter gehen diese Liste durch, statt einzelne Pfade
    aufzuzaehlen. Der Unterschied zaehlt beim NAECHSTEN Knopf: Eine aufgezaehlte
    Liste waechst nicht mit, eine durchlaufene schon.
    """
    for regel in R.app.url_map.iter_rules():
        if regel.endpoint == "static":
            continue
        assert not regel.arguments, (
            f"{regel.rule} hat Plat" "zhalter im Pfad - dieser Test muss dafuer "
            "erweitert werden, sonst prueft er die Route stillschweigend nicht")
        yield regel


@pytest.fixture
def ohne_nebenwirkungen(monkeypatch):
    """
    SICHERHEITSNETZ fuer die beiden Vollstaendigkeitstests darunter.

    Sie pruefen, ob sich Routen ohne Anmeldung oder ohne CSRF-Token ausloesen
    lassen. Faellt einer dieser Schutzschilde weg, laeuft der Rumpf der Route
    aber wirklich - und hinter /sys_shutdown steht ein poweroff. Auf dem
    Raspberry Pi laesst update.sh diese Testsuite laufen, und dort ist die
    Sudoers-Regel dafuer eingerichtet: Der Test wuerde also genau dann das
    Geraet abschalten, wenn er einen Fehler findet.

    Ein blosses Ersetzen von subprocess.Popen genuegt nicht - der Aufruf
    passiert in einem Thread nach 2,5 Sekunden Wartezeit, also womoeglich erst,
    wenn der Test schon vorbei und die Ersetzung zurueckgenommen ist. Deshalb
    wird der Thread selbst ersetzt: Er wird gar nicht erst gestartet.
    """
    gestartet = []

    class Scheinthread:
        def __init__(self, *args, **kwargs):
            gestartet.append(kwargs.get("target"))

        def start(self):
            pass

    monkeypatch.setattr(web.threading, "Thread", Scheinthread)
    monkeypatch.setattr(web.subprocess, "Popen",
                        lambda *a, **k: pytest.fail(f"Systembefehl ausgefuehrt: {a}"))
    return gestartet


def test_keine_route_ist_ohne_anmeldung_erreichbar(webclient, ohne_nebenwirkungen):
    """
    Heute tragen alle Routen @requires_auth. Nichts hindert die naechste daran,
    es zu vergessen - und auffallen wuerde es nicht, denn die Seite
    funktioniert ja, sie steht nur offen.
    """
    client, _ = webclient
    for regel in alle_routen():
        methode = "POST" if "POST" in regel.methods else "GET"
        # Die Fehlversuchsliste vor jeder Route leeren: Sonst sperrt der
        # Ratenbegrenzer nach der fuenften Route die eigene Adresse, und alle
        # weiteren bekaemen 429 - ohne dass ihre Anmeldepruefung je erreicht
        # wuerde. Der Test saehe weiterhin gruen aus und pruefte nichts mehr.
        with R.app_state.state_lock:
            R.app_state.failed_logins.clear()

        antwort = client.open(regel.rule, method=methode)

        assert antwort.status_code == 401, (
            f"{regel.rule} antwortet ohne Zugangsdaten mit {antwort.status_code} "
            "statt 401 - fehlt dort @requires_auth?")


def test_jede_schreibende_route_verlangt_den_csrf_token(webclient, ohne_nebenwirkungen):
    """
    Ohne diesen Schutz genuegt ein praeparierter Link in einer Mail, den eine
    angemeldete Lehrkraft anklickt, um das Schild abzuschalten oder den Pi
    herunterzufahren.
    """
    client, kopf = webclient
    geprueft = 0
    for regel in alle_routen():
        if "POST" not in regel.methods:
            continue
        geprueft += 1

        antwort = client.post(regel.rule, headers=kopf)

        assert antwort.status_code == 403, (
            f"{regel.rule} laesst sich ohne CSRF-Token ausloesen "
            f"({antwort.status_code}) - fehlt dort @verify_csrf?")

    assert geprueft >= 8, \
        f"Nur {geprueft} schreibende Routen gefunden - wird wirklich alles durchlaufen?"
    assert not ohne_nebenwirkungen, \
        "Eine Route hat trotz fehlendem Token einen Hintergrundvorgang gestartet"


def test_systembefehle_sind_ebenfalls_geschuetzt(webclient):
    """Ueber diese Routen laesst sich der Pi neu starten - ohne Token niemals."""
    client, kopf = webclient
    for route in ["/sys_reboot", "/sys_shutdown"]:
        assert client.post(route, headers=kopf).status_code == 403


# ==============================================================================
# Einstellungen speichern
# ==============================================================================
def test_zu_kurzes_intervall_wird_abgelehnt_statt_zurechtgebogen(webclient):
    """
    Frueher wurde ein zu kurzer Wert stillschweigend auf MIN_UPDATE_SECONDS
    hochgesetzt und die Seite meldete "Einstellungen gespeichert.". Wer 5
    eingab und eine Bestaetigung las, hatte in Wahrheit 300 - und keinen
    Hinweis darauf.

    Die Begrenzung beim LESEN bleibt bestehen (tests/test_konfiguration.py):
    Sie ist die Absicherung fuer eine von Hand bearbeitete config.json, die an
    keinem Formular vorbeikommt.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()["AUTO_UPDATE_SECONDS"]

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Raum101",
        "AUTO_UPDATE_SECONDS": "5",
    })

    assert R.get_cached_config()["AUTO_UPDATE_SECONDS"] == vorher
    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Nicht gespeichert" in seite
    assert str(R.MIN_UPDATE_SECONDS) in seite, \
        "Die Meldung nennt den erlaubten Mindestwert nicht"


def test_raumname_wird_uebernommen(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Chemie 2",
        "AUTO_UPDATE_SECONDS": "900",
    })
    assert R.get_cached_config()["ROOM_NAME"] == "Chemie 2"


# ==============================================================================
# Darstellung des Dashboards
# ==============================================================================
def test_ohne_stoerung_kein_warnbanner(webclient):
    client, kopf = webclient
    R.app_state.data_is_stale = False
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "nicht erreichbar" not in inhalt


def test_bei_stoerung_erscheint_das_warnbanner(webclient):
    client, kopf = webclient
    R.app_state.data_is_stale = True
    R.app_state.last_successful_sync = uhrzeit(8, 15)

    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "WebUntis ist derzeit nicht erreichbar" in inhalt
    assert "31.08.2026 08:15" in inhalt


def test_api_texte_werden_maskiert(webclient):
    """
    Schutz gegen Cross-Site Scripting: Texte aus der WebUntis-API landen
    ungeprueft im Dashboard - inzwischen in der Beschreibung der Vorschau.
    Enthielten sie HTML, duerfte es nicht ausgefuehrt werden.

    Seit die Vorschau ein Bild ist, baut die Seite aus diesen Texten gar kein
    HTML mehr zusammen; Jinja maskiert sie beim Einsetzen ins Attribut. Die
    Zusicherung gilt unveraendert, nur der Weg dorthin ist ein anderer.
    """
    client, kopf = webclient
    R.app_state.current_display_msg = "<script>alert('xss')</script>"

    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "<script>alert" not in inhalt
    assert "&lt;script&gt;" in inhalt


def test_die_meldung_steht_in_der_bildbeschreibung(webclient):
    """
    Die Vorschau ist ein Bild und damit fuer eine Vorlesesoftware stumm. Was
    auf dem Schild steht, muss deshalb im alt-Attribut als Text erscheinen.

    Frueher stand die Meldung als HTML auf der Seite und der Zeilenumbruch
    wurde zu <br>. In einem Attribut waere ein <br> sinnlos - der Umbruch wird
    hier zum Leerzeichen.
    """
    client, kopf = webclient
    R.app_state.current_display_msg = "Schöne Ferien!\n(Sommerferien)"

    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert 'alt="' in inhalt
    assert "Schöne Ferien! (Sommerferien)" in inhalt


def test_simulierte_zeit_wird_im_dashboard_ausgewiesen(webclient):
    """
    Ohne diesen Hinweis zeigt die Seite ein voellig plausibles Datum, das mit
    der Wirklichkeit nichts zu tun hat.

    Verglichen wird bewusst ohne Ruecksicht auf Gross- und Kleinschreibung: Die
    Versalien kommen inzwischen aus dem Stylesheet (text-transform), stehen
    also nicht mehr im Quelltext. Der Test soll die Aussage festhalten, nicht
    die Schreibweise.
    """
    client, kopf = webclient
    R.app_state.simulated_datetime = uhrzeit(9, 0)
    inhalt = client.get("/", headers=kopf).get_data(as_text=True)
    assert "zeit wird simuliert" in inhalt.lower()
