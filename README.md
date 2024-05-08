# FoxESS T Series for Home Assistant

## About

This integration was mainly created to solve the issue for the SIW200G and SIW400G WEG inverters, really common in Brazil, which are made by FoxESS, being the T series.

The integration now supports both RS485 TCP Bridges as well as RS485 USB Adapters (the one we tested and know it's working is the WaveShare RS485 to USB (B))

To connect your inverter to either adapter (TCP or USB), on the newer models, please refer to [this guide](https://github.com/LucasTor/FoxESS-T-series/issues/2#issuecomment-2088445998), for older models, a RS485 comm port should be found on one of the connectors on the inverter.
## Installation

Please use HACS to install it :)

## Configuring the payload version

Payload version 0: The "old" payload, from the older firmwares
Payload version 1: The "new" payload, with the added 6 bytes in the beginning

## Considerations

This integration is very new, and was only tested by me, so feel free to test it and open any issues you find here in GitHub

### Pull Requests are appreciated :)
## TODO List
- Implement "zero values after" param so the integration can set the values to 0 after a set time without receiving data (inverter goes to sleep at night and does not send anything, we don't want any residual values that were left from the last comm)

- Add type energy for daily generation sensor to appear on the energy dashboard
