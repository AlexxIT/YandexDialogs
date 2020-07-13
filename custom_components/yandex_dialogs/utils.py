import base64
import json
import logging
import pickle
import re
from os import path

from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.network import get_url, NoURLAvailableError
from homeassistant.helpers.typing import HomeAssistantType

_LOGGER = logging.getLogger(__name__)

INDEX = 'https://dialogs.yandex.ru/developer'


async def create_dialog(hass: HomeAssistantType, name: str):
    # check external HTTPS URL
    try:
        hass_url = get_url(hass, require_ssl=True, allow_internal=False)
        _LOGGER.debug(f"External hass URL: {hass_url}")
    except NoURLAvailableError:
        _LOGGER.error("Can't get external HTTPS URL")
        return

    try:
        cachefile = hass.config.path(f".yandex_station.json")

        if not path.isfile(cachefile):
            _LOGGER.error("Empty Yandex Login Data")
            return

        with open(cachefile, 'rt') as f:
            raw = json.load(f)

        raw = base64.b64decode(raw['cookie'])

        session = async_create_clientsession(hass)
        session.cookie_jar._cookies = pickle.loads(raw)

        # check if skill exists
        r = await session.get(INDEX)
        assert r.status == 200, await r.read()
        data = await r.text()
        m = re.search(r'"secretkey":"(.+?)"', data)
        headers = {'x-csrf-token': m[1]}

        r = await session.get(f"{INDEX}/api/snapshot", headers=headers)
        assert r.status == 200, await r.read()
        data = await r.json()
        for skill in data['result']['skills']:
            if skill['name'] == name or skill['draft']['name'] == name:
                url = f"{INDEX}/skills/{skill['id']}"
                _LOGGER.debug(f"Skill alreay exists: {url}")
                return

        # create new skill
        r = await session.post(f"{INDEX}/api/skills", headers=headers,
                               json={'channel': 'aliceSkill'})
        assert r.status == 201, await r.read()
        data = await r.json()
        skill_id = data['result']['id']
        skill_url = f"{INDEX}/skills/{data['result']['id']}"

        filename = path.join(path.dirname(path.abspath(__file__)), 'logo.png')
        r = await session.post(
            f"{INDEX}/api/skills/{skill_id}/logo", headers=headers,
            data={'file': open(filename, 'rb')})
        assert r.status == 201, await r.read()
        data = await r.json()
        logo_id = data['result']['id']

        payload = {
            "activationPhrases": [name],
            "appMetricaApiKey": "",
            "backendSettings": {
                "backendType": "webhook",
                "functionId": "",
                "uri": hass_url + '/api/yandex_dialogs'
            },
            "exactSurfaces": [],
            "hideInStore": False,
            "logo2": None,
            "logoId": logo_id,
            "name": name,
            "noteForModerator": "",
            "oauthAppId": None,
            "publishingSettings": {
                "brandVerificationWebsite": "",
                "category": "utilities",
                "description": "Home Assistant",
                "developerName": "Home Assistant",
                "email": "",
                "explicitContent": None,
                "structuredExamples": [{
                    "activationPhrase": name,
                    "marker": "запусти навык",
                    "request": ""
                }]
            },
            "requiredInterfaces": [],
            "rsyPlatformId": "",
            "skillAccess": "private",
            "surfaceBlacklist": [],
            "surfaceWhitelist": [],
            "useStateStorage": False,
            "voice": "shitova.us",
            "yaCloudGrant": False
        }
        r = await session.patch(f"{INDEX}/api/skills/{skill_id}/draft",
                                headers=headers, json=payload)
        assert r.status == 200, await r.read()

        # check if webhook works
        payload = {"text": "", "isDraft": True, "sessionId": "",
                   "sessionSeq": 0, "surface": "mobile",
                   "isAnonymousUser": False}
        r = await session.post(f"{INDEX}/api/skills/{skill_id}/message",
                               headers=headers, json=payload)
        assert r.status == 201, await r.read()
        data = await r.json()
        error = data['result'].get('error')
        if error:
            _LOGGER.debug(f"Ошибка при создании навыка: {error}")
            hass.components.persistent_notification.async_create(
                f"При создании навыка: [ссыка]({skill_url})\n"
                f"возникла ошибка: `{error}`\n"
                f"Проверьте внешний доступ: {hass_url}",
                title="Yandex Dialogs")
            return

        # publish skill
        r = await session.post(f"{INDEX}/api/skills/{skill_id}/release",
                               headers=headers)
        assert r.status == 201, await r.read()

        _LOGGER.debug("Навык успешно создан")
        hass.components.persistent_notification.async_create(
            f"Навык успешно создан: [ссылка]({skill_url})",
            title="Yandex Dialogs")

    except Exception:
        _LOGGER.exception("Create Skill")
