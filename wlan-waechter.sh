#!/bin/bash
# =============================================================================
# WLAN-Waechter: stellt die Verbindung wieder her, wenn sie abgerissen ist
# =============================================================================
# WOFUER DAS DA IST:
# Scheitert die WPA-Schluesselverhandlung ein einziges Mal - in einem dicht
# belegten 2,4-GHz-Band geht schon mal ein Paket verloren -, deutet der
# NetworkManager das als falsches Passwort und fordert ein NEUES an:
#
#     no secrets: No agents were available for this request.
#     state change: need-auth -> failed (reason 'no-secrets')
#
# Auf einem Geraet ohne Bildschirm beantwortet diese Frage niemand. Der
# NetworkManager gibt daraufhin endgueltig auf und sucht nicht einmal mehr
# nach dem Netz. Fuer ein Schild an der Wand ist das der Unterschied zwischen
# "faellt gelegentlich aus und kommt wieder" und "ist weg, bis jemand den
# Stecker zieht". Im Praxisbetrieb beobachtet: derselbe Raum, derselbe Access
# Point, dasselbe Passwort - ein Versuch scheitert, der naechste klappt sofort.
#
# WARUM EIN NEUER VERSUCH GENUEGT:
# Die Sperre gilt nur fuer den laufenden Aktivierungsversuch. Ein frischer
# Versuch nimmt das systemweit gespeicherte Passwort ganz normal. Er tut also
# genau das, was ein Neustart auch tut - nur ohne Neustart.
#
# WAS ER NICHT TUT:
# Er behebt nicht die Ursache. Die verlorenen Handshakes bleiben. Er sorgt nur
# dafuer, dass ein einzelner Fehlschlag nicht mehr endgueltig ist.
#
# Aufruf ohne Argument prueft wlan0; ein anderer Schnittstellenname kann als
# erstes Argument uebergeben werden.
# =============================================================================
set -u

# Erzwingt englische Ausgaben der aufgerufenen Werkzeuge. Ohne das haengt jede
# Auswertung ihrer Texte an der eingestellten Sprache des Geraets.
export LC_ALL=C

GERAET="${1:-wlan0}"

# Geprueft wird die IPv4-Adresse und nicht der Zustandsname des NetworkManagers.
# Zwei Gruende: Der Zustandsname ist uebersetzt ("verbunden"/"connected") und
# damit von der Spracheinstellung abhaengig. Und eine Verbindung ohne Adresse
# ist fuer das Schild genauso wertlos wie gar keine - schlaegt die Adressvergabe
# fehl, haelt der NetworkManager sich trotzdem fuer verbunden.
if ip -4 addr show dev "$GERAET" 2>/dev/null | grep -q 'inet '; then
    exit 0
fi

logger -t wlan-waechter "Keine IPv4-Adresse auf $GERAET - neuer Verbindungsversuch."

# 'device connect' statt 'connection up': Es kommt ohne den Namen des
# WLAN-Profils aus und funktioniert damit in jeder Installation, nicht nur in
# der, in der es geschrieben wurde. Die Wartezeit ist begrenzt, damit sich
# Durchgaenge nicht stauen, wenn das Netz laenger weg ist.
if nmcli --wait 30 device connect "$GERAET" >/dev/null 2>&1; then
    logger -t wlan-waechter "Verbindung wiederhergestellt."
else
    logger -t wlan-waechter "Versuch fehlgeschlagen - naechster Anlauf beim naechsten Durchgang."
fi

# Bewusst immer 0: Ein misslungener Verbindungsversuch ist kein Fehler des
# Dienstes. Ein Fehlschlag wuerde die Einheit als "failed" markieren und den
# Zustand des Geraets damit dauerhaft alarmierend aussehen lassen, obwohl der
# naechste Durchgang laengst wieder greift. Was passiert ist, steht im Journal.
exit 0
