import asyncio
import logging
from typing import Callable

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent
from homeassistant.requirements import async_process_requirements

_LOGGER = logging.getLogger(__name__)

DOMAIN = "yandex_dialogs"

CONF_USER_IDS = "user_ids"
CONF_FILE = "file"
CONF_SOURCE = "source"
CONF_CACHE = "cache"
CONF_REQUIREMENTS = "requirements"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_FILE): cv.string,
                vol.Optional(CONF_SOURCE): cv.string,
                vol.Optional(CONF_CACHE): cv.boolean,
                vol.Optional(CONF_REQUIREMENTS): cv.ensure_list,
            },
            extra=vol.ALLOW_EXTRA,
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, hass_config: dict):
    # basic setup
    hass.data[DOMAIN] = dialog = YandexDialog()
    hass.http.register_view(dialog)

    # response on event setup
    async def listener(event: Event):
        dialog.response = event.data
        dialog.response_waiter.set()

    hass.bus.async_listen("yandex_intent_response", listener)

    # config setup
    config: dict = hass_config.get(DOMAIN)
    if config is None:
        return True

    # config entry from configuration.yaml
    if hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}
            )
        )

    if req := config.get(CONF_REQUIREMENTS):
        hass.async_create_task(async_process_requirements(hass, DOMAIN, req))

    if file := config.get(CONF_FILE):
        file = hass.config.path(file)
        dialog.handler = (
            file_handler(file) if config.get(CONF_CACHE) else nocache_handler(file)
        )
    elif source := config.get(CONF_SOURCE):
        dialog.handler = source_handler(source)

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    dialog: YandexDialog = hass.data[DOMAIN]
    dialog.user_ids = config_entry.options.get(CONF_USER_IDS)

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    return True


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    await hass.config_entries.async_reload(config_entry.entry_id)


# noinspection PyUnusedLocal
async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    return True


class YandexDialog(HomeAssistantView):
    url = "/api/yandex_dialogs"
    name = "api:yandex_dialogs"
    requires_auth = False

    user_ids: list = None
    dialogs: dict = {}

    handler: Callable = None

    response: dict = {}
    response_waiter = asyncio.Event()

    async def post(self, request: web.Request) -> web.Response:
        event: dict = None

        try:
            event = await request.json()

            _LOGGER.debug(event)

            command: str = event["request"]["command"]

            # 1. ping/pong from Yandex servers
            if command == "ping":
                return empty_response(text="pong")

            # 2. check allowed user (only if users not null)
            if self.user_ids:
                user_id = event["session"]["user"]["user_id"]
                if user_id not in self.user_ids:
                    _LOGGER.debug("Unknown user: " + user_id)
                    return empty_response()

            # sometimes we not exit from skill and receive new request
            if event["request"]["original_utterance"].startswith("СКАЖИ НАВЫКУ"):
                command = event["request"]["nlu"]["tokens"][-1]

            # 3. integration with yandex station
            if command in self.dialogs:
                data = self.dialogs.pop(command)
                return web_response(**data)

            hass: HomeAssistant = request.app["hass"]

            if self.handler:
                data = await hass.async_add_executor_job(
                    self.handler, event, {"hass": hass, "logger": _LOGGER}
                )
                return web.json_response(data)

            event_data = {
                "text": event["request"]["original_utterance"],
                "command": event["request"]["command"],
            }

            if state := event.get("state"):
                # skip empty state because compare any with empty is True
                for k in ("session", "user", "application"):
                    if state[k]:
                        event_data[k] = state[k]

            if intents := event["request"].get("nlu", {}).get("intents"):
                event_data["intent"] = intent_type = next(iter(intents))
                for k, v in intents[intent_type]["slots"].items():
                    event_data[k] = v["value"]

            else:
                intent_type = "yandex_default"

            _LOGGER.debug(f"Request: {event_data}")

            self.response.clear()

            # by default won't exit dialog on empty input
            if event["session"]["new"] and command == "":
                self.response["end_session"] = False

            try:
                if intent_type in hass.data.get("intent", ""):
                    # run intent if exists
                    slots = {k: {"value": v} for k, v in event_data.items()}
                    resp = await intent.async_handle(
                        hass,
                        DOMAIN,
                        intent_type,
                        slots,
                        event["request"]["original_utterance"],
                    )
                    if resp.speech:
                        self.response["text"] = resp.speech["plain"]["speech"]

                else:
                    self.response_waiter.clear()
                    hass.bus.async_fire("yandex_intent", event_data)
                    await asyncio.wait_for(self.response_waiter.wait(), 4.0)

            except:
                pass

            return web_response(**self.response)

        except:
            _LOGGER.exception(f"Yandex Dialog {event}")
            return empty_response()


def empty_response(text=""):
    return web.json_response(
        {"response": {"text": text, "end_session": True}, "version": "1.0"}
    )


def web_response(text="", end_session=True, **kwargs):
    data = {
        "response": {"text": text, "end_session": end_session},
        "version": "1.0",
    }

    if "tts" in kwargs:
        # text should be not empty of tts won't work
        if not text:
            data["response"]["text"] = "-"
        data["response"]["tts"] = kwargs["tts"]

    if "session" in kwargs:
        data["session_state"] = kwargs["session"]
    if "user" in kwargs:
        data["user_state_update"] = kwargs["user"]
    if "application" in kwargs:
        data["application_state"] = kwargs["application"]

    _LOGGER.debug(data)

    return web.json_response(data)


def exec_handler(source: str) -> Callable | None:
    try:
        exec(source)
        return locals()["handler"]
    except Exception as e:
        _LOGGER.exception(f"Error executing script: {e}")
        return None


def source_handler(source: str) -> Callable:
    exec(source)
    globals().update(locals())
    return locals()["handler"]


def file_handler(file: str) -> Callable:
    with open(file) as f:
        return source_handler(f.read())


def nocache_handler(file: str) -> Callable:
    def nocache(event: dict, context: dict) -> dict:
        f = file_handler(file)
        return f(event, context)

    return nocache
