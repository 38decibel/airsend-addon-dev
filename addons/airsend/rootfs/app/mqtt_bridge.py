"""
Pont MQTT : publie la discovery HA pour chaque device connu, republie les
etats decodes (recus depuis callback_server via on_state), et route les
commandes MQTT entrantes vers AirSendClient.transfer().

Utilise paho-mqtt (API callback classique, pas la variante asyncio - on
l'entoure de call_soon_threadsafe pour rester compatible avec la boucle
asyncio du reste de l'app, paho tournant sur son propre thread reseau interne).
"""

from __future__ import annotations

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

from airsend_client import AirSendClient, AirSendError, BoxConfig
from device_registry import Device, DeviceRegistry
from domains import get_domain_module
from domains.topics import AVAILABILITY_OFFLINE, AVAILABILITY_ONLINE, AVAILABILITY_TOPIC, DeviceTopics
from inclusion import InclusionState

_LOGGER = logging.getLogger("airsend.mqtt_bridge")

# Entite systeme "mode inclusion", independante du device_registry (ce n'est
# pas un appareil AirSend, c'est un mode de fonctionnement de l'addon).
_INCLUSION_COMMAND_TOPIC = "airsend/inclusion/set"
_INCLUSION_STATE_TOPIC = "airsend/inclusion/state"
_INCLUSION_DISCOVERY_TOPIC = "homeassistant/switch/airsend_inclusion_mode/config"
_INCLUSION_CANDIDATES_TOPIC = "airsend/inclusion/candidates"


class MqttBridge:
    def __init__(
        self,
        registry: DeviceRegistry,
        client: AirSendClient,
        boxes_by_slug: dict[str, BoxConfig],
        inclusion: InclusionState,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
    ) -> None:
        self._registry = registry
        self._client = client
        self._boxes_by_slug = boxes_by_slug
        self._inclusion = inclusion
        self._loop = asyncio.get_event_loop()
        self._candidates_task: asyncio.Task | None = None

        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="airsend-addon")
        if username:
            self._mqtt.username_pw_set(username, password)
        if use_ssl:
            self._mqtt.tls_set()
        self._mqtt.will_set(AVAILABILITY_TOPIC, AVAILABILITY_OFFLINE, retain=True)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message

        self._host = host
        self._port = port

    # ------------------------------------------------------------------ #
    # Connexion
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._mqtt.connect_async(self._host, self._port)
        self._mqtt.loop_start()
        self._candidates_task = asyncio.create_task(self._candidates_publisher_loop())

    async def stop(self) -> None:
        if self._candidates_task is not None:
            self._candidates_task.cancel()
        self._mqtt.publish(AVAILABILITY_TOPIC, AVAILABILITY_OFFLINE, retain=True)
        self._mqtt.loop_stop()
        self._mqtt.disconnect()

    async def _candidates_publisher_loop(self) -> None:
        """Republie la liste des candidats d'inclusion en continu pendant que
        le mode est actif. Implementation volontairement simple (polling),
        suffisante tant que la fenetre d'inclusion reste courte (quelques
        minutes) - a event-driver si besoin plus tard."""
        while True:
            if self._inclusion.active:
                candidates = [
                    {
                        "box": c.box,
                        "channel_id": c.channel_id,
                        "channel_source": c.channel_source,
                        "protocol_name": c.protocol_name,
                        "suggested_kind": c.suggested_kind,
                    }
                    for c in self._inclusion.list_candidates()
                ]
                self._mqtt.publish(_INCLUSION_CANDIDATES_TOPIC, json.dumps(candidates), retain=False)
            await asyncio.sleep(2.0)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        _LOGGER.info("MQTT connected (reason_code=%s)", reason_code)
        client.publish(AVAILABILITY_TOPIC, AVAILABILITY_ONLINE, retain=True)
        client.subscribe("airsend/+/set")
        client.subscribe("airsend/+/set_position")
        client.subscribe(_INCLUSION_COMMAND_TOPIC)
        # Republie la discovery + le dernier etat connu de tous les devices a
        # chaque (re)connexion : couvre le cas d'un broker/HA redemarre.
        for device in self._registry.all():
            self.publish_discovery(device)
        self._publish_inclusion_discovery()
        self._publish_inclusion_state()

    # ------------------------------------------------------------------ #
    # Entite systeme : mode inclusion
    # ------------------------------------------------------------------ #

    def _publish_inclusion_discovery(self) -> None:
        config = {
            "name": "AirSend - Mode inclusion",
            "unique_id": "airsend_inclusion_mode",
            "command_topic": _INCLUSION_COMMAND_TOPIC,
            "state_topic": _INCLUSION_STATE_TOPIC,
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": AVAILABILITY_ONLINE,
            "payload_not_available": AVAILABILITY_OFFLINE,
            "device": {
                "identifiers": ["airsend_addon"],
                "name": "AirSend Addon",
                "manufacturer": "Devmel",
            },
        }
        self._mqtt.publish(_INCLUSION_DISCOVERY_TOPIC, json.dumps(config), retain=True)

    def _publish_inclusion_state(self) -> None:
        self._mqtt.publish(
            _INCLUSION_STATE_TOPIC,
            "ON" if self._inclusion.active else "OFF",
            retain=True,
        )

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #

    def publish_discovery(self, device: Device) -> None:
        module = get_domain_module(device.domain)
        if module is None:
            _LOGGER.warning("Unknown domain '%s' for device %s, skipping discovery", device.domain, device.key)
            return
        topics = DeviceTopics.for_device(module.COMPONENT, device.key)
        config = module.discovery_config(device, topics)
        self._mqtt.publish(topics.discovery, json.dumps(config), retain=True)
        _LOGGER.info("Published discovery for %s (%s) on %s", device.key, device.domain, topics.discovery)

    def remove_discovery(self, device: Device) -> None:
        module = get_domain_module(device.domain)
        if module is None:
            return
        topics = DeviceTopics.for_device(module.COMPONENT, device.key)
        self._mqtt.publish(topics.discovery, "", retain=True)  # payload vide = suppression cote HA

    # ------------------------------------------------------------------ #
    # Etat sortant (RF -> MQTT)
    # ------------------------------------------------------------------ #

    async def publish_state(self, device_key: str, stype: str, svalue: object, channel: dict) -> None:
        device = self._registry.get(device_key)
        if device is None:
            _LOGGER.warning("publish_state called for unknown device_key=%s", device_key)
            return

        module = get_domain_module(device.domain)
        if module is None:
            return

        for topic, payload in module.encode_state(device, stype, svalue):
            self._mqtt.publish(topic, payload, retain=True)
            _LOGGER.debug("Published state %s = %s", topic, payload)

    # ------------------------------------------------------------------ #
    # Commande entrante (MQTT -> RF)
    # ------------------------------------------------------------------ #

    def _on_message(self, client, userdata, msg) -> None:
        # Callback paho = thread reseau interne, on repasse sur la boucle
        # asyncio pour pouvoir faire l'appel HTTP vers AirSendWebService.
        asyncio.run_coroutine_threadsafe(self._handle_command(msg.topic, msg.payload.decode()), self._loop)

    async def _handle_command(self, topic: str, payload: str) -> None:
        if topic == _INCLUSION_COMMAND_TOPIC:
            if payload.upper() == "ON":
                self._inclusion.start()
            else:
                self._inclusion.stop()
            self._publish_inclusion_state()
            return

        # topic attendu: airsend/<device_key>/set ou /set_position
        parts = topic.split("/")
        if len(parts) < 3:
            return
        device_key = parts[1]
        device = self._registry.get(device_key)
        if device is None:
            _LOGGER.warning("Command on unknown device_key=%s (topic=%s)", device_key, topic)
            return

        module = get_domain_module(device.domain)
        if module is None:
            return

        thingnotes = module.decode_command(device, topic, payload)
        if thingnotes is None:
            _LOGGER.debug("Command payload %r on %s not understood by domain %s", payload, topic, device.domain)
            return

        box = self._boxes_by_slug.get(device.box)
        if box is None:
            _LOGGER.error("Command for device %s references unknown box '%s'", device.key, device.box)
            return

        channel = {"id": device.channel_id, "source": device.channel_source}
        try:
            await self._client.transfer(box, channel=channel, thingnotes=thingnotes, wait=True)
        except AirSendError as exc:
            _LOGGER.warning("Failed to send command for device %s: %s", device.key, exc)
