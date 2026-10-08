// Gang Links panel: pick a wall switch, then link each of its gangs to the
// lights or switches it should control. Talks to the gang_link integration
// over the gang_link/list and gang_link/set websocket commands.

const TARGET_DOMAINS = ["light", "switch"];
const DEVICE_KEY = "gang-link-device";

const STYLE = `
  :host {
    display: block;
    min-height: 100%;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--ha-font-family-body, Roboto, sans-serif);
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    height: var(--header-height, 56px);
    padding: 0 16px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color, #fff));
    border-bottom: var(--app-header-border-bottom, none);
    font-size: 20px;
  }
  .menu {
    background: none;
    border: 0;
    color: inherit;
    font-size: 22px;
    cursor: pointer;
    padding: 4px 8px;
  }
  .content {
    max-width: 720px;
    margin: 0 auto;
    padding: 16px;
    display: grid;
    gap: 12px;
  }
  .card {
    background: var(--card-background-color, var(--ha-card-background));
    border: 1px solid var(--divider-color);
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 16px;
    display: grid;
    gap: 12px;
  }
  .field {
    display: grid;
    gap: 6px;
    font-weight: 500;
  }
  select {
    font: inherit;
    padding: 8px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color);
    color: inherit;
  }
  .hint {
    margin: 0;
    color: var(--secondary-text-color);
    font-size: 14px;
  }
  .error {
    margin: 0;
    color: var(--error-color, #db4437);
  }
  .gang-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .gang-name {
    font-weight: 500;
    font-size: 16px;
  }
  code {
    color: var(--secondary-text-color);
    font-size: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 4px 4px 10px;
    border-radius: 16px;
    background: var(--secondary-background-color);
  }
  .remove {
    border: 0;
    background: none;
    color: var(--secondary-text-color);
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    padding: 2px 6px;
    border-radius: 50%;
  }
  .remove:hover {
    color: var(--error-color, #db4437);
  }
  .dot {
    flex: none;
    box-sizing: border-box;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--disabled-color, #9e9e9e);
  }
  .dot.on {
    background: var(--state-light-on-color, var(--amber-color, #ffc107));
  }
  .dot.unavailable,
  .dot.unknown,
  .dot.missing {
    background: transparent;
    border: 2px solid var(--error-color, #db4437);
  }
`;

// Builds a DOM element. Text goes through properties, never innerHTML.
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (value !== undefined) el[key] = value;
  }
  el.append(...children.flat().filter((child) => child != null));
  return el;
}

const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true });

class GangLinkPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._links = null;
    this._error = null;
    this._narrow = false;
    this._device = localStorage.getItem(DEVICE_KEY) || "";
  }

  set hass(hass) {
    const old = this._hass;
    this._hass = hass;
    if (!old) {
      this._render();
      this._fetch();
    } else if (
      old.devices !== hass.devices ||
      old.entities !== hass.entities ||
      old.areas !== hass.areas
    ) {
      this._render();
    } else if (old.states !== hass.states) {
      this._updateStates();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._hass) this._render();
  }

  async _fetch() {
    try {
      this._links = (await this._hass.callWS({ type: "gang_link/list" })).links;
      this._error = null;
    } catch (err) {
      this._error = `Could not load links: ${err.message || err.code || err}`;
    }
    this._render();
  }

  async _save(gang, targets) {
    try {
      this._links = (await this._hass.callWS({ type: "gang_link/set", gang, targets })).links;
      this._error = null;
    } catch (err) {
      this._error = `Could not save: ${err.message || err.code || err}`;
    }
    this._render();
  }

  _name(entityId) {
    return (
      this._hass.states[entityId]?.attributes.friendly_name ||
      this._hass.entities[entityId]?.name ||
      entityId
    );
  }

  _areaName(entityId) {
    const entity = this._hass.entities[entityId];
    const areaId = entity?.area_id || this._hass.devices[entity?.device_id]?.area_id;
    return this._hass.areas[areaId]?.name;
  }

  // Entities a user would pick: no config/diagnostic entities, nothing hidden.
  _usable(domains) {
    return Object.values(this._hass.entities).filter(
      (e) =>
        !e.entity_category && !e.hidden && domains.includes(e.entity_id.split(".")[0])
    );
  }

  _gangsOf(deviceId) {
    return this._usable(["switch"])
      .filter((e) => e.device_id === deviceId)
      .map((e) => ({ id: e.entity_id, name: this._name(e.entity_id) }))
      .sort(byName)
      .map((gang) => gang.id);
  }

  _switchDevices() {
    const ids = new Set(this._usable(["switch"]).map((e) => e.device_id).filter(Boolean));
    return [...ids]
      .map((id) => this._hass.devices[id])
      .filter(Boolean)
      .map((d) => ({ id: d.id, name: d.name_by_user || d.name || d.id }))
      .sort(byName);
  }

  _render() {
    this.shadowRoot.replaceChildren(
      h("style", { textContent: STYLE }),
      this._toolbar(),
      h("div", { className: "content" }, this._body())
    );
    this._updateStates();
  }

  _toolbar() {
    const menu = this._narrow
      ? h("button", {
          className: "menu",
          title: "Menu",
          textContent: "☰",
          onclick: () =>
            this.dispatchEvent(
              new Event("hass-toggle-menu", { bubbles: true, composed: true })
            ),
        })
      : null;
    return h("div", { className: "toolbar" }, menu, h("span", { textContent: "Gang Links" }));
  }

  _body() {
    const error = this._error ? h("p", { className: "error", textContent: this._error }) : null;
    if (!this._links) {
      return [error || h("p", { className: "hint", textContent: "Loading…" })];
    }

    const picker = h(
      "select",
      {
        onchange: (ev) => {
          this._device = ev.target.value;
          localStorage.setItem(DEVICE_KEY, this._device);
          this._render();
        },
      },
      h("option", { value: "", textContent: "Choose a switch…" }),
      this._switchDevices().map((device) => {
        const linked = this._gangsOf(device.id).filter((g) => this._links[g]).length;
        const suffix = linked ? ` · ${linked} linked` : "";
        return h("option", { value: device.id, textContent: device.name + suffix });
      })
    );
    picker.value = this._hass.devices[this._device] ? this._device : "";

    const intro = h(
      "div",
      { className: "card" },
      h("label", { className: "field" }, h("span", { textContent: "Wall switch" }), picker),
      h("p", {
        className: "hint",
        textContent:
          "Pressing a linked gang turns its devices on or off. The gang shows on while any of its devices is on. Disconnect the relay output from linked lights so they keep power.",
      }),
      error
    );

    const gangs = picker.value ? this._gangsOf(picker.value) : [];
    return [intro, ...gangs.map((gang) => this._gangCard(gang, picker.value))];
  }

  _gangCard(gang, deviceId) {
    const targets = this._links[gang] || [];

    const chips = targets.length
      ? h(
          "div",
          { className: "chips" },
          targets.map((target) =>
            h(
              "span",
              { className: "chip", title: target },
              this._dot(target),
              h("span", { textContent: this._name(target) }),
              h("button", {
                className: "remove",
                title: "Unlink",
                textContent: "×",
                onclick: () => this._save(gang, targets.filter((t) => t !== target)),
              })
            )
          )
        )
      : h("p", {
          className: "hint",
          textContent: "Not linked. The relay works as a normal switch.",
        });

    // Candidates grouped by area, skipping this switch's own gangs.
    const groups = new Map();
    for (const entity of this._usable(TARGET_DOMAINS)) {
      if (entity.device_id === deviceId || targets.includes(entity.entity_id)) continue;
      const area = this._areaName(entity.entity_id) || "No area";
      if (!groups.has(area)) groups.set(area, []);
      groups.get(area).push({ id: entity.entity_id, name: this._name(entity.entity_id) });
    }
    const areas = [...groups.keys()].sort((a, b) =>
      a === "No area" ? 1 : b === "No area" ? -1 : a.localeCompare(b)
    );

    const add = h(
      "select",
      {
        onchange: (ev) => {
          if (ev.target.value) this._save(gang, [...targets, ev.target.value]);
        },
      },
      h("option", { value: "", textContent: "+ Link a device…" }),
      areas.map((area) =>
        h(
          "optgroup",
          { label: area },
          groups
            .get(area)
            .sort(byName)
            .map((c) => h("option", { value: c.id, textContent: c.name }))
        )
      )
    );

    return h(
      "div",
      { className: "card" },
      h(
        "div",
        { className: "gang-head" },
        this._dot(gang),
        h("span", { className: "gang-name", textContent: this._name(gang) }),
        h("code", { textContent: gang })
      ),
      chips,
      add
    );
  }

  _dot(entityId) {
    return h("span", { className: "dot", dataset: { stateOf: entityId } });
  }

  // Refreshes state dots in place so open dropdowns survive state updates.
  _updateStates() {
    for (const dot of this.shadowRoot.querySelectorAll("[data-state-of]")) {
      const state = this._hass.states[dot.dataset.stateOf]?.state ?? "missing";
      dot.className = `dot ${state}`;
      dot.title = state;
    }
  }
}

if (!customElements.get("gang-link-panel")) {
  customElements.define("gang-link-panel", GangLinkPanel);
}
