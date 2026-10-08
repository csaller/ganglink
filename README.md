<img src="assets/icon.svg" width="96" height="96" alt="">

# Gang Link

A Home Assistant integration that lets the buttons (gangs) of a multi-gang
smart wall switch control smart lights without cutting their power.

Smart bulbs and LED strips need constant power. A normal wall switch cuts it,
so the bulb drops off the network and stops working from the app or voice
assistant. Gang Link keeps the lights powered and turns each gang into a remote
for one or more lights: pressing the gang tells Home Assistant to turn the
lights on or off, and the gang's state follows the lights when they change from
somewhere else.

It works with any wall switch whose gangs show up in Home Assistant as `switch.*`
entities (Matter, Zigbee, Z-Wave, Wi-Fi, ...). Targets can be any `light.*` or
`switch.*` entity.

## Wiring

1. Connect the lights to permanent live, not to the switch's relay outputs.
2. Leave the relay outputs of linked gangs disconnected. The relay still clicks
   when you press the gang, but it switches nothing.
3. The switch itself still needs its normal supply (and neutral, if the model
   requires one).

> [!WARNING]
> This means changing mains wiring. Turn off the circuit breaker before you
> open the switch box, follow your local electrical code, and have a qualified
> electrician do the work if you are not sure.

## Install

Requires Home Assistant 2024.7 or newer.

### HACS

1. In HACS, open the menu (⋮) and choose **Custom repositories**.
2. Add `https://github.com/csaller/ganglink` with type **Integration**.
3. Search for **Gang Link**, download it, and restart Home Assistant.

### Manual

Copy `custom_components/gang_link` into the `custom_components` folder of your
Home Assistant config directory and restart Home Assistant.

## Setup

Go to **Settings → Devices & services → Add integration**, search for
**Gang Link**, and confirm. There is nothing to configure. A **Gang Links**
entry appears in the sidebar for admin users.

## Using the panel

1. Open **Gang Links** in the sidebar.
2. Choose the wall switch.
3. For each gang, pick the lights or switches it should control from
   **+ Link a device…**. Remove a target with **×**. A gang with no targets
   goes back to being a normal switch.

Each gang and target shows a dot with its current state: amber is on, grey is
off, a red ring means unavailable or missing.

Links are stored in `.storage/gang_link` and survive restarts, updates, and
removing and re-adding the integration.

## How syncing works

- **Press:** turning a linked gang on or off turns all its targets on or off.
- **Follow:** when a target changes from somewhere else (app, voice,
  automation), the gang shows on while any target is on and off when all are
  off.
- **New link:** the gang adopts the targets' current state. Linking never
  switches your lights.
- **Gang comes back:** after a power cut or Home Assistant restart, a gang that
  was unavailable adopts the targets' state instead of driving them. A switch
  that boots in the "off" state does not turn your lights off.
- **Hold after press:** for 5 seconds after a press, Gang Link ignores target
  updates, so lights that report their new state one by one don't bounce the
  gang back.
- **Settle:** target updates are batched for 1 second before the gang is
  synced.

## Limitations

- The buttons only work while Home Assistant and the switch's network (Wi-Fi,
  Thread, Zigbee, ...) are up. If either is down, pressing a linked gang does
  nothing. Keep another way to control the lights, such as a physical switch
  or the bulbs' own app.
- Links are keyed by entity ID. If you rename a gang or target entity ID, its
  link breaks; link it again in the panel.
- Each gang is a single on/off control. Gang Link does not do dimming, scenes,
  or multi-press actions.

## Upgrading from the YAML version

Early versions were enabled by a `gang_link:` key in `configuration.yaml`.
That key is no longer used. Remove it and add the integration from the UI as
described above. Your links are kept. Until you remove the key, Home Assistant
logs an error and shows a repair notice, but everything else keeps working.

## Development

```bash
uv venv -p 3.14 && source .venv/bin/activate
uv pip install -r requirements_test.txt
pytest
```

## License

[MIT](LICENSE)
