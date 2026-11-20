import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('./battery-plugin-index.js', import.meta.url), 'utf8');
const initial = {
  mode: 'system', delay_minutes: 120,
  available_modes: ['system', 's2idle', 'hibernate', 'auto'],
  router_installed: true, ready: true,
  delay_options: [20, 60, 120, 720, 1440], hibernate_on_ac: false,
};

function harness(language = 'zh-CN', sleepStatus = initial, batteryFailure = false, batteryOverride = {}) {
  const slots = [], refs = [], effects = [], calls = [], listeners = new Map(), timers = new Map();
  let stateIndex = 0, refIndex = 0, mounted = false, tree, failSave = false, interval;
  let nextTimer = 0, now = 0;
  const battery = { limit: 80, mode: 'auto', capacity: 80, status: 'Full', ac: true, ...batteryOverride };
  let sleep = sleepStatus === null ? null : structuredClone(sleepStatus);
  const R = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState: value => {
      const index = stateIndex++;
      if (!(index in slots)) slots[index] = value;
      return [slots[index], next => { slots[index] = next; }];
    },
    useRef: value => {
      const index = refIndex++;
      return refs[index] || (refs[index] = { current: value });
    },
    useEffect: fn => { if (!mounted) effects.push(fn); },
  };
  const context = vm.createContext({
    navigator: { language }, URLSearchParams,
    setTimeout: (fn, delay = 0) => {
      const id = ++nextTimer;
      timers.set(id, { fn, due: now + Number(delay || 0) });
      return id;
    },
    clearTimeout: id => { timers.delete(id); },
    setInterval: fn => { interval = fn; return 2; },
    clearInterval: () => {},
    window: {
      SP_REACT: R,
      DFL: { PanelSection: 'section', PanelSectionRow: 'row', SliderField: 'slider', DropdownItem: 'dropdown', ButtonItem: 'button' },
      addEventListener: (name, fn) => listeners.set(name, fn),
      removeEventListener: name => listeners.delete(name),
      __DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit: {
        connect: () => ({ callable: name => async (...args) => {
          if (name.startsWith('set_')) calls.push([name, ...args]);
          if (name === 'get_status') {
            if (batteryFailure) throw Error('Battery charge controls unavailable');
            return battery;
          }
          if (name === 'get_sleep_status') {
            if (sleep === null) throw Error('Sleep status unavailable');
            return sleep;
          }
          if (name === 'set_sleep_policy') {
            if (failSave) throw Error('Could not save sleep policy');
            sleep = { ...sleep, mode: args[0], delay_minutes: args[1] };
            return sleep;
          }
          if (name === 'set_mode') {
            if (failSave) throw Error('Could not save charging mode');
            battery.mode = args[0];
            return battery;
          }
          return battery;
        } }),
      },
    },
  });
  vm.runInContext(source.replace(/export default\s*/, 'globalThis.plugin='), context);

  const render = () => {
    stateIndex = 0; refIndex = 0;
    tree = context.plugin().content.type();
    return tree;
  };
  const find = (key, node = tree) => {
    if (Array.isArray(node)) {
      for (const child of node) {
        const found = find(key, child);
        if (found !== undefined) return found;
      }
      return undefined;
    }
    if (!node || typeof node !== 'object') return undefined;
    if (node.props?.key === key) return node.children?.[0] ?? node;
    return find(key, node.children || []);
  };
  const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  const advance = async milliseconds => {
    now += milliseconds;
    while (true) {
      const next = [...timers.entries()].sort((a, b) => a[1].due - b[1].due)[0];
      if (!next || next[1].due > now) break;
      timers.delete(next[0]);
      next[1].fn();
      await settle();
    }
    render();
  };
  return {
    calls, slots, render, find,
    fail: () => { failSave = true; },
    recover: () => { sleep = structuredClone(sleepStatus ?? initial); batteryFailure = false; },
    dispatch: name => listeners.get(name)?.(),
    nextTimerDelay: () => {
      const next = [...timers.values()].sort((a, b) => a.due - b.due)[0];
      return next ? next.due - now : undefined;
    },
    async advance(milliseconds) { await advance(milliseconds); },
    async poll() { await interval(); render(); },
    async mount() {
      render(); effects.forEach(fn => fn()); mounted = true;
      await settle(); render();
    },
  };
}


const field = (ui, key) => ui.find(key).children[0];
const expectedCharge = {'en-US':'Charge','zh-CN':'正常充电','zh-TW':'正常充電','ja':'充電','ko':'충전','de':'Laden','fr':'Charger','es':'Cargar'};
for (const language of Object.keys(expectedCharge)) {
  const ui = harness(language); await ui.mount();
  assert(ui.find('state')); assert(ui.find('limit')); assert(ui.find('charge-mode'));
  assert.equal(ui.find('sleep-slider'), undefined);
  assert.equal(field(ui,'limit').props.value,80);
  assert.equal(field(ui,'charge-mode').props.value,0);
  assert.equal(field(ui,'charge-mode').props.renderValue(0), expectedCharge[language]);
  assert.equal(ui.calls.length,0);
}
for (const [position, mode] of [[0,'auto'],[1,'inhibit-charge-awake'],[2,'inhibit-charge']]) {
  const ui=harness('en-US'); await ui.mount();
  field(ui,'charge-mode').props.onChangeStart(); field(ui,'charge-mode').props.onChange(position);
  assert.equal(ui.calls.length,0); field(ui,'charge-mode').props.onChangeComplete(position); await ui.advance(0);
  assert.deepEqual(ui.calls,[['set_mode',mode]]);
}
const limit=harness('en-US'); await limit.mount(); field(limit,'limit').props.onChangeStart();
field(limit,'limit').props.onChange(70); field(limit,'limit').props.onChange(75); assert.equal(limit.calls.length,0);
field(limit,'limit').props.onChangeComplete(75); await limit.advance(999); assert.equal(limit.calls.length,0);
await limit.advance(1); assert.deepEqual(limit.calls,[['set_limit',75]]);
const readonly=harness('en-US',initial,false,{charge_control_available:false}); await readonly.mount();
assert(readonly.find('charge-unavailable')); assert.equal(readonly.find('limit'),undefined); assert.equal(readonly.find('charge-mode'),undefined);
assert(!/get_sleep_status|set_sleep_policy|sleep-slider|S4/.test(source));
console.log('Passed: eight locales, telemetry, charge modes, debounced limit and telemetry-only capability.');
