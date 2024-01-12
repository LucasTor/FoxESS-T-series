# FoxESS T Series for Home Assistant

## About

This integration was mainly created to solve the issue for the SIW200G and SIW400G WEG inverters, really common in Brazil, which are made by FoxESS, being the T series.

In the current state, it only supports RS485 TCP bridges (I'm using Elfin EW11), but I plan on adding USB support in the future, let me know if you are interested in it...

## Installation

Please use HACS to install it :)

### Considerations

This integration is very new, and was only tested by me, so feel free to test it and open any issues you find here in GitHub

## TODO List
- Implement "zero values after" param so the integration can set the values to 0 after a set time without receiving data (inverter goes to sleep at night and does not send anything, we don't want any residual values that were left from the last comm)

- Implement USB support for RS485 -> USB adapters (Thanks @mattwilson1066 for researching the USB integration part)

- Add type energy for daily generation sensor to appear on the energy dashboard
