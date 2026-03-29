# Tuya Local for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

A Home Assistant custom integration to support devices running Tuya firmware locally, without going via the Tuya cloud. It improves speed and reliability while unlocking features that might not be available through the cloud API.

## 🌟 Features

- **Local Control:** Communicate directly with devices over WiFi or through hubs.
- **Sub-device Support:** Enhanced handling for devices functioning through gateways.
- **Remote Entities:** Support for IR remotes through native remote entities.
- **Flexible Configuration:**
  - **Auto-configure:** Uses Cloud API to automate setup (requires account).
  - **Manual Setup:** Fully functional without any cloud dependency.
- **Device Discovery:** Automatically detects Tuya devices on your local network.
- **Matter Support:** Compatibility with Tuya hubs that support Matter over WiFi.

## 📦 Installation

Installation is easiest via the **Home Assistant Community Store (HACS)**:

1. Open HACS in your Home Assistant instance.
2. Search for `Tuya Local`.
3. Click **Install**.
4. Restart Home Assistant.

## ⚙️ Configuration

Go to **Settings > Devices & Services** and click **Add Integration**. Search for **Tuya Local**.

### Choose Your Path
1. **Cloud Assisted (Recommended):** Log in to the Tuya/SmartLife app to retrieve local keys and device IDs automatically. The token expires in a few hours and is not saved for security.
2. **Manual Configuration:** Provide all details manually (see `DEVICES_DETAILS.md` for instructions).

### Setup Stages

#### Stage One: Technical details
- **Host:** IP address or hostname of the device.
- **Device ID:** The unique ID for the device.
- **Local Key:** Retrieved from the cloud or manual tools. *Note: Re-pairing changes the key.*
- **Protocol Version:** Options include `auto`, 3.1, 3.2, 3.3, 3.4, 3.5, 3.22. Use `auto` or experiment if commands fail.

#### Stage Two: Device Selection
Select the device type from a filtered list of matches. Over 1000+ devices are supported. If the wrong type is selected, you must delete and re-add the device.

#### Stage Three: Customization
Choose a unique **Name** for the device, which will serve as the base for all associated entities.

## 🔗 Special Connections

### Connecting via Hubs
For devices behind a hub (e.g., zigbee water timers):
- **Device ID:** Use the hub's ID.
- **Host:** Use the hub's IP.
- **Local Key:** Use the hub's key.
- **Sub-device ID (node_id):** Required for the specific sub-device.

### Secure Locks
Supports standard BLE lock models. Unlocking may require capturing an 8-digit numeric code from the Tuya developer portal (under DPs 60/61).

## ⚠️ Known Issues & Limitations

- **Cloud Status:** Devices still send status to Tuya servers; this is not a security measure, but a performance and reliability boost.
- **Single Connection:** Most Tuya devices support only **one** local connection. Ensure other integrations or apps are closed.
- **Battery Devices:** WiFi-only battery devices (sensors, smoke alarms) cannot be supported locally due to power management.
- **Rate Limiting:** Avoid sending multiple commands in quick succession. Add delays in automations to prevent device rebooting or going offline.
- **Offline Operation:** Some devices stop responding if blocked from Tuya servers for too long. Blocking DNS can sometimes help.

## 🤝 Contributing & Support

Feel free to raise pull requests or report issues.
- **New Devices:** Please include model, brand, and datapoints (dps) when requesting support.
- **Unit Tests:** Help improve coverage for Python code.
- **Discovery:** Contributions to improve background discovery are welcome.

*Credits: Many contributors have helped build and maintain this integration. If your device isn't supported, check out `localtuya` as an alternative.*
