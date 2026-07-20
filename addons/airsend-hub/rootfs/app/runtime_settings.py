"""
Reglages ajustables depuis HA (entites MQTT "number", categorie config),
partages par reference entre callback_server.py et mqtt_bridge.py.

Volontairement un objet mutable simple plutot qu'une valeur immuable : le
changement doit prendre effet immediatement sur la prochaine trame recue,
sans redemarrage de l'addon.
"""

from __future__ import annotations


class RuntimeSettings:
    def __init__(self) -> None:
        self.bind_duration_s: float = 3600.0

    RELIABILITY_MIN = 6
    RELIABILITY_MAX = 71
