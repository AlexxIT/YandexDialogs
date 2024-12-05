import base64
import logging
import pickle
import re
from os import path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

_LOGGER = logging.getLogger(__name__)

INDEX = "https://dialogs.yandex.ru/developer"


async def create_dialog(
    hass: HomeAssistant, name: str, hass_url: str, cookies: str
) -> (str, str):
    try:
        raw = base64.b64decode(cookies)

        session = async_create_clientsession(hass)
        session.cookie_jar._cookies = pickle.loads(raw)

        # check if skill exists
        r = await session.get(INDEX)
        if r.status != 200:
            return {"error": "Ошибка при проверке авторизации:\n" + await r.text()}

        data = await r.text()
        m = re.search(r'"secretkey":"(.+?)"', data)
        headers = {"x-csrf-token": m[1]}

        r = await session.get(f"{INDEX}/api/snapshot", headers=headers)
        if r.status != 200:
            return {"error": "Ошибка при чтении списка навыков:\n" + await r.text()}

        data = await r.json()
        for skill in data["result"]["skills"]:
            if skill["name"] == name or skill["draft"]["name"] == name:
                url = f"{INDEX}/skills/{skill['id']}"
                _LOGGER.debug(f"Навык уже существует: {url}")
                return {"error": "Навык уже существует", "url": url}

        # create new skill
        r = await session.post(
            f"{INDEX}/api/skills", headers=headers, json={"channel": "aliceSkill"}
        )
        if r.status != 201:
            return {"error": "Ошибка при создании навыка:\n" + await r.text()}

        data = await r.json()
        skill_id = data["result"]["id"]
        skill_url = f"{INDEX}/skills/{data['result']['id']}"

        filename = path.join(path.dirname(path.abspath(__file__)), "logo.png")
        r = await session.post(
            f"{INDEX}/api/skills/{skill_id}/logo",
            headers=headers,
            data={"file": open(filename, "rb")},
        )
        if r.status != 201:
            return {
                "error": "Ошибка при добавлении иконки:\n" + await r.text(),
                "url": skill_url,
            }

        data = await r.json()
        logo_id = data["result"]["id"]

        payload = {
            "activationPhrases": [name],
            "backendSettings": {
                "backendType": "webhook",
                "uri": hass_url + "/api/yandex_dialogs",
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
                    {"activationPhrase": name, "marker": "запусти навык", "request": ""}
                ],
            },
            "requiredInterfaces": [],
            "rsyPlatformId": "",
            "skillAccess": "private",
            "surfaceBlacklist": [],
            "surfaceWhitelist": [],
            "useStateStorage": True,
            "voice": "shitova.us",
            "yaCloudGrant": False,
        }
        r = await session.patch(
            f"{INDEX}/api/skills/{skill_id}/draft", headers=headers, json=payload
        )
        if r.status != 200:
            return {
                "error": "Ошибка при настроке навыка:\n" + await r.text(),
                "url": skill_url,
            }

        # check if webhook works
        payload = {
            "text": "",
            "isDraft": True,
            "sessionId": "",
            "sessionSeq": 0,
            "surface": "mobile",
            "isAnonymousUser": False,
        }
        r = await session.post(
            f"{INDEX}/api/skills/{skill_id}/message", headers=headers, json=payload
        )
        if r.status != 201:
            return {
                "error": "Ошибка получения статуса:\n" + await r.text(),
                "url": skill_url,
            }

        data = await r.json()
        error = data["result"].get("error")
        if error:
            _LOGGER.debug(f"Ошибка при создании навыка: {error}")
            return {
                "error": "Ошибка при проверке навыка:\n" + error,
                "url": skill_url,
            }

        user_id = data["result"]["history"]["request"]["session"]["user"]["user_id"]

        # publish skill
        r = await session.post(
            f"{INDEX}/api/skills/{skill_id}/release", headers=headers
        )
        if r.status != 201:
            return {
                "error": "Ошибка публикации навыка:\n" + await r.text(),
                "url": skill_url,
            }

        _LOGGER.debug("Навык успешно создан")
        return {"url": skill_url, "user_id": user_id}

    except Exception as e:
        _LOGGER.exception("Create Skill")
        return {"error": repr(e)}
