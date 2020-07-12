import base64
import json
import logging
import pickle
import re
from os import path

from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import HomeAssistantType

_LOGGER = logging.getLogger(__name__)

INDEX = 'https://dialogs.yandex.ru/developer'


async def create_dialog(hass: HomeAssistantType, name: str):
    try:
        if not hass.config.external_url:
            _LOGGER.error("Empty Home Assistant External URL")
            return

        cachefile = hass.config.path(f".yandex_station.json")

        if not path.isfile(cachefile):
            _LOGGER.error("Empty Yandex Login Data")
            return

        with open(cachefile, 'rt') as f:
            raw = json.load(f)

        raw = base64.b64decode(raw['cookie'])

        session = async_create_clientsession(hass)
        session.cookie_jar._cookies = pickle.loads(raw)

        r = await session.get(INDEX)
        assert r.status == 200, "Get Index"
        data = await r.text()
        m = re.search(r'"secretkey":"(.+?)"', data)
        headers = {'x-csrf-token': m[1]}

        r = await session.get(f"{INDEX}/api/snapshot", headers=headers)
        assert r.status == 200, "Get Skills"
        data = await r.json()
        for skill in data['result']['skills']:
            if skill['name'] == name:
                url = f"{INDEX}/skills/{skill['id']}"
                hass.components.persistent_notification.async_create(
                    f"[Ссылка]({url}) на диалог",
                    title="Yandex Dialogs")
                return

        r = await session.post(f"{INDEX}/api/skills", headers=headers,
                               json={'channel': 'aliceSkill'})
        assert r.status == 201, "Create Skill"
        data = await r.json()
        skill_id = data['result']['id']

        filename = path.join(path.dirname(path.abspath(__file__)), 'logo.png')
        r = await session.post(
            f"{INDEX}/api/skills/{skill_id}/logo", headers=headers,
            data={'file': open(filename, 'rb')})
        assert r.status == 201, "Upload Logo"
        data = await r.json()
        logo_id = data['result']['id']

        hass_uri = f"{hass.config.external_url}api/yandex_dialogs"
        r = await session.patch(
            f"{INDEX}/api/skills/{skill_id}/draft",
            headers=headers, json={
                "activationPhrases": [
                    name
                ],
                "appMetricaApiKey": "",
                "backendSettings": {
                    "backendType": "webhook",
                    "functionId": "",
                    "uri": hass_uri
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
                    "structuredExamples": [
                        {
                            "activationPhrase": name,
                            "marker": "запусти навык",
                            "request": ""
                        }
                    ]
                },
                "requiredInterfaces": [],
                "rsyPlatformId": "",
                "skillAccess": "private",
                "surfaceBlacklist": [],
                "surfaceWhitelist": [],
                "useStateStorage": False,
                "voice": "shitova.us",
                "yaCloudGrant": False
            })
        assert r.status == 200, "Patch Draft"

        r = await session.post(f"{INDEX}/api/skills/{skill_id}/release",
                               headers=headers)
        assert r.status == 201, "Release Skill"

        url = f"{INDEX}/skills/{skill_id}"
        hass.components.persistent_notification.async_create(
            f"Диалог успешно создан: [ссылка]({url})",
            title="Yandex Dialogs")

    except Exception:
        _LOGGER.exception("Create Skill")
