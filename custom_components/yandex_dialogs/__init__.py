import asyncio
import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import Event
from homeassistant.helpers import intent
from homeassistant.helpers.typing import HomeAssistantType

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'yandex_dialogs'

CONF_USER_IDS = 'user_ids'


async def async_setup(hass: HomeAssistantType, hass_config: dict):
    if DOMAIN in hass_config and not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}
        ))
    return True


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry):
    user_ids = entry.options.get(CONF_USER_IDS, [])

    if DOMAIN in hass.data:
        dialog: YandexDialog = hass.data[DOMAIN]
        dialog.user_ids = user_ids
        return True

    hass.data[DOMAIN] = dialog = YandexDialog(hass, user_ids)
    hass.http.register_view(dialog)

    async def listener(event: Event):
        dialog.response = event.data
        dialog.response_waiter.set()

    hass.bus.async_listen('yandex_intent_response', listener)

    # add options handler
    if not entry.update_listeners:
        entry.add_update_listener(async_update_options)

    return True


async def async_update_options(hass: HomeAssistantType, entry):
    await hass.config_entries.async_reload(entry.entry_id)


# noinspection PyUnusedLocal
async def async_unload_entry(hass: HomeAssistantType, entry):
    return True


class YandexDialog(HomeAssistantView):
    url = '/api/yandex_dialogs'
    name = 'api:yandex_dialogs'
    requires_auth = False

    dialogs: dict = {}
    response: dict = {}
    response_waiter = asyncio.Event()

    def __init__(self, hass: HomeAssistantType, user_ids: list):
        self.hass = hass
        self.user_ids = user_ids

    @staticmethod
    def empty(text=""):
        return web.json_response({
            "response": {"text": text, "end_session": True},
            "version": "1.0"
        })

    @staticmethod
    def web_response(text="", end_session=True, **kwargs):
        data = {
            "response": {"text": text, "end_session": end_session},
            "version": "1.0"
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

    async def post(self, request: web.Request) -> web.Response:
        data = None

        try:
            data = await request.json()

            _LOGGER.debug(data)

            request = data["request"]
            command: str = request["command"]
            if command == "ping":
                return self.empty(text="pong")

            user_id = data["session"]["user"]["user_id"]
            if user_id not in self.user_ids:
                _LOGGER.debug("Unknown user: " + user_id)
                return self.empty()

            # sometimes we not exit from skill and receive new request
            if request["original_utterance"].startswith("СКАЖИ НАВЫКУ"):
                command = request["nlu"]["tokens"][-1]

            if command in self.dialogs:
                response = self.dialogs.pop(command)
                return self.web_response(**response)

            event_data = {
                'text': request['original_utterance'],
                'command': request['command'],
            }

            if "state" in data:
                event_data.update(data["state"])

            intents = request['nlu'].get('intents')
            if intents:
                event_data['intent'] = intent_type = next(iter(intents))
                for k, v in intents[intent_type]['slots'].items():
                    event_data[k] = v['value']

            else:
                intent_type = 'yandex_default'

            _LOGGER.debug(f"Request: {event_data}")

            self.response.clear()

            # by default won't exit dialog on empty input
            if data["session"]["new"] and request["command"] == "":
                self.response["end_session"] = False

            try:
                if intent_type in self.hass.data.get('intent', ""):
                    # run intent if exists
                    slots = {k: {'value': v} for k, v in event_data.items()}
                    resp = await intent.async_handle(
                        self.hass, DOMAIN, intent_type, slots,
                        request['original_utterance']
                    )
                    if resp.speech:
                        self.response["text"] = resp.speech['plain']['speech']

                else:
                    self.response_waiter.clear()
                    self.hass.bus.async_fire('yandex_intent', event_data)
                    await asyncio.wait_for(self.response_waiter.wait(), 2.0)

            except:
                pass

            return self.web_response(**self.response)

        except:
            _LOGGER.exception(f"Yandex Dialog {data}")
            return self.empty()
