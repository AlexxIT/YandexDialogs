import asyncio
import logging

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONF_NAME
from homeassistant.core import Event
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.helpers.typing import HomeAssistantType

from . import utils

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'yandex_dialogs'

CONF_USER_IDS = 'allowed_user_ids'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_USER_IDS, default=[]): cv.ensure_list,
    }, extra=vol.ALLOW_EXTRA),
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistantType, hass_config: dict):
    config = hass_config[DOMAIN]

    dialog = YandexDialog(hass, config[CONF_USER_IDS])
    hass.http.register_view(dialog)

    async def listener(event: Event):
        dialog.response_text = event.data.get('text')
        dialog.response_end_session = event.data.get('end_session')
        dialog.response_waiter.set()

    hass.bus.async_listen('yandex_intent_response', listener)

    if 'name' in config:
        coro = utils.create_dialog(hass, config['name'])
        asyncio.create_task(coro)

    return True


class YandexDialog(HomeAssistantView):
    url = '/api/yandex_dialogs'
    name = 'api:yandex_dialogs'
    requires_auth = False

    response_text = None
    response_end_session = None
    response_waiter = asyncio.Event()

    def __init__(self, hass: HomeAssistantType, user_ids: list):
        self.hass = hass
        self.allowed_user_ids = user_ids

    @staticmethod
    def empty(text='', end_session=False):
        return web.json_response({
            'response': {'text': text, 'end_session': end_session},
            'version': '1.0'
        })

    async def post(self, request: web.Request) -> web.Response:
        data = None

        try:
            data = await request.json()

            _LOGGER.debug(data)

            request = data['request']
            if request['command'] == 'ping':
                return self.empty(text='pong')

            if 'user' not in data['session']:
                return self.empty()

            user_id = data['session']['user']['user_id']
            if user_id not in self.allowed_user_ids:
                if request['command'] == 'привет':
                    self.hass.components.persistent_notification.async_create(
                        f"Новый пользователь: {user_id}",
                        title="Yandex Dialogs")
                    return self.empty(text="Умный дом на связи")

                else:
                    return self.empty(text="Я тебя не знаю")

            slots = {
                'text': request['original_utterance'],
                'command': request['command'],
            }

            intents = data['request']['nlu'].get('intents')
            if intents:
                slots['intent'] = intent_type = next(iter(intents))
                for k, v in intents[intent_type]['slots'].items():
                    slots[k] = v['value']

            else:
                intent_type = 'yandex_default'

            _LOGGER.debug(f"Request: {slots}")

            self.response_text = None
            self.response_end_session = None

            try:
                if intent_type in self.hass.data.get('intent', {}):
                    # run intent if exists
                    slots = {k: {'value': v} for k, v in slots.items()}
                    response = await intent.async_handle(
                        self.hass, DOMAIN, intent_type, slots,
                        request['original_utterance'])
                    if self.response_text:
                        text = self.response_text
                    elif response.speech:
                        text = response.speech['plain']['speech']
                    else:
                        text = ''

                else:
                    self.response_waiter.clear()
                    self.hass.bus.async_fire('yandex_intent', slots)
                    await asyncio.wait_for(self.response_waiter.wait(), 2.0)
                    text = self.response_text

            except:
                text = ''

            if self.response_end_session is not None:
                end_session = self.response_end_session
            else:
                end_session = (data['session']['new'] and
                               request['command'] != '')

            _LOGGER.debug(f"Response: {text}, end_session: {end_session}")

            return web.json_response({
                'response': {
                    'text': text,
                    'end_session': end_session
                },
                'version': '1.0'
            })

        except:
            _LOGGER.exception(f"Yandex Dialog {data}")
            return self.empty(end_session=True)
