# FoxESS T Series for Home Assistant

## About

This integration was mainly created to solve the issue for the SIW200G and SIW400G WEG inverters, really common in Brazil, which are made by FoxESS, being the T series.

The integration supports three ways of talking to the inverter:

- RS485 TCP bridges, such as the Elfin EW11 (see [the EW11 settings](#connecting-through-an-elfin-ew11-rs485-to-wi-fitcp-bridge))
- RS485 USB Adapters (the one we tested and know it's working is the WaveShare RS485 to USB (B))
- [ESPHome serial proxies](https://esphome.io/components/serial_proxy/): an ESP board with an RS485 transceiver, exposed to Home Assistant over the network by the ESPHome integration

To connect your inverter to any adapter (TCP, USB or ESP), on the newer models, please refer to [this guide](https://github.com/LucasTor/FoxESS-T-series/issues/2#issuecomment-2088445998), for older models, a RS485 comm port should be found on one of the connectors on the inverter.

Home Assistant 2026.5 or newer is required (it introduced the serial port selector and the `serialx` driver used for USB adapters and ESPHome proxies).

## Installation
[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=foxess_tseries)
[![Add Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=LucasTor&repository=FoxESS-T-series&category=integration)

### HACS Installation (Recommended)

1. Make sure you have [HACS](https://hacs.xyz/) installed
2. Add this repository as a custom repository in HACS:
   - Go to HACS > Integrations
   - Click on the three dots in the top right corner
   - Select "Custom repositories"
   - Add the URL of this repository
   - Select "Integration" as the category
3. Click "Install" on the FoxEss-T-series integration

### Manual Installation

1. Copy the `custom_components/foxess_tseries` directory to your Home Assistant `custom_components` directory
2. Restart Home Assistant


## Connecting through an ESPHome serial proxy

1. Flash an ESPHome device with a UART wired to an RS485 transceiver and add the `serial_proxy` component:

   ```yaml
   uart:
     - id: inverter_uart
       tx_pin: GPIO17
       rx_pin: GPIO16
       baud_rate: 9600

   api:

   serial_proxy:
     - id: inverter_serial
       uart_id: inverter_uart
       name: FoxESS Inverter
       port_type: RS485
   ```

2. Add the ESPHome device to Home Assistant (Settings > Devices & services > ESPHome) and wait for it to show as connected.
3. Add this integration, choose **Serial port (USB adapter or ESPHome serial proxy)** as the connection type and pick the ESPHome port (it is listed by the name given in `serial_proxy`, next to any local USB adapters).
4. Leave the baud rate at 9600 unless your inverter is configured differently.

If the ESPHome device is offline when Home Assistant starts, the integration waits and retries automatically once the ESPHome integration reconnects.

## Connecting through an Elfin EW11 (RS485 to Wi-Fi/TCP bridge)

The inverter does not answer Modbus requests. It pushes a status frame on its own, periodically and only while there is sunlight. The EW11 has to forward those raw bytes untouched, so **the serial port protocol must be `None`, not `Modbus`**. This is the most common setup mistake.

In the EW11 web interface (by default at the bridge's IP address):

**Serial Port Settings**

| Setting | Value |
|---|---|
| Baud Rate | 9600 |
| Data Bit | 8 |
| Stop Bit | 1 |
| Parity | None |
| Buffer Size | 512 |
| Gap Time | 50 |
| Flow Control | Disable |
| Cli | Serial String (default) |
| Protocol | **None** |

**Communication Settings** (edit the `netp` socket)

| Setting | Value |
|---|---|
| Protocol | Tcp Server |
| Local Port | 502 |
| Buffer Size | 512 |
| Keep Alive(s) | 60 |
| Timeout(s) | 0 |
| Max Accept | 3 |
| Security | Disable |
| Route | Uart |

After saving and restarting the EW11, open its **Status** page. The received byte counter on the serial side should go up while the sun is up. If it stays at 0, the problem is between the inverter and the EW11 (wiring, A/B swapped, wrong port), not in Home Assistant.

Then add this integration, choose **RS485 to TCP bridge (IP address)** and enter the EW11's IP address and port `502`.

## Connecting through an RS485 USB adapter

Plug the adapter into the Home Assistant host, add this integration, choose **Serial port (USB adapter or ESPHome serial proxy)** and pick the adapter from the list. Leave the baud rate at 9600. You do not need an IP address for this connection type.

## Configuring the payload version

The inverter firmware sends one of two frame layouts:

- Payload version 0: the "old" payload, from the older firmwares.
- Payload version 1: the "new" payload, with 6 extra bytes after the header.

If you are not sure, start with 0. With the wrong version every value is shifted into the wrong sensor, so you get things like a grid frequency of 0 Hz, a voltage of 49.5 V or a temperature of 15552 °C. The integration checks the first frame it receives and logs a warning naming the version to use if the configured one does not decode sensibly.

To change it, go to **Settings > Devices & services > FoxESS T Series > Configure**. You do not need to remove the integration.

Add the integration **only once per inverter**. A second entry for the same connection is rejected, because two entries would both decode every frame.

## How the values behave

- Values update each time the inverter sends a frame, so the refresh rate is set by the inverter, not by Home Assistant.
- The inverter stops sending at night. After 5 minutes without a valid frame, the live values (power, voltage, current, frequency) are set to 0 so the last daytime reading does not stay on screen all night. Temperatures and yields keep their last value.
- **Todays Yield** and **Total Yield** are energy sensors. Use **Total Yield** in the Energy dashboard as solar production.

## Troubleshooting

To see what the integration receives, go to **Settings > Devices & services > FoxESS T Series** and click **Enable debug logging**. Wait for a few frames (in daylight), then disable it to download the log. When opening an issue, attach that log along with your inverter model and firmware version.

| Symptom | What to check |
|---|---|
| "Unable to reach inverter" while adding the integration | For an EW11, check the IP and that the `netp` socket is a Tcp Server on port 502. For a USB adapter, choose the **Serial port** connection type instead of the TCP bridge. |
| All values stay at 0 or unknown | It is night, or no frames reach Home Assistant. Check the EW11 status page counters and that its Protocol is `None`. In the debug log, look for "Valid frame" lines. |
| Values look random or impossible | Wrong payload version. See [Configuring the payload version](#configuring-the-payload-version). |
| Values drop to 0 between updates | Normal only after 5 minutes without data. If it happens during the day, the EW11 settings are usually off; compare them with the tables above. |
| "Invalid message function code" in the debug log | Harmless. The inverter also sends other frame types that the integration skips. |

## Considerations

This integration was mainly tested by me, so feel free to test it and open any issues you find here in GitHub.

### Pull Requests are appreciated :)

## TODO List

- Request data from the inverter instead of waiting for its periodic frame, for faster updates (see [#1](https://github.com/LucasTor/FoxESS-T-series/issues/1)).
