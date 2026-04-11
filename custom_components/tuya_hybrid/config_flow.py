"""Config flow for LocalTuya integration integration."""
import errno
import logging
import time
from importlib import import_module

import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.entity_registry as er
import voluptuous as vol
from homeassistant import config_entries, core, exceptions
from homeassistant.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_ID,
    CONF_NAME,
    CONF_PLATFORM,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback

from .cloud_api import TuyaCloudApi
from .common import pytuya
from .const import (
    ATTR_UPDATED_AT,
    CONF_ACTION,
    CONF_ADD_DEVICE,
    CONF_DPS_STRINGS,
    CONF_EDIT_DEVICE,
    CONF_ENABLE_DEBUG,
    CONF_LOCAL_KEY,
    CONF_MANUAL_DPS,
    CONF_MODEL,
    CONF_NO_CLOUD,
    CONF_PRODUCT_KEY,
    CONF_PRODUCT_NAME,
    CONF_PROTOCOL_VERSION,
    CONF_RESET_DPIDS,
    CONF_SETUP_CLOUD,
    CONF_USER_ID,
    CONF_ENABLE_ADD_ENTITIES,
    DATA_CLOUD,
    DATA_DISCOVERY,
    DOMAIN,
    PLATFORMS,
    CONF_SETUP_CLOUD_SHARING,
    CONF_USER_CODE,
)
from .discovery import discover

_LOGGER = logging.getLogger(__name__)

ENTRIES_VERSION = 2

def _write_diagnostic_error(ex: Exception):
    """Write internal diagnostics log for the agent to see."""
    try:
        import traceback
        import os
        log_path = os.path.join(os.path.dirname(__file__), "DIAGNOSTICS.log")
        with open(log_path, "a") as f:
            f.write(f"\n--- ERROR AT {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            if ex and str(ex) != "MODULE LOADED - DIAGNOSTICS ACTIVE":
                f.write(f"Exception: {type(ex).__name__}: {str(ex)}\n")
            
            # format_exc() only works if an exception is currently being handled
            exc_info = traceback.format_exc()
            if exc_info.strip() != "NoneType: None":
                f.write(exc_info)
            elif ex:
                f.write("".join(traceback.format_exception(type(ex), ex, ex.__traceback__)))
            f.write("\n------------------------------\n")
    except:
        pass

_write_diagnostic_error(Exception("MODULE LOADED - DIAGNOSTICS ACTIVE"))

PLATFORM_TO_ADD = "platform_to_add"
NO_ADDITIONAL_ENTITIES = "no_additional_entities"
SELECTED_DEVICE = "selected_device"

CUSTOM_DEVICE = "..."

CONF_AUTO_IMPORT = "auto_import"

CONF_ACTIONS = {
    CONF_ADD_DEVICE: "Add a new device",
    CONF_EDIT_DEVICE: "Edit a device",
    CONF_AUTO_IMPORT: "Automatically add all discovered cloud devices",
    CONF_SETUP_CLOUD: "Reconfigure Cloud API (Client ID/Secret)",
    CONF_SETUP_CLOUD_SHARING: "Link Tuya Account (Easy Login via QR Code)",
}

CONF_ACTIONS_FIRST_TIME = {
    CONF_SETUP_CLOUD_SHARING: "Link Tuya Account (Easy Login via QR Code - RECOMMENDED)",
    CONF_SETUP_CLOUD: "Manual Cloud Configuration (Client ID/Secret)",
    CONF_NO_CLOUD: "No Cloud (Manual device entry only)",
}

CONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACTION, default=CONF_ADD_DEVICE): vol.In(CONF_ACTIONS),
    }
)

CLOUD_SETUP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REGION, default="eu"): vol.In(["eu", "us", "cn", "in"]),
        vol.Optional(CONF_CLIENT_ID): cv.string,
        vol.Optional(CONF_CLIENT_SECRET): cv.string,
        vol.Optional(CONF_USER_ID): cv.string,
        vol.Optional(CONF_USERNAME, default=DOMAIN): cv.string,
        vol.Required(CONF_NO_CLOUD, default=False): bool,
    }
)


DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FRIENDLY_NAME): cv.string,
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_LOCAL_KEY): cv.string,
        vol.Required(CONF_PROTOCOL_VERSION, default="3.3"): vol.In(
            ["3.1", "3.2", "3.3", "3.4"]
        ),
        vol.Required(CONF_ENABLE_DEBUG, default=False): bool,
        vol.Optional(CONF_SCAN_INTERVAL): int,
        vol.Optional(CONF_MANUAL_DPS): cv.string,
        vol.Optional(CONF_RESET_DPIDS): str,
    }
)

PICK_ENTITY_SCHEMA = vol.Schema(
    {vol.Required(PLATFORM_TO_ADD, default="switch"): vol.In(PLATFORMS)}
)


def devices_schema(discovered_devices, cloud_devices_list, configured_devices=None, add_custom_device=True):
    """Create schema for devices step."""
    devices = {}
    configured_devices = configured_devices or []
    
    # First, add all discovered devices (not yet configured)
    for dev_id, dev_info in discovered_devices.items():
        if dev_id not in configured_devices:
            dev_host = dev_info.get("ip", "unknown")
            dev_name = dev_id
            if dev_id in cloud_devices_list:
                dev_name = cloud_devices_list[dev_id].get(CONF_NAME, dev_id)
            devices[dev_id] = f"{dev_name} ({dev_host})"
    
    # Then, add any devices found in the cloud that were NOT discovered locally and NOT yet configured
    for dev_id, dev_info in cloud_devices_list.items():
        if dev_id not in devices and dev_id not in configured_devices:
            dev_name = dev_info.get(CONF_NAME, dev_id)
            devices[dev_id] = f"{dev_name} (Cloud only - manual IP needed)"

    if add_custom_device:
        devices[CUSTOM_DEVICE] = "Manual entry"
    return vol.Schema({vol.Required(SELECTED_DEVICE): vol.In(devices)})


async def _detect_entities_from_datamodel(cloud_sharing, dev_id):
    """Heuristic logic to detect entities from Tuya datamodel."""
    entities = []
    dps_strings = []
    datamodel = await cloud_sharing.async_get_datamodel(dev_id)
    if datamodel:
        for dp in datamodel:
            dp_id = dp["id"]
            dp_name = dp["name"]
            dp_type = dp["type"]
            dps_strings.append(f"{dp_id} ({dp_name})")
            
            platform = "sensor"
            dp_name_lower = str(dp_name).lower()
            
            if dp_type == "Boolean":
                if any(sub in dp_name_lower for sub in ["door", "window", "contact", "water", "leak", "motion", "presence", "pir", "tamper", "alarm", "fault", "state", "low", "high", "dry", "wet", "open", "close"]):
                    platform = "binary_sensor"
                elif any(sub in dp_name_lower for sub in ["light", "led", "lamp", "backlight", "indicator"]):
                    platform = "light"
                elif any(sub in dp_name_lower for sub in ["siren", "buzzer", "bell"]):
                    platform = "siren"
                elif any(sub in dp_name_lower for sub in ["fan", "ventilator"]):
                    platform = "fan"
                else:
                    platform = "switch"
            elif dp_type == "Enum":
                if any(sub in dp_name_lower for sub in ["mode", "status", "state", "work", "unit", "fault"]):
                    platform = "sensor"
                elif any(sub in dp_name_lower for sub in ["fan", "speed", "level"]):
                    platform = "fan"
            elif dp_type in ["Integer", "Value"]:
                if any(sub in dp_name_lower for sub in ["temp", "humid", "volt", "current", "power", "batt", "speed", "bright", "color", "time", "count", "value", "conc", "signal", "rssi", "energy", "lux", "illumin"]):
                    platform = "sensor"
                else:
                    platform = "number"
            
            entities.append({
                CONF_ID: int(dp_id),
                CONF_FRIENDLY_NAME: str(dp_name).replace("_", " ").title(),
                CONF_PLATFORM: platform,
            })
    return entities, dps_strings


async def _generate_auto_import_devices(hass, cloud_sharing, cloud_devs, existing_devices=None):
    if existing_devices is None:
        existing_devices = {}
    
    # Perform local discovery to get reliable local IPs
    from .discovery import discover
    local_devs = {}
    try:
        _LOGGER.debug("Starting local discovery for auto-import...")
        local_devs = await discover()
        _LOGGER.debug("Discovered %d devices locally", len(local_devs))
    except Exception as ex:
        _LOGGER.warning("Local discovery failed during auto-import: %s", ex)

    configured = existing_devices.copy()
    new_devices = 0
    
    for dev_id, dev_info in cloud_devs.items():
        if dev_id in configured:
            continue
            
        try:
            # Match cloud device with local discovery for best IP
            ip_address = dev_info.get("ip", "")
            if dev_id in local_devs:
                ip_address = local_devs[dev_id].get("ip", ip_address)
                _LOGGER.debug("Updated IP for %s from local discovery: %s", dev_id, ip_address)
            
            entities = []
            dps_strings = []
            if cloud_sharing:
                entities, dps_strings = await _detect_entities_from_datamodel(cloud_sharing, dev_id)
            
            # If no entities found, fallback
            if not entities:
                entities.append({
                    CONF_ID: 1,
                    CONF_FRIENDLY_NAME: "Switch 1",
                    CONF_PLATFORM: "switch",
                })
                dps_strings = ["1 (Auto-added)"]

            dev_config = {
                CONF_FRIENDLY_NAME: dev_info.get("name", f"Tuya {dev_id}"),
                CONF_HOST: dev_info.get("ip", ""),
                CONF_DEVICE_ID: dev_id,
                CONF_LOCAL_KEY: dev_info.get(CONF_LOCAL_KEY, ""),
                CONF_PROTOCOL_VERSION: "3.3",
                CONF_ENTITIES: entities,
                CONF_DPS_STRINGS: dps_strings,
            }
            configured[dev_id] = dev_config
            new_devices += 1
        except Exception as ex:
            _LOGGER.error("Failed to auto-import device %s: %s", dev_id, ex)
            _write_diagnostic_error(ex)

    return configured, new_devices


def options_schema(entities):
    """Create schema for options."""
    entity_names = [
        f"{entity[CONF_ID]}: {entity[CONF_FRIENDLY_NAME]}" for entity in entities
    ]
    return vol.Schema(
        {
            vol.Required(CONF_FRIENDLY_NAME): cv.string,
            vol.Required(CONF_HOST): cv.string,
            vol.Required(CONF_LOCAL_KEY): cv.string,
            vol.Required(CONF_PROTOCOL_VERSION, default="3.3"): vol.In(
                ["3.1", "3.2", "3.3", "3.4"]
            ),
            vol.Required(CONF_ENABLE_DEBUG, default=False): bool,
            vol.Optional(CONF_SCAN_INTERVAL): int,
            vol.Optional(CONF_MANUAL_DPS): cv.string,
            vol.Optional(CONF_RESET_DPIDS): cv.string,
            vol.Required(
                CONF_ENTITIES, description={"suggested_value": entity_names}
            ): cv.multi_select(entity_names),
            vol.Required(CONF_ENABLE_ADD_ENTITIES, default=False): bool,
        }
    )


def schema_defaults(schema, dps_list=None, **defaults):
    """Create a new schema with default values filled in."""
    copy = schema.extend({})
    for field, field_type in copy.schema.items():
        if isinstance(field_type, vol.In):
            value = None
            for dps in dps_list or []:
                if dps.startswith(f"{defaults.get(field)} "):
                    value = dps
                    break

            if value in field_type.container:
                field.default = vol.default_factory(value)
                continue

        if field.schema in defaults:
            field.default = vol.default_factory(defaults[field])
    return copy


def dps_string_list(dps_data):
    """Return list of friendly DPS values."""
    return [f"{id} (value: {value})" for id, value in dps_data.items()]


def gen_dps_strings():
    """Generate list of DPS values."""
    return [f"{dp} (value: ?)" for dp in range(1, 256)]


def platform_schema(platform, dps_strings, allow_id=True, yaml=False):
    """Generate input validation schema for a platform."""
    schema = {}
    if yaml:
        # In YAML mode we force the specified platform to match flow schema
        schema[vol.Required(CONF_PLATFORM)] = vol.In([platform])
    if allow_id:
        schema[vol.Required(CONF_ID)] = vol.In(dps_strings)
    schema[vol.Required(CONF_FRIENDLY_NAME)] = str
    return vol.Schema(schema).extend(flow_schema(platform, dps_strings))


def flow_schema(platform, dps_strings):
    """Return flow schema for a specific platform."""
    integration_module = ".".join(__name__.split(".")[:-1])
    return import_module("." + platform, integration_module).flow_schema(dps_strings)


def strip_dps_values(user_input, dps_strings):
    """Remove values and keep only index for DPS config items."""
    stripped = {}
    for field, value in user_input.items():
        if value in dps_strings:
            stripped[field] = int(user_input[field].split(" ")[0])
        else:
            stripped[field] = user_input[field]
    return stripped


def config_schema():
    """Build schema used for setting up component."""
    entity_schemas = [
        platform_schema(platform, range(1, 256), yaml=True) for platform in PLATFORMS
    ]
    return vol.Schema(
        {
            DOMAIN: vol.All(
                cv.ensure_list,
                [
                    DEVICE_SCHEMA.extend(
                        {vol.Required(CONF_ENTITIES): [vol.Any(*entity_schemas)]}
                    )
                ],
            )
        },
        extra=vol.ALLOW_EXTRA,
    )


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect."""
    detected_dps = {}

    interface = None

    reset_ids = None
    try:
        interface = await pytuya.connect(
            data[CONF_HOST],
            data[CONF_DEVICE_ID],
            data[CONF_LOCAL_KEY],
            float(data[CONF_PROTOCOL_VERSION]),
            data[CONF_ENABLE_DEBUG],
        )
        if CONF_RESET_DPIDS in data:
            reset_ids_str = data[CONF_RESET_DPIDS].split(",")
            reset_ids = []
            for reset_id in reset_ids_str:
                reset_ids.append(int(reset_id.strip()))
            _LOGGER.debug(
                "Reset DPIDs configured: %s (%s)",
                data[CONF_RESET_DPIDS],
                reset_ids,
            )
        try:
            detected_dps = await interface.detect_available_dps()
        except Exception as ex:
            try:
                _LOGGER.debug(
                    "Initial state update failed (%s), trying reset command", ex
                )
                if len(reset_ids) > 0:
                    await interface.reset(reset_ids)
                    detected_dps = await interface.detect_available_dps()
            except Exception as ex:
                _LOGGER.debug("No DPS able to be detected: %s", ex)
                detected_dps = {}

        # if manual DPs are set, merge these.
        _LOGGER.debug("Detected DPS: %s", detected_dps)
        if CONF_MANUAL_DPS in data:
            manual_dps_list = [dps.strip() for dps in data[CONF_MANUAL_DPS].split(",")]
            _LOGGER.debug(
                "Manual DPS Setting: %s (%s)", data[CONF_MANUAL_DPS], manual_dps_list
            )
            # merge the lists
            for new_dps in manual_dps_list + (reset_ids or []):
                # If the DPS not in the detected dps list, then add with a
                # default value indicating that it has been manually added
                if str(new_dps) not in detected_dps:
                    detected_dps[new_dps] = -1

    except (ConnectionRefusedError, ConnectionResetError) as ex:
        raise CannotConnect from ex
    except ValueError as ex:
        raise InvalidAuth from ex
    finally:
        if interface:
            await interface.close()

    # Indicate an error if no datapoints found as the rest of the flow
    # won't work in this case
    if not detected_dps:
        raise EmptyDpsList

    _LOGGER.debug("Total DPS: %s", detected_dps)

    return dps_string_list(detected_dps)


async def attempt_cloud_connection(hass, user_input):
    """Create device."""
    cloud_api = TuyaCloudApi(
        hass,
        user_input.get(CONF_REGION),
        user_input.get(CONF_CLIENT_ID),
        user_input.get(CONF_CLIENT_SECRET),
        user_input.get(CONF_USER_ID),
    )

    res = await cloud_api.async_get_access_token()
    if res != "ok":
        _LOGGER.error("Cloud API connection failed: %s", res)
        return cloud_api, {"reason": "authentication_failed", "msg": res}

    res = await cloud_api.async_get_devices_list()
    if res != "ok":
        _LOGGER.error("Cloud API get_devices_list failed: %s", res)
        return cloud_api, {"reason": "device_list_failed", "msg": res}
    _LOGGER.info("Cloud API connection succeeded.")

    return cloud_api, {}


class LocaltuyaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LocalTuya integration."""

    VERSION = ENTRIES_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow for this handler."""
        return LocalTuyaOptionsFlowHandler(config_entry)

    def __init__(self):
        """Initialize a new LocaltuyaConfigFlow."""

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                action = user_input.get(CONF_ACTION)
                if action == CONF_SETUP_CLOUD_SHARING:
                    return await self.async_step_cloud_sharing()
                if action == CONF_SETUP_CLOUD:
                    return await self.async_step_cloud_setup_manual()
                if action == CONF_NO_CLOUD:
                    return await self._create_entry({CONF_NO_CLOUD: True, CONF_USERNAME: "Tuya Hybrid (Local)"})
            except Exception as ex:
                _LOGGER.exception("Error in user step: %s", ex)
                _write_diagnostic_error(ex)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ACTION, default=CONF_SETUP_CLOUD_SHARING): vol.In(CONF_ACTIONS_FIRST_TIME)
            }),
            errors=errors,
        )

    async def async_step_cloud_setup_manual(self, user_input=None):
        """Handle manual cloud setup."""
        errors = {}
        placeholders = {}
        if user_input is not None:
            if user_input.get(CONF_NO_CLOUD):
                return await self._create_entry(user_input)

            cloud_api, res = await attempt_cloud_connection(self.hass, user_input)

            if not res:
                return await self._create_entry(user_input)
            errors["base"] = res["reason"]
            placeholders = {"msg": res["msg"]}

        return self.async_show_form(
            step_id="cloud_setup_manual",
            data_schema=schema_defaults(CLOUD_SETUP_SCHEMA, **(user_input or {})),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _create_entry(self, user_input):
        """Register new entry."""
        user_id = user_input.get(CONF_USER_ID)
        if user_id:
            await self.async_set_unique_id(user_id)
            self._abort_if_unique_id_configured()
        
        user_input.setdefault(CONF_DEVICES, {})
        return self.async_create_entry(
            title=user_input.get(CONF_USERNAME, "Tuya Hybrid"),
            data=user_input,
        )

    async def async_step_cloud_sharing(self, user_input=None):
        """Handle Tuya Cloud Sharing (Easy Login)."""
        errors = {}
        if user_input is not None:
            try:
                from .cloud_sharing import Cloud
                user_code = user_input.get(CONF_USER_CODE)
                self.hass.data.setdefault(DOMAIN, {})
                self.hass.data[DOMAIN]["sharing"] = Cloud(self.hass)
                qr_code = await self.hass.data[DOMAIN]["sharing"].async_get_qr_code(user_code)
                if qr_code:
                    return await self.async_step_cloud_sharing_qr()
                errors["base"] = "qr_code_failed"
            except Exception as ex:
                _LOGGER.exception("Error during cloud sharing setup: %s", ex)
                _write_diagnostic_error(ex)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="cloud_sharing",
            data_schema=vol.Schema({vol.Required(CONF_USER_CODE): str}),
            errors=errors,
        )

    async def async_step_cloud_sharing_qr(self, user_input=None):
        """Handle QR code scan confirmation."""
        errors = {}
        if user_input is not None:
            try:
                success = await self.hass.data[DOMAIN]["sharing"].async_login()
                if success:
                    devices = await self.hass.data[DOMAIN]["sharing"].async_get_devices()
                    # Store devices in DATA_CLOUD format
                    class MockCloudApi:
                        def __init__(self, devices):
                            self.device_list = devices
                    self.hass.data[DOMAIN][DATA_CLOUD] = MockCloudApi(devices)
                    
                    # If we should auto-import after login, go there next
                    if getattr(self, "_auto_import_after_login", False):
                        return await self.async_step_auto_import()
                    
                    # If we are in ConfigFlow (no config_entry yet), ask if they want to import existing devices
                    if not hasattr(self, "config_entry") or self.config_entry is None:
                        return await self.async_step_auto_import()
                    
                    # If we are in OptionsFlow, just proceed to add device
                    return await self.async_step_add_device()
                errors["base"] = "login_failed"
            except Exception as ex:
                _LOGGER.exception("Error during cloud login: %s", ex)
                _write_diagnostic_error(ex)
                errors["base"] = "unknown"

        qr_code_url = self.hass.data[DOMAIN]["sharing"].qr_code_url
        return self.async_show_form(
            step_id="cloud_sharing_qr",
            errors=errors,
            description_placeholders={"qr_code": f"![QR Code]({qr_code_url})"},
        )

    async def async_step_auto_import(self, user_input=None):
        """Automatically import all devices from cloud in ConfigFlow."""
        if user_input is not None:
            if user_input.get("do_import"):
                data = self.hass.data.get(DOMAIN, {})
                cloud_sharing = data.get("sharing")
                cloud_devs = {}
                if DATA_CLOUD in data and data[DATA_CLOUD]:
                    cloud_devs = data[DATA_CLOUD].device_list
                configured, new_count = await _generate_auto_import_devices(self.hass, cloud_sharing, cloud_devs)
                
                return await self._create_entry({
                    CONF_NO_CLOUD: False,
                    CONF_USERNAME: "Tuya Hybrid",
                    ATTR_UPDATED_AT: str(int(time.time() * 1000)),
                    CONF_DEVICES: configured,
                })
            else:
                return await self._create_entry({
                    CONF_NO_CLOUD: False,
                    CONF_USERNAME: "Tuya Hybrid",
                    ATTR_UPDATED_AT: str(int(time.time() * 1000)),
                })

        cloud_data = self.hass.data.get(DOMAIN, {}).get(DATA_CLOUD)
        devices_count = len(cloud_data.device_list) if cloud_data else 0
        return self.async_show_form(
            step_id="auto_import",
            data_schema=vol.Schema({vol.Optional("do_import", default=True): bool}),
            description_placeholders={"count": str(devices_count)},
        )

    async def async_step_import(self, user_input):
        """Handle import from YAML."""
        _LOGGER.error(
            "Configuration via YAML file is no longer supported by this integration."
        )


class LocalTuyaOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for LocalTuya integration."""

    def __init__(self, config_entry):
        """Initialize localtuya options flow."""
        self._config_entry = config_entry
        # self.dps_strings = config_entry.data.get(CONF_DPS_STRINGS, gen_dps_strings())
        # self.entities = config_entry.data[CONF_ENTITIES]
        self.selected_device = None
        self.editing_device = False
        self.device_data = None
        self.dps_strings = []
        self.selected_platform = None
        self.discovered_devices = {}
        self.entities = []
        self._auto_import_after_login = False

    async def async_step_init(self, user_input=None):
        """Manage basic options."""
        # device_id = self.config_entry.data[CONF_DEVICE_ID]
        if user_input is not None:
            if user_input.get(CONF_ACTION) == CONF_SETUP_CLOUD:
                return await self.async_step_cloud_setup()
            if user_input.get(CONF_ACTION) == CONF_SETUP_CLOUD_SHARING:
                self._auto_import_after_login = False
                return await self.async_step_cloud_sharing()
            if user_input.get(CONF_ACTION) == CONF_AUTO_IMPORT:
                self._auto_import_after_login = True
                return await self.async_step_cloud_sharing()
            if user_input.get(CONF_ACTION) == CONF_ADD_DEVICE:
                return await self.async_step_add_device()
            if user_input.get(CONF_ACTION) == CONF_EDIT_DEVICE:
                return await self.async_step_edit_device()

        return self.async_show_form(
            step_id="init",
            data_schema=CONFIGURE_SCHEMA,
        )

    async def async_step_cloud_setup(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        placeholders = {}
        if user_input is not None:
            if user_input.get(CONF_NO_CLOUD):
                new_data = self.config_entry.data.copy()
                new_data.update(user_input)
                for i in [CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_USER_ID]:
                    new_data[i] = ""
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=new_data,
                )
                return self.async_create_entry(
                    title=new_data.get(CONF_USERNAME), data={}
                )

            cloud_api, res = await attempt_cloud_connection(self.hass, user_input)

            if not res:
                new_data = self.config_entry.data.copy()
                new_data.update(user_input)
                cloud_devs = cloud_api.device_list
                for dev_id, dev in new_data[CONF_DEVICES].items():
                    if CONF_MODEL not in dev and dev_id in cloud_devs:
                        model = cloud_devs[dev_id].get(CONF_PRODUCT_NAME)
                        new_data[CONF_DEVICES][dev_id][CONF_MODEL] = model
                new_data[ATTR_UPDATED_AT] = str(int(time.time() * 1000))

                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=new_data,
                )
                return self.async_create_entry(
                    title=new_data.get(CONF_USERNAME), data={}
                )
            errors["base"] = res["reason"]
            placeholders = {"msg": res["msg"]}

        defaults = self._config_entry.data.copy()
        defaults.update(user_input or {})
        defaults[CONF_NO_CLOUD] = False

        return self.async_show_form(
            step_id="cloud_setup",
            data_schema=schema_defaults(CLOUD_SETUP_SCHEMA, **defaults),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_cloud_sharing(self, user_input=None):
        """Handle Tuya Cloud Sharing (Easy Login) for Options Flow."""
        # ConfigFlow and OptionsFlow can share these methods if they have same names
        # But we need to be careful with 'self' context.
        # Since I added them to LocaltuyaConfigFlow, I can just call them if I make sure 
        # LocalTuyaOptionsFlowHandler knows where they are.
        # Actually, it's easier to just have them here too but they are very similar.
        
        # To avoid confusion, I'll just keep the existing ones in OptionsFlow but they'll 
        # be redundant. The Error happened because async_step_user called a non-existent method.
        # Now it exists in LocaltuyaConfigFlow.
        
        return await LocaltuyaConfigFlow.async_step_cloud_sharing(self, user_input)

    async def async_step_cloud_sharing_qr(self, user_input=None):
        """Handle QR code scan confirmation for Options Flow."""
        return await LocaltuyaConfigFlow.async_step_cloud_sharing_qr(self, user_input)

    async def async_step_auto_import(self, user_input=None):
        """Automatically import all devices from cloud."""
        if user_input is not None:
            # User confirmed the import
            data = self.hass.data.get(DOMAIN, {})
            if not data or DATA_CLOUD not in data or not data[DATA_CLOUD]:
                return self.async_abort(reason="no_cloud_connection")
            
            cloud_sharing = data.get("sharing")
            cloud_devs = data[DATA_CLOUD].device_list
            configured_devices = self.config_entry.data.get(CONF_DEVICES, {})
            
            configured, new_count = await _generate_auto_import_devices(self.hass, cloud_sharing, cloud_devs, configured_devices)

            if new_count > 0:
                new_data = self.config_entry.data.copy()
                new_data[CONF_DEVICES] = configured
                new_data[ATTR_UPDATED_AT] = str(int(time.time() * 1000))
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=new_data,
                )
                return self.async_create_entry(title="", data={})
            
            return self.async_abort(reason="no_new_devices")

        cloud_data = self.hass.data.get(DOMAIN, {}).get(DATA_CLOUD)
        devices_count = len(cloud_data.device_list) if cloud_data else 0
        return self.async_show_form(
            step_id="auto_import",
            description_placeholders={"count": str(devices_count)},
        )

    async def async_step_add_device(self, user_input=None):
        """Handle adding a new device."""
        # Use cache if available or fallback to manual discovery
        self.editing_device = False
        self.selected_device = None
        errors = {}
        if user_input is not None:
            if user_input[SELECTED_DEVICE] != CUSTOM_DEVICE:
                self.selected_device = user_input[SELECTED_DEVICE]

            return await self.async_step_configure_device()

        self.discovered_devices = {}
        data = self.hass.data.get(DOMAIN)

        if data and DATA_DISCOVERY in data:
            self.discovered_devices = data[DATA_DISCOVERY].devices
        else:
            try:
                self.discovered_devices = await discover()
            except OSError as ex:
                if ex.errno == errno.EADDRINUSE:
                    errors["base"] = "address_in_use"
                else:
                    errors["base"] = "discovery_failed"
            except Exception as ex:
                _LOGGER.exception("discovery failed: %s", ex)
                errors["base"] = "discovery_failed"

        cloud_list = {}
        if DATA_CLOUD in data and data[DATA_CLOUD]:
            cloud_list = data[DATA_CLOUD].device_list

        configured = self.config_entry.data.get(CONF_DEVICES, {})

        return self.async_show_form(
            step_id="add_device",
            data_schema=devices_schema(
                self.discovered_devices, cloud_list, list(configured.keys())
            ),
            errors=errors,
        )

    async def async_step_edit_device(self, user_input=None):
        """Handle editing a device."""
        self.editing_device = True
        # Use cache if available or fallback to manual discovery
        errors = {}
        if user_input is not None:
            self.selected_device = user_input[SELECTED_DEVICE]
            dev_conf = self.config_entry.data[CONF_DEVICES][self.selected_device]
            self.dps_strings = dev_conf.get(CONF_DPS_STRINGS, gen_dps_strings())
            self.entities = dev_conf[CONF_ENTITIES]

            return await self.async_step_configure_device()

        devices = {}
        for dev_id, configured_dev in self.config_entry.data[CONF_DEVICES].items():
            devices[dev_id] = configured_dev[CONF_HOST]

        cloud_list = {}
        data = self.hass.data.get(DOMAIN, {})
        if DATA_CLOUD in data and data[DATA_CLOUD]:
            cloud_list = data[DATA_CLOUD].device_list

        return self.async_show_form(
            step_id="edit_device",
            data_schema=devices_schema(
                devices, cloud_list, False
            ),
            errors=errors,
        )

    async def async_step_configure_device(self, user_input=None):
        """Handle input of basic info."""
        errors = {}
        dev_id = self.selected_device
        if user_input is not None:
            try:
                self.device_data = user_input.copy()
                if dev_id is not None:
                    data = self.hass.data.get(DOMAIN, {})
                    cloud_devs = {}
                    if DATA_CLOUD in data and data[DATA_CLOUD]:
                        cloud_devs = data[DATA_CLOUD].device_list
                    
                    if dev_id in cloud_devs:
                        self.device_data[CONF_MODEL] = cloud_devs[dev_id].get(
                            CONF_PRODUCT_NAME
                        )
                if self.editing_device:
                    if user_input[CONF_ENABLE_ADD_ENTITIES]:
                        self.editing_device = False
                        user_input[CONF_DEVICE_ID] = dev_id
                        self.device_data.update(
                            {
                                CONF_DEVICE_ID: dev_id,
                                CONF_DPS_STRINGS: self.dps_strings,
                            }
                        )
                        return await self.async_step_pick_entity_type()

                    self.device_data.update(
                        {
                            CONF_DEVICE_ID: dev_id,
                            CONF_DPS_STRINGS: self.dps_strings,
                            CONF_ENTITIES: [],
                        }
                    )
                    if len(user_input[CONF_ENTITIES]) == 0:
                        return self.async_abort(
                            reason="no_entities",
                            description_placeholders={},
                        )
                    if user_input[CONF_ENTITIES]:
                        entity_ids = [
                            int(entity.split(":")[0])
                            for entity in user_input[CONF_ENTITIES]
                        ]
                        device_config = self.config_entry.data[CONF_DEVICES][dev_id]
                        self.entities = [
                            entity
                            for entity in device_config[CONF_ENTITIES]
                            if entity[CONF_ID] in entity_ids
                        ]
                        return await self.async_step_configure_entity()

                self.dps_strings = await validate_input(self.hass, user_input)
                
                # AUTOMATION: Try to detect entities automatically and skip manual steps 
                # if cloud sharing is available.
                data = self.hass.data.get(DOMAIN, {})
                cloud_sharing = data.get("sharing")
                if cloud_sharing and self.selected_device:
                    try:
                        detected_entities, dps_strings = await _detect_entities_from_datamodel(
                            cloud_sharing, self.selected_device
                        )
                        if detected_entities:
                            self.entities = detected_entities
                            self.dps_strings = dps_strings
                            # Jump straight to adding additional entities check (or finish)
                            return await self.async_step_pick_entity_type({NO_ADDITIONAL_ENTITIES: True})
                    except Exception as ex:
                        _LOGGER.warning("Auto detection failed during single device setup: %s", ex)

                return await self.async_step_pick_entity_type()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except EmptyDpsList:
                errors["base"] = "empty_dps"
            except Exception as ex:
                _LOGGER.exception("Unexpected exception: %s", ex)
                errors["base"] = "unknown"

        defaults = {}
        if self.editing_device:
            # If selected device exists as a config entry, load config from it
            defaults = self.config_entry.data[CONF_DEVICES][dev_id].copy()
            cloud_devs = {}
            if DOMAIN in self.hass.data and DATA_CLOUD in self.hass.data[DOMAIN] and self.hass.data[DOMAIN][DATA_CLOUD]:
                cloud_devs = self.hass.data[DOMAIN][DATA_CLOUD].device_list
            placeholders = {"for_device": f" for device `{dev_id}`"}
            if dev_id in cloud_devs:
                cloud_local_key = cloud_devs[dev_id].get(CONF_LOCAL_KEY)
                if defaults.get(CONF_LOCAL_KEY) != cloud_local_key and cloud_local_key:
                    _LOGGER.info(
                        "New local_key detected: new %s vs old %s",
                        cloud_local_key,
                        defaults.get(CONF_LOCAL_KEY),
                    )
                    defaults[CONF_LOCAL_KEY] = cloud_local_key
                    note = "\nNOTE: a new local_key has been retrieved using cloud API"
                    placeholders = {"for_device": f" for device `{dev_id}`.{note}"}
            defaults[CONF_ENABLE_ADD_ENTITIES] = False
            schema = schema_defaults(options_schema(self.entities), **defaults)
        else:
            defaults[CONF_PROTOCOL_VERSION] = "3.3"
            defaults[CONF_HOST] = ""
            
            # Pre-fill from local discovery
            if dev_id in self.discovered_devices:
                defaults[CONF_HOST] = self.discovered_devices[dev_id].get("ip", "")
                defaults[CONF_PROTOCOL_VERSION] = self.discovered_devices[dev_id].get("version", "3.3")
            
            # Pre-fill from Cloud Data if available
            cloud_devs = {}
            if DOMAIN in self.hass.data and DATA_CLOUD in self.hass.data[DOMAIN] and self.hass.data[DOMAIN][DATA_CLOUD]:
                cloud_devs = self.hass.data[DOMAIN][DATA_CLOUD].device_list

            if dev_id in cloud_devs:
                defaults[CONF_DEVICE_ID] = dev_id
                defaults[CONF_LOCAL_KEY] = cloud_devs[dev_id].get(CONF_LOCAL_KEY, "")
                defaults[CONF_FRIENDLY_NAME] = cloud_devs[dev_id].get("name", "")
                
                # Use cloud IP if local discovery failed to find one
                if not defaults[CONF_HOST]:
                    defaults[CONF_HOST] = cloud_devs[dev_id].get("ip", "")
            
            schema = schema_defaults(DEVICE_SCHEMA, **defaults)
            placeholders = {"for_device": f" for device `{dev_id}`"}

        return self.async_show_form(
            step_id="configure_device",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_pick_entity_type(self, user_input=None):
        """Handle asking if user wants to add another entity."""
        if user_input is not None:
            if user_input.get(NO_ADDITIONAL_ENTITIES):
                config = {
                    **self.device_data,
                    CONF_DPS_STRINGS: self.dps_strings,
                    CONF_ENTITIES: self.entities,
                }

                dev_id = self.device_data.get(CONF_DEVICE_ID)

                new_data = self.config_entry.data.copy()
                new_data[ATTR_UPDATED_AT] = str(int(time.time() * 1000))
                new_data[CONF_DEVICES].update({dev_id: config})

                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=new_data,
                )
                return self.async_create_entry(title="", data={})

            self.selected_platform = user_input[PLATFORM_TO_ADD]
            return await self.async_step_configure_entity()

        # Add a checkbox that allows bailing out from config flow if at least one
        # entity has been added
        schema = PICK_ENTITY_SCHEMA
        if self.selected_platform is not None:
            schema = schema.extend(
                {vol.Required(NO_ADDITIONAL_ENTITIES, default=True): bool}
            )

        return self.async_show_form(step_id="pick_entity_type", data_schema=schema)

    def available_dps_strings(self):
        """Return list of DPs use by the device's entities."""
        available_dps = []
        used_dps = [str(entity[CONF_ID]) for entity in self.entities]
        for dp_string in self.dps_strings:
            dp = dp_string.split(" ")[0]
            if dp not in used_dps:
                available_dps.append(dp_string)
        return available_dps

    async def async_step_entity(self, user_input=None):
        """Manage entity settings."""
        errors = {}
        if user_input is not None:
            entity = strip_dps_values(user_input, self.dps_strings)
            entity[CONF_ID] = self.current_entity[CONF_ID]
            entity[CONF_PLATFORM] = self.current_entity[CONF_PLATFORM]
            self.device_data[CONF_ENTITIES].append(entity)

            if len(self.entities) == len(self.device_data[CONF_ENTITIES]):
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    title=self.device_data[CONF_FRIENDLY_NAME],
                    data=self.device_data,
                )
                return self.async_create_entry(title="", data={})

        schema = platform_schema(
            self.current_entity[CONF_PLATFORM], self.dps_strings, allow_id=False
        )
        return self.async_show_form(
            step_id="entity",
            errors=errors,
            data_schema=schema_defaults(
                schema, self.dps_strings, **self.current_entity
            ),
            description_placeholders={
                "id": self.current_entity[CONF_ID],
                "platform": self.current_entity[CONF_PLATFORM],
            },
        )

    async def async_step_configure_entity(self, user_input=None):
        """Manage entity settings."""
        errors = {}
        if user_input is not None:
            if self.editing_device:
                entity = strip_dps_values(user_input, self.dps_strings)
                entity[CONF_ID] = self.current_entity[CONF_ID]
                entity[CONF_PLATFORM] = self.current_entity[CONF_PLATFORM]
                self.device_data[CONF_ENTITIES].append(entity)

                if len(self.entities) == len(self.device_data[CONF_ENTITIES]):
                    # finished editing device. Let's store the new config entry....
                    dev_id = self.device_data[CONF_DEVICE_ID]
                    new_data = self.config_entry.data.copy()
                    entry_id = self.config_entry.entry_id
                    # removing entities from registry (they will be recreated)
                    ent_reg = er.async_get(self.hass)
                    reg_entities = {
                        ent.unique_id: ent.entity_id
                        for ent in er.async_entries_for_config_entry(ent_reg, entry_id)
                        if dev_id in ent.unique_id
                    }
                    for entity_id in reg_entities.values():
                        ent_reg.async_remove(entity_id)

                    new_data[CONF_DEVICES][dev_id] = self.device_data
                    new_data[ATTR_UPDATED_AT] = str(int(time.time() * 1000))
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data=new_data,
                    )
                    return self.async_create_entry(title="", data={})
            else:
                user_input[CONF_PLATFORM] = self.selected_platform
                self.entities.append(strip_dps_values(user_input, self.dps_strings))
                # new entity added. Let's check if there are more left...
                user_input = None
                if len(self.available_dps_strings()) == 0:
                    user_input = {NO_ADDITIONAL_ENTITIES: True}
                return await self.async_step_pick_entity_type(user_input)

        if self.editing_device:
            schema = platform_schema(
                self.current_entity[CONF_PLATFORM], self.dps_strings, allow_id=False
            )
            schema = schema_defaults(schema, self.dps_strings, **self.current_entity)
            placeholders = {
                "entity": f"entity with DP {self.current_entity[CONF_ID]}",
                "platform": self.current_entity[CONF_PLATFORM],
            }
        else:
            available_dps = self.available_dps_strings()
            schema = platform_schema(self.selected_platform, available_dps)
            placeholders = {
                "entity": "an entity",
                "platform": self.selected_platform,
            }

        return self.async_show_form(
            step_id="configure_entity",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_yaml_import(self, user_input=None):
        """Manage YAML imports."""
        _LOGGER.error(
            "Configuration via YAML file is no longer supported by this integration."
        )
        # if user_input is not None:
        #     return self.async_create_entry(title="", data={})
        # return self.async_show_form(step_id="yaml_import")

    @property
    def current_entity(self):
        """Existing configuration for entity currently being edited."""
        return self.entities[len(self.device_data[CONF_ENTITIES])]


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class EmptyDpsList(exceptions.HomeAssistantError):
    """Error to indicate no datapoints found."""
