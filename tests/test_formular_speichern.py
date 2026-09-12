"""
Tests fuer das Speicherformular im Web-Interface.

Geprueft wird hier die VERDRAHTUNG: dass die Pruefung des Raumnamens
tatsaechlich im Anfrageweg sitzt und nicht nur als Funktion existiert, und dass
bei einer Ablehnung wirklich nichts in der config.json landet.

Die Pruefregeln selbst stehen in tests/test_konfiguration_pruefung.py.
"""
import tuerschild as R


def formular(kopf_token, **felder):
    daten = {"csrf_token": kopf_token, "ROOM_NAME": "Raum101"}
    daten.update(felder)
    return daten


# ==============================================================================
# Raumname
# ==============================================================================
def test_leerer_raumname_wird_abgelehnt(webclient):
    client, kopf = webclient
    vorher = R.get_cached_config()["ROOM_NAME"]

    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": "   "})

    assert R.get_cached_config()["ROOM_NAME"] == vorher
    assert "Nicht gespeichert" in client.get("/", headers=kopf).get_data(as_text=True)


def test_zu_langer_raumname_wird_abgelehnt(webclient):
    client, kopf = webclient
    vorher = R.get_cached_config()["ROOM_NAME"]

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "R" * (R.ROOM_NAME_MAX_LEN + 1)})

    assert R.get_cached_config()["ROOM_NAME"] == vorher


def test_raumname_wird_von_leerzeichen_befreit(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": "  Chemie 2  ",
                                             "AUTO_UPDATE_SECONDS": "900"})
    assert R.get_cached_config()["ROOM_NAME"] == "Chemie 2"


# ==============================================================================
# Abrufintervall
# ==============================================================================
def test_unbrauchbares_intervall_wird_abgelehnt_statt_still_verworfen(webclient):
    """
    Der Anlass fuer diese Pruefung. Gemessen am Stand davor:

        Antwort                  302 (Weiterleitung, wie bei Erfolg)
        Meldung auf der Seite    "Einstellungen gespeichert."
        Wert in der config.json  unveraendert

    Der verworfene Wert war dabei das kleinere Problem. Wer das Intervall
    verstellt und eine Bestaetigung liest, geht davon aus, dass es gilt.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()["AUTO_UPDATE_SECONDS"]

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Raum101",
        "AUTO_UPDATE_SECONDS": "abc"})

    assert R.get_cached_config()["AUTO_UPDATE_SECONDS"] == vorher
    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Nicht gespeichert" in seite, \
        "Die Seite meldet weiterhin Erfolg, obwohl nichts uebernommen wurde"
    assert "gespeichert." not in seite.replace("Nicht gespeichert", ""), \
        "Die Erfolgsmeldung steht zusaetzlich auf der Seite"


def test_ein_abgelehntes_intervall_laesst_auch_den_raumnamen_stehen(webclient):
    """
    ALLES ODER NICHTS, von der anderen Seite her: Wird das Intervall
    abgelehnt, darf auch der Raumname nicht uebernommen werden - sonst waere
    nach der Meldung "Nicht gespeichert" unklar, welcher Stand in der Datei
    steht.

    Heute traegt das schon get_cached_config(), das eine Kopie herausgibt; bis
    save_config() laeuft, aendert eine Zuweisung nichts. Dieser Test haelt die
    Zusicherung an der Oberflaeche fest, damit sie nicht davon abhaengt.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()["ROOM_NAME"]

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Ganz anderer Raum",
        "AUTO_UPDATE_SECONDS": "abc"})

    assert R.get_cached_config()["ROOM_NAME"] == vorher


def test_das_intervall_landet_als_zahl_in_der_datei(webclient):
    """
    Aus dem Formular kommt Text. Stuende "1800" in der config.json, verglichen
    die Schleife eine Zahl mit einer Zeichenkette.
    """
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Raum101",
        "AUTO_UPDATE_SECONDS": "1800"})

    wert = R.get_cached_config()["AUTO_UPDATE_SECONDS"]
    assert wert == 1800 and isinstance(wert, int)


# ==============================================================================
# Rueckmeldung an die Bedienerin
# ==============================================================================
def test_der_grund_der_ablehnung_erscheint_auf_der_seite(webclient):
    """
    Eine wortlose Ablehnung waere die schlechteste Loesung: Das Formular
    springt zurueck, nichts hat sich geaendert, und niemand weiss warum.
    """
    client, kopf = webclient
    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": ""})

    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "Nicht gespeichert" in seite
    assert "leer" in seite


def test_die_fehlermeldung_erscheint_nur_einmal(webclient):
    """
    Sie gehoert zu einem einzelnen Speicherversuch. Bliebe sie stehen, warnte
    die Seite noch Stunden spaeter vor einem laengst behobenen Fehler.
    """
    client, kopf = webclient
    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": ""})

    assert "Nicht gespeichert" in client.get("/", headers=kopf).get_data(as_text=True)
    assert "Nicht gespeichert" not in client.get("/", headers=kopf).get_data(as_text=True)


def test_erfolgreiches_speichern_wird_bestaetigt(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={"csrf_token": R.app_state.csrf_token,
                                             "ROOM_NAME": "Raum101"})

    seite = client.get("/", headers=kopf).get_data(as_text=True)
    assert "gespeichert" in seite.lower()


# ==============================================================================
# Alles oder nichts
# ==============================================================================
def test_bei_einer_ablehnung_wird_gar_nichts_gespeichert(webclient):
    """
    Der wichtigste Test dieser Datei. Wuerde das Formular haeppchenweise
    uebernommen, waere nach einer Fehlermeldung unklar, welcher Stand nun in der
    Datei steht: das Intervall geaendert, der Raum nicht - und niemand sieht es
    der Seite an.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()

    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "   ",
        "AUTO_UPDATE_SECONDS": "1800"})

    nachher = R.get_cached_config()
    assert nachher["ROOM_NAME"] == vorher["ROOM_NAME"]
    assert nachher["AUTO_UPDATE_SECONDS"] == vorher["AUTO_UPDATE_SECONDS"]


def test_gueltiges_formular_uebernimmt_alle_felder(webclient):
    client, kopf = webclient
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Physik 1",
        "AUTO_UPDATE_SECONDS": "1800"})

    conf = R.get_cached_config()
    assert conf["ROOM_NAME"] == "Physik 1"
    assert conf["AUTO_UPDATE_SECONDS"] == 1800


def test_der_stundenplan_wird_vom_formular_nicht_angetastet(webclient):
    """
    Das Web-Interface ist Steuerung und Anzeige, keine Konfigurationsoberflaeche.
    SCHEDULE gehoert in die config.json und wird dort von Hand gepflegt - ein
    mitgesendetes Feld darf daran nichts aendern.
    """
    client, kopf = webclient
    vorher = R.get_cached_config()["SCHEDULE"]

    # Mit gueltigem Intervall, damit das Formular wirklich gespeichert wird:
    # Wuerde es abgelehnt, bliebe SCHEDULE schon deshalb unveraendert und der
    # Test ginge aus dem falschen Grund durch.
    client.post("/save", headers=kopf, data={
        "csrf_token": R.app_state.csrf_token,
        "ROOM_NAME": "Raum101",
        "AUTO_UPDATE_SECONDS": "900",
        "SCHEDULE": '{"DAY_START": "23:00"}'})

    assert R.get_cached_config()["ROOM_NAME"] == "Raum101", \
        "Das Formular wurde gar nicht erst gespeichert"
    assert R.get_cached_config()["SCHEDULE"] == vorher


def test_speichern_braucht_weiterhin_einen_csrf_token(webclient):
    """Die neuen Felder duerfen den Schutz nicht ausgehebelt haben."""
    client, kopf = webclient
    antwort = client.post("/save", headers=kopf, data={"ROOM_NAME": "Ohne Token"})
    assert antwort.status_code == 403
