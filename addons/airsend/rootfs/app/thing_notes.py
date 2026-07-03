"""
Decodage des `thingnotes.notes[]` d'un ThingEvent en (type_derive, valeur).

Port fidele de HassAPI::convertNotesToStates() (hassapi.class.php), qui est du
savoir-faire de terrain confirme, pas une reconstruction depuis le spec seul.
On ne reinvente pas cette logique, on la traduit en Python.

note.type (entier, cf. AirSendWebService.yaml ThingNotes.notes[].type) :
  0 = STATE, 1 = DATA, 2 = TEMPERATURE, 3 = ILLUMINANCE, 4 = R_HUMIDITY, 9 = LEVEL

note.value quand type == STATE, valeurs entieres rencontrees en pratique :
  17=STOP 18=TOGGLE 19=OFF 20=ON 21=CLOSE 22=OPEN 33=MIDDLE 34=DOWN 35=UP
  36=LEFT 37=RIGHT 38=USERPOSITION
"""

from __future__ import annotations

# Table de secours si jamais value arrive en forme texte (enum string) plutot
# qu'entiere - index = valeur entiere correspondante, "" = position inutilisee.
_STATE_ENUM_TO_INT = {
    "PING": 1,
    "PROG": 2,
    "UNPROG": 3,
    "RESET": 4,
    "STOP": 17,
    "TOGGLE": 18,
    "OFF": 19,
    "ON": 20,
    "CLOSE": 21,
    "OPEN": 22,
    "MIDDLE": 33,
    "DOWN": 34,
    "UP": 35,
    "LEFT": 36,
    "RIGHT": 37,
    "USERPOSITION": 38,
}


def _as_int_state_value(raw_value) -> int | None:
    if isinstance(raw_value, (int, float)):
        return int(raw_value)
    if isinstance(raw_value, str):
        if raw_value.isdigit():
            return int(raw_value)
        return _STATE_ENUM_TO_INT.get(raw_value)
    return None


def convert_notes_to_states(notes: list[dict]) -> list[tuple[str, object]]:
    """
    Retourne une liste de (stype, svalue) exploitables par les modules domains/*.

    stype in {"toggle", "level", "state", "data", "temperature", "illuminance",
              "r_humidity"}
    """
    results: list[tuple[str, object]] = []
    if not notes:
        return results

    for note in notes:
        note_type = note.get("type")
        raw_value = note.get("value")
        stype: str | None = None
        svalue = None

        if note_type == 0:  # STATE
            ivalue = _as_int_state_value(raw_value)
            if ivalue is not None:
                stype = "state"
                if ivalue == 18:  # TOGGLE
                    stype, svalue = "toggle", "pressed"
                elif ivalue == 19:  # OFF
                    stype, svalue = "level", 0
                elif ivalue == 20:  # ON
                    stype, svalue = "level", 100
                elif ivalue == 17:  # STOP
                    svalue = "stop"
                elif ivalue in (33, 38):  # MIDDLE / USERPOSITION
                    svalue = "user"
                elif ivalue == 34:  # DOWN
                    stype, svalue = "level", 0
                elif ivalue == 35:  # UP
                    stype, svalue = "level", 100
                else:
                    svalue = ivalue  # etat non mappe explicitement, on garde brut

        elif note_type == 1:  # DATA
            stype, svalue = "data", raw_value

        elif note_type == 2:  # TEMPERATURE (Kelvins -> Celsius)
            try:
                svalue = round((float(raw_value) - 273.15) * 10.0) / 10.0
                stype = "temperature"
            except (TypeError, ValueError):
                pass

        elif note_type == 3:  # ILLUMINANCE
            try:
                svalue = float(raw_value)
                stype = "illuminance"
            except (TypeError, ValueError):
                pass

        elif note_type == 4:  # R_HUMIDITY
            try:
                svalue = int(raw_value)
                stype = "r_humidity"
            except (TypeError, ValueError):
                pass

        elif note_type == 9:  # LEVEL
            try:
                svalue = int(raw_value)
                stype = "level"
            except (TypeError, ValueError):
                pass

        if stype is not None:
            results.append((stype, svalue))

    return results
