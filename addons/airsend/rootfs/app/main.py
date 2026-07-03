"""
Point d'entree de l'addon.

Chaine complete : ecoute RF (bind_manager) -> callback (callback_server,
filtre reliability) -> decodage (thing_notes) -> etat/discovery/commandes
(mqtt_bridge) -> Home Assistant via MQTT discovery. Le mode inclusion est
pilotable depuis HA via l'entite switch "AirSend - Mode inclusion" (plus de
force-start de dev), la confirmation des candidats detectes (nom/kind/options
-> ecriture dans devices.json) reste en revanche a construire : pour l'instant
les candidats sont uniquement publies en lecture sur `airsend/inclusion/candidates`
(JSON), consultables mais pas encore actionnables depuis HA. Prochaine etape
naturelle plutot que de bricoler ca en MQTT pur : exposer un service HA cote
addon (Supervisor API / webhook) pour ce formulaire de confirmation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from airsend_client import AirSendClient, BoxConfig
from bind_manager import BindManager
from callback_server import CallbackServer
from device_registry import DeviceRegistry
from inclusion import InclusionState
from mqtt_bridge import MqttBridge
from protocol_catalog import ProtocolCatalog

_LOGGER = logging.getLogger("airsend.main")

CALLBACK_PORT = 8126
# AirSendWebService tourne dans le MEME conteneur que cette app Python (cf.
# Dockerfile/run.sh), donc 127.0.0.1 est toujours la bonne cible pour le
# callback - pas besoin (et pas souhaitable) de deviner une IP via une
# connexion sortante vers un serveur externe (8.8.8.8).
CALLBACK_HOST = "127.0.0.1"


def _load_boxes() -> list[BoxConfig]:
    raw = os.environ.get("BOXES_JSON", "[]")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        _LOGGER.error("BOXES_JSON is not valid JSON, no box will be configured: %r", raw)
        return []

    boxes: list[BoxConfig] = []
    for entry in entries:
        try:
            boxes.append(
                BoxConfig(
                    name=entry["name"],
                    localip=entry["localip"],
                    ipv6=entry["ipv6"],
                    password=entry["password"],
                    gw=bool(entry.get("gw", False)),
                )
            )
        except KeyError as exc:
            _LOGGER.error("Skipping malformed box entry %r (missing %s)", entry, exc)
    return boxes


async def async_main() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    boxes = _load_boxes()
    if not boxes:
        _LOGGER.error("No AirSend box configured, nothing to do. Check addon configuration.")
        # On ne quitte pas immediatement : ca permettrait a l'utilisateur de
        # voir l'erreur dans les logs plutot qu'un crash-loop silencieux.
        while True:
            await asyncio.sleep(3600)

    client = AirSendClient()
    await client.start()

    catalog = ProtocolCatalog(client)
    registry = DeviceRegistry()
    inclusion = InclusionState()

    for box in boxes:
        await catalog.refresh(box)
        is_duo = catalog.is_duo_best_effort(box.slug)
        _LOGGER.info(
            "Box '%s' detection (best effort): %s",
            box.name,
            "AirSend Duo (433+868MHz)" if is_duo else "AirSend (433MHz)" if is_duo is False else "unknown",
        )

    boxes_by_slug = {box.slug: box for box in boxes}

    mqtt_bridge = MqttBridge(
        registry=registry,
        client=client,
        boxes_by_slug=boxes_by_slug,
        inclusion=inclusion,
        host=os.environ.get("MQTT_HOST", "core-mosquitto"),
        port=int(os.environ.get("MQTT_PORT") or 1883),
        username=os.environ.get("MQTT_USER") or None,
        password=os.environ.get("MQTT_PASS") or None,
        use_ssl=os.environ.get("MQTT_SSL", "false").lower() == "true",
    )
    await mqtt_bridge.start()

    callback_server = CallbackServer(
        registry=registry,
        inclusion=inclusion,
        catalog=catalog,
        on_state=mqtt_bridge.publish_state,
        port=CALLBACK_PORT,
    )
    await callback_server.start()

    callback_base_url = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}"
    _LOGGER.info("Callback base URL: %s", callback_base_url)

    bind_manager = BindManager(client, callback_base_url)
    for box in boxes:
        bind_manager.add_box(box)

    _LOGGER.info(
        "Ready. Toggle 'AirSend - Mode inclusion' in HA to start detecting new devices."
    )

    try:
        await asyncio.Event().wait()  # tourne indefiniment
    finally:
        await bind_manager.stop_all()
        await callback_server.stop()
        await mqtt_bridge.stop()
        await client.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
