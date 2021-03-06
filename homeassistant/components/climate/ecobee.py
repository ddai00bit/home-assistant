"""
Platform for Ecobee Thermostats.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/climate.ecobee/
"""
import logging
from os import path

import voluptuous as vol

from homeassistant.components import ecobee
from homeassistant.components.climate import (
    DOMAIN, STATE_COOL, STATE_HEAT, STATE_AUTO, STATE_IDLE, ClimateDevice,
    ATTR_TARGET_TEMP_LOW, ATTR_TARGET_TEMP_HIGH)
from homeassistant.const import (
    ATTR_ENTITY_ID, STATE_OFF, STATE_ON, ATTR_TEMPERATURE, TEMP_FAHRENHEIT)
from homeassistant.config import load_yaml_config_file
import homeassistant.helpers.config_validation as cv

_CONFIGURING = {}
_LOGGER = logging.getLogger(__name__)

ATTR_FAN_MIN_ON_TIME = 'fan_min_on_time'
ATTR_RESUME_ALL = 'resume_all'

DEFAULT_RESUME_ALL = False

DEPENDENCIES = ['ecobee']

SERVICE_SET_FAN_MIN_ON_TIME = 'ecobee_set_fan_min_on_time'
SERVICE_RESUME_PROGRAM = 'ecobee_resume_program'

SET_FAN_MIN_ON_TIME_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Required(ATTR_FAN_MIN_ON_TIME): vol.Coerce(int),
})

RESUME_PROGRAM_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Optional(ATTR_RESUME_ALL, default=DEFAULT_RESUME_ALL): cv.boolean,
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the Ecobee Thermostat Platform."""
    if discovery_info is None:
        return
    data = ecobee.NETWORK
    hold_temp = discovery_info['hold_temp']
    _LOGGER.info(
        "Loading ecobee thermostat component with hold_temp set to %s",
        hold_temp)
    devices = [Thermostat(data, index, hold_temp)
               for index in range(len(data.ecobee.thermostats))]
    add_devices(devices)

    def fan_min_on_time_set_service(service):
        """Set the minimum fan on time on the target thermostats."""
        entity_id = service.data.get(ATTR_ENTITY_ID)
        fan_min_on_time = service.data[ATTR_FAN_MIN_ON_TIME]

        if entity_id:
            target_thermostats = [device for device in devices
                                  if device.entity_id in entity_id]
        else:
            target_thermostats = devices

        for thermostat in target_thermostats:
            thermostat.set_fan_min_on_time(str(fan_min_on_time))

            thermostat.schedule_update_ha_state(True)

    def resume_program_set_service(service):
        """Resume the program on the target thermostats."""
        entity_id = service.data.get(ATTR_ENTITY_ID)
        resume_all = service.data.get(ATTR_RESUME_ALL)

        if entity_id:
            target_thermostats = [device for device in devices
                                  if device.entity_id in entity_id]
        else:
            target_thermostats = devices

        for thermostat in target_thermostats:
            thermostat.resume_program(resume_all)

            thermostat.schedule_update_ha_state(True)

    descriptions = load_yaml_config_file(
        path.join(path.dirname(__file__), 'services.yaml'))

    hass.services.register(
        DOMAIN, SERVICE_SET_FAN_MIN_ON_TIME, fan_min_on_time_set_service,
        descriptions.get(SERVICE_SET_FAN_MIN_ON_TIME),
        schema=SET_FAN_MIN_ON_TIME_SCHEMA)

    hass.services.register(
        DOMAIN, SERVICE_RESUME_PROGRAM, resume_program_set_service,
        descriptions.get(SERVICE_RESUME_PROGRAM),
        schema=RESUME_PROGRAM_SCHEMA)


class Thermostat(ClimateDevice):
    """A thermostat class for Ecobee."""

    def __init__(self, data, thermostat_index, hold_temp):
        """Initialize the thermostat."""
        self.data = data
        self.thermostat_index = thermostat_index
        self.thermostat = self.data.ecobee.get_thermostat(
            self.thermostat_index)
        self._name = self.thermostat['name']
        self.hold_temp = hold_temp
        self._operation_list = ['auto', 'auxHeatOnly', 'cool',
                                'heat', 'off']
        self.update_without_throttle = False

    def update(self):
        """Get the latest state from the thermostat."""
        if self.update_without_throttle:
            self.data.update(no_throttle=True)
            self.update_without_throttle = False
        else:
            self.data.update()

        self.thermostat = self.data.ecobee.get_thermostat(
            self.thermostat_index)

    @property
    def name(self):
        """Return the name of the Ecobee Thermostat."""
        return self.thermostat['name']

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_FAHRENHEIT

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self.thermostat['runtime']['actualTemperature'] / 10

    @property
    def target_temperature_low(self):
        """Return the lower bound temperature we try to reach."""
        if self.current_operation == STATE_AUTO:
            return int(self.thermostat['runtime']['desiredHeat'] / 10)
        else:
            return None

    @property
    def target_temperature_high(self):
        """Return the upper bound temperature we try to reach."""
        if self.current_operation == STATE_AUTO:
            return int(self.thermostat['runtime']['desiredCool'] / 10)
        else:
            return None

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self.current_operation == STATE_AUTO:
            return None
        if self.current_operation == STATE_HEAT:
            return int(self.thermostat['runtime']['desiredHeat'] / 10)
        elif self.current_operation == STATE_COOL:
            return int(self.thermostat['runtime']['desiredCool'] / 10)
        else:
            return None

    @property
    def desired_fan_mode(self):
        """Return the desired fan mode of operation."""
        return self.thermostat['runtime']['desiredFanMode']

    @property
    def fan(self):
        """Return the current fan state."""
        if 'fan' in self.thermostat['equipmentStatus']:
            return STATE_ON
        else:
            return STATE_OFF

    @property
    def current_hold_mode(self):
        """Return current hold mode."""
        if self.is_away_mode_on:
            hold = 'away'
        elif self.is_home_mode_on:
            hold = 'home'
        elif self.is_temp_hold_on():
            hold = 'temp'
        else:
            hold = None
        return hold

    @property
    def current_operation(self):
        """Return current operation."""
        if self.operation_mode == 'auxHeatOnly' or \
           self.operation_mode == 'heatPump':
            return STATE_HEAT
        else:
            return self.operation_mode

    @property
    def operation_list(self):
        """Return the operation modes list."""
        return self._operation_list

    @property
    def operation_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self.thermostat['settings']['hvacMode']

    @property
    def mode(self):
        """Return current mode ie. home, away, sleep."""
        return self.thermostat['program']['currentClimateRef']

    @property
    def fan_min_on_time(self):
        """Return current fan minimum on time."""
        return self.thermostat['settings']['fanMinOnTime']

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        # Move these to Thermostat Device and make them global
        status = self.thermostat['equipmentStatus']
        operation = None
        if status == '':
            operation = STATE_IDLE
        elif 'Cool' in status:
            operation = STATE_COOL
        elif 'auxHeat' in status:
            operation = STATE_HEAT
        elif 'heatPump' in status:
            operation = STATE_HEAT
        else:
            operation = status
        return {
            "actual_humidity": self.thermostat['runtime']['actualHumidity'],
            "fan": self.fan,
            "mode": self.mode,
            "operation": operation,
            "fan_min_on_time": self.fan_min_on_time
        }

    def is_vacation_on(self):
        """Return true if vacation mode is on."""
        events = self.thermostat['events']
        return any(event['type'] == 'vacation' and event['running']
                   for event in events)

    def is_temp_hold_on(self):
        """Return true if temperature hold is on."""
        events = self.thermostat['events']
        return any(event['type'] == 'hold' and event['running']
                   for event in events)

    @property
    def is_away_mode_on(self):
        """Return true if away mode is on."""
        events = self.thermostat['events']
        return any(event['holdClimateRef'] == 'away' or
                   event['type'] == 'autoAway'
                   for event in events)

    def turn_away_mode_on(self):
        """Turn away on."""
        self.data.ecobee.set_climate_hold(self.thermostat_index,
                                          "away", self.hold_preference())
        self.update_without_throttle = True

    def turn_away_mode_off(self):
        """Turn away off."""
        self.set_hold_mode(None)

    @property
    def is_home_mode_on(self):
        """Return true if home mode is on."""
        events = self.thermostat['events']
        return any(event['holdClimateRef'] == 'home' or
                   event['type'] == 'autoHome'
                   for event in events)

    def turn_home_mode_on(self):
        """Turn home on."""
        self.data.ecobee.set_climate_hold(self.thermostat_index,
                                          "home", self.hold_preference())
        self.update_without_throttle = True

    def set_hold_mode(self, hold_mode):
        """Set hold mode (away, home, temp)."""
        hold = self.current_hold_mode

        if hold == hold_mode:
            return
        elif hold_mode == 'away':
            self.turn_away_mode_on()
        elif hold_mode == 'home':
            self.turn_home_mode_on()
        elif hold_mode == 'temp':
            self.set_temp_hold(int(self.current_temperature))
        else:
            self.data.ecobee.resume_program(self.thermostat_index)
            self.update_without_throttle = True

    def set_auto_temp_hold(self, heat_temp, cool_temp):
        """Set temperature hold in auto mode."""
        self.data.ecobee.set_hold_temp(self.thermostat_index, cool_temp,
                                       heat_temp, self.hold_preference())
        _LOGGER.debug("Setting ecobee hold_temp to: heat=%s, is=%s, "
                      "cool=%s, is=%s", heat_temp, isinstance(
                          heat_temp, (int, float)), cool_temp,
                      isinstance(cool_temp, (int, float)))

        self.update_without_throttle = True

    def set_temp_hold(self, temp):
        """Set temperature hold in modes other than auto."""
        # Set arbitrary range when not in auto mode
        if self.current_operation == STATE_HEAT:
            heat_temp = temp
            cool_temp = temp + 20
        elif self.current_operation == STATE_COOL:
            heat_temp = temp - 20
            cool_temp = temp

        self.data.ecobee.set_hold_temp(self.thermostat_index, cool_temp,
                                       heat_temp, self.hold_preference())
        _LOGGER.debug("Setting ecobee hold_temp to: low=%s, is=%s, "
                      "cool=%s, is=%s", heat_temp, isinstance(
                          heat_temp, (int, float)), cool_temp,
                      isinstance(cool_temp, (int, float)))

        self.update_without_throttle = True

    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        low_temp = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high_temp = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        temp = kwargs.get(ATTR_TEMPERATURE)

        if self.current_operation == STATE_AUTO and low_temp is not None \
           and high_temp is not None:
            self.set_auto_temp_hold(int(low_temp), int(high_temp))
        elif temp is not None:
            self.set_temp_hold(int(temp))
        else:
            _LOGGER.error(
                'Missing valid arguments for set_temperature in %s', kwargs)

    def set_operation_mode(self, operation_mode):
        """Set HVAC mode (auto, auxHeatOnly, cool, heat, off)."""
        self.data.ecobee.set_hvac_mode(self.thermostat_index, operation_mode)
        self.update_without_throttle = True

    def set_fan_min_on_time(self, fan_min_on_time):
        """Set the minimum fan on time."""
        self.data.ecobee.set_fan_min_on_time(self.thermostat_index,
                                             fan_min_on_time)
        self.update_without_throttle = True

    def resume_program(self, resume_all):
        """Resume the thermostat schedule program."""
        self.data.ecobee.resume_program(self.thermostat_index,
                                        str(resume_all).lower())
        self.update_without_throttle = True

    def hold_preference(self):
        """Return user preference setting for hold time."""
        # Values returned from thermostat are 'useEndTime4hour',
        # 'useEndTime2hour', 'nextTransition', 'indefinite', 'askMe'
        default = self.thermostat['settings']['holdAction']
        if default == 'nextTransition':
            return default
        elif default == 'indefinite':
            return default
        else:
            return 'nextTransition'

    # Sleep mode isn't used in UI yet:

    # def turn_sleep_mode_on(self):
    #     """ Turns sleep mode on. """
    #     self.data.ecobee.set_climate_hold(self.thermostat_index, "sleep")

    # def turn_sleep_mode_off(self):
    #     """ Turns sleep mode off. """
    #     self.data.ecobee.resume_program(self.thermostat_index)
