import re
from urllib.parse import urlparse

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow, ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.network import get_url, NoURLAvailableError

from . import DOMAIN, utils, CONF_USER_IDS


class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    async def async_step_import(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return await self.async_step_create()

        return self.async_create_entry(title="Yandex Dialogs", data={})

    async def async_step_create(self, user_input=None, desc=''):
        if user_input:
            p = urlparse(user_input['url'])
            if p.scheme != 'https':
                desc = "Обязательно использование HTTPS"
                return await self.async_step_create(desc=desc)

            hass_url = f"{p.scheme}://{p.netloc}"

            entry_id = user_input['account']
            entry = self.hass.config_entries.async_get_entry(entry_id)

            resp = await utils.create_dialog(
                self.hass, user_input['name'], hass_url, entry.data['cookie']
            )
            if 'error' in resp:
                error = resp['error']
                if 'url' in resp:
                    error += f"\n[ссылка на диалог]({resp['url']})"
                return await self.async_step_create(desc=error)

            await self._update_allowed_users(resp['user_id'])

            return self.async_abort(
                reason="success",
                description_placeholders={'url': resp['url']}
            )

        entries = self.hass.config_entries.async_entries('yandex_station')
        if not entries:
            return self.async_abort(reason="yandex_station")

        entries = {
            entry.entry_id: entry.title
            for entry in entries
        }

        try:
            hass_url = get_url(self.hass, require_ssl=True,
                               allow_internal=False)
        except NoURLAvailableError:
            hass_url = None

        return self.async_show_form(
            step_id='create',
            data_schema=vol.Schema({
                vol.Required('account'): vol.In(entries),
                vol.Required('url', default=hass_url): str,
                vol.Required('name'): str
            }),
            description_placeholders={'description': desc}
        )

    async def _update_allowed_users(self, user_id: str):
        entry = self._async_current_entries()[0]

        user_ids = entry.options.get(CONF_USER_IDS, [])
        if user_id in user_ids:
            return

        self.hass.config_entries.async_update_entry(entry, data={}, options={
            CONF_USER_IDS: user_ids + [user_id]
        })
        await self.hass.config_entries.async_reload(entry.entry_id)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        user_ids = self.config_entry.options.get(CONF_USER_IDS)
        user_ids = ','.join(user_ids) if user_ids else ''
        return self.async_show_form(
            step_id='user',
            data_schema=vol.Schema({
                vol.Optional(CONF_USER_IDS, default=user_ids): cv.string,
            })
        )

    async def async_step_user(self, user_input: dict = None):
        user_ids = user_input[CONF_USER_IDS].strip()
        user_ids = re.split(r"\s*,\s*", user_ids)
        return self.async_create_entry(title='', data={
            CONF_USER_IDS: user_ids
        })
