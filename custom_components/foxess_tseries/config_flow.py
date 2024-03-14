from typing import Any
import voluptuous as vol
import json
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import socket
from .const import DOMAIN


form_schema = vol.Schema({
    vol.Optional("ip_address", default="192.168.0.129"): str,
    vol.Optional("port", default=502): int,
    vol.Optional("serial_port"): str
})

def ping_server(server: str, port: int, timeout=3):
    """ping server"""
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((server, port))
    except OSError as e:
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
        if(user_input['ip_address'] and user_input['port'] and not ping_server(user_input['ip_address'], user_input['port'])):
            return self.async_show_form(
                step_id="user",
                data_schema=form_schema,
                errors={ 'base': "Unable to reach inverter." }
            )

        return self.async_create_entry(title='FoxESS T Series', data=user_input)