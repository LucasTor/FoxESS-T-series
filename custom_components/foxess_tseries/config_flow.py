from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import socket
from .const import DOMAIN


form_schema = vol.Schema({
    vol.Optional("ip_address", default="192.168.0.129"): str,
    vol.Optional("port", default=502): int,
    vol.Optional("serial_port"): str,
    vol.Optional("payload_version", default=0): int,
})

def ping_server(server: str, port: int, timeout=3):
    """Check the inverter is reachable. Blocking, run in an executor."""
    try:
        s = socket.create_connection((server, port), timeout=timeout)
    except OSError:
        return False
    else:
        s.close()
        return True

class CustomFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if(not user_input):
            return self.async_show_form(
                step_id="user",
                data_schema=form_schema
            )
        
        #TODO: Communication valdation for when using serial port
        reachable = True
        if(user_input.get('ip_address') and user_input.get('port')):
            reachable = await self.hass.async_add_executor_job(
                ping_server, user_input['ip_address'], user_input['port']
            )

        if(not reachable):
            return self.async_show_form(
                step_id="user",
                data_schema=form_schema,
                errors={ 'base': "Unable to reach inverter." }
            )

        return self.async_create_entry(title='FoxESS T Series', data=user_input)