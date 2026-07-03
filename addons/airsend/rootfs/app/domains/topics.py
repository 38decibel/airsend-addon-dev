"""
Convention de topics MQTT, commune a tous les domains/*.py et a mqtt_bridge.py.

    homeassistant/<component>/airsend_<device.key>/config   (discovery, retained)
    airsend/<device.key>/state                               (etat courant, retained)
    airsend/<device.key>/set                                 (commande simple: OPEN/CLOSE/STOP/ON/OFF/PRESS)
    airsend/<device.key>/set_position                        (cover "niveau" uniquement: 0-100)
    airsend/<device.key>/position                            (cover "niveau" uniquement: position courante 0-100)
    airsend/bridge/status                                    (availability, "online"/"offline", LWT)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceTopics:
    state: str
    command: str
    set_position: str
    position: str
    discovery: str

    @staticmethod
    def for_device(component: str, device_key: str) -> "DeviceTopics":
        base = f"airsend/{device_key}"
        return DeviceTopics(
            state=f"{base}/state",
            command=f"{base}/set",
            set_position=f"{base}/set_position",
            position=f"{base}/position",
            discovery=f"homeassistant/{component}/airsend_{device_key}/config",
        )


AVAILABILITY_TOPIC = "airsend/bridge/status"
AVAILABILITY_ONLINE = "online"
AVAILABILITY_OFFLINE = "offline"


def device_info_block(box_slug: str, box_name: str) -> dict:
    """Bloc `device` MQTT discovery commun : regroupe toutes les entites d'une
    meme box AirSend sous un seul appareil cote HA."""
    return {
        "identifiers": [f"airsend_{box_slug}"],
        "name": f"AirSend - {box_name}",
        "manufacturer": "Devmel",
        "model": "AirSend / AirSend Duo",
    }


def base_discovery_payload(device, component: str, topics: DeviceTopics) -> dict:
    """Champs communs a toute config de discovery, a completer par chaque
    domains/*.py avec ses champs specifiques (device_class, position_topic...)."""
    return {
        "name": device.friendly_name,
        "unique_id": f"airsend_{device.key}",
        "state_topic": topics.state,
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": AVAILABILITY_ONLINE,
        "payload_not_available": AVAILABILITY_OFFLINE,
        "device": device_info_block(device.box, device.box),
    }
