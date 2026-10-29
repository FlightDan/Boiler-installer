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
    navigator: { language },
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

const setting = (ui, key) => ui.find(key);
const field = (ui, key) => setting(ui, key).children[0];
const help = (ui, key) => setting(ui, key).children[1];
const helpText = (ui, key) => help(ui, key).children.join('');
const slider = ui => field(ui, 'sleep-slider');
const chargeModeSlider = ui => field(ui, 'charge-mode');
const selected = ui => ({ value: slider(ui).props.value, label: slider(ui).props.renderValue(slider(ui).props.value) });
const verifyGroup = (ui, key) => {
  const container = setting(ui, key), control = field(ui, key), description = help(ui, key);
  assert.equal(container.props.style.display, 'flex');
  assert.equal(container.props.style.flexDirection, 'column');
  assert.equal(container.props.style.gap, 4);
  assert.equal(control.props.showValue, true);
  assert.equal(control.props.label.type, 'strong', 'setting title uses the shared bold heading style');
  assert.equal(control.props['aria-describedby'], description.props.id, 'slider points to its help text below');
  assert.equal(description.props.style.fontSize, 12);
  assert.equal(description.props.style.lineHeight, '18px');
  assert.equal(description.props.style.minHeight, 36);
  assert.equal(description.props.style.whiteSpace, 'pre-line');
  assert.equal(description.props.style.opacity, 0.85);
};
const labels = {
  'en-US': ['Immediately', '20 min', '1 hour', '2 hours', '12 hours', '24 hours', 'Never'],
  'zh-CN': ['直接进入', '20 分钟', '1 小时', '2 小时', '12 小时', '24 小时', '永不进入'],
};

for (const language of ['zh-CN', 'en-US']) {
  const ui = harness(language);
  await ui.mount();
  assert.deepEqual(selected(ui), {
    value: 6,
    label: language === 'zh-CN' ? '跟随系统' : 'System default',
  }, 'system default is displayed without changing the saved setting');
  assert.equal(slider(ui).props.min, 0);
  assert.equal(slider(ui).props.max, 6);
  assert.equal(slider(ui).props.step, 1);
  assert.equal(slider(ui).props.notchCount, 7);
  assert.equal(ui.calls.length, 0, 'initial system status must not write a preference or start sleep');
  assert.equal(slider(ui).props.renderValue(6), selected(ui).label,
    'the accessible current value must describe System default, not Never');
  for (const key of ['limit', 'charge-mode', 'sleep-slider']) verifyGroup(ui, key);
  assert.equal(help(ui, 'limit').props.id, 'battery-limit-help');
  assert.equal(help(ui, 'charge-mode').props.id, 'battery-charge-mode-help');
  assert.equal(help(ui, 'sleep-slider').props.id, 'battery-sleep-slider-help');

  const timed = harness(language, { ...initial, mode: 'auto', delay_minutes: 120 });
  await timed.mount();
  const timedHelp = helpText(timed, 'sleep-slider');
  assert(timedHelp.includes(language === 'zh-CN'
    ? '电池供电时计时转入 S4。\n接电时保持快速待机。'
    : 'Timed on battery power.\nOn AC, keep fast sleep in memory.'));
}

const positions = [
  { mode: 'hibernate', delay_minutes: 120, position: 0 },
  { mode: 'auto', delay_minutes: 20, position: 1 },
  { mode: 'auto', delay_minutes: 60, position: 2 },
  { mode: 'auto', delay_minutes: 120, position: 3 },
  { mode: 'auto', delay_minutes: 720, position: 4 },
  { mode: 'auto', delay_minutes: 1440, position: 5 },
  { mode: 's2idle', delay_minutes: 120, position: 6 },
];
for (const language of ['zh-CN', 'en-US']) {
  for (const item of positions) {
    const ui = harness(language, { ...initial, mode: item.mode, delay_minutes: item.delay_minutes });
    await ui.mount();
    assert.deepEqual(selected(ui), { value: item.position, label: labels[language][item.position] },
      `${language} maps ${item.mode}/${item.delay_minutes} to slider notch ${item.position}`);
    assert.equal(ui.calls.length, 0, 'rendering a stored selection must not write');
    assert.equal(slider(ui).props.renderValue(item.position), labels[language][item.position]);
  }
}

const chargeModes = [
  {
    mode: 'auto', position: 0,
    label: { 'en-US': 'Charge', 'zh-CN': '正常充电' },
    help: { 'en-US': 'Allow charging up to the limit above.', 'zh-CN': '允许充电，遵循上方上限。' },
  },
  {
    mode: 'inhibit-charge-awake', position: 1,
    label: { 'en-US': 'Bypass: awake', 'zh-CN': '开机旁路' },
    help: { 'en-US': 'Stop charging while awake.\nAllow charging during sleep.', 'zh-CN': '开机时停止充电。\n待机时允许充电。' },
  },
  {
    mode: 'inhibit-charge', position: 2,
    label: { 'en-US': 'Bypass: all', 'zh-CN': '全程旁路' },
    help: { 'en-US': 'Stop charging while awake and asleep.\nChoose Charge to charge again.', 'zh-CN': '开机、待机均停止充电。\n需要充电时切回正常充电。' },
  },
];
for (const language of ['zh-CN', 'en-US']) {
  for (const item of chargeModes) {
    const ui = harness(language, initial, false, { mode: item.mode });
    await ui.mount();
    const control = chargeModeSlider(ui);
    assert.equal(control.props.value, item.position);
    assert.equal(control.props.renderValue(item.position), item.label[language]);
    assert.equal(helpText(ui, 'charge-mode'), item.help[language]);
    verifyGroup(ui, 'charge-mode');
    assert.equal(ui.calls.length, 0, 'rendering stored charging modes must not write');
  }
}

for (const item of chargeModes) {
  const ui = harness('en-US');
  await ui.mount();
  chargeModeSlider(ui).props.onChangeComplete(item.position);
  await ui.advance(0);
  assert.deepEqual(ui.calls, [['set_mode', item.mode]], `charging notch ${item.position} maps to ${item.mode}`);
  assert.equal(chargeModeSlider(ui).props.value, item.position);
}

const chargeDrag = harness('en-US');
await chargeDrag.mount();
chargeModeSlider(chargeDrag).props.onChangeStart();
for (const position of [2, 0, 1]) {
  chargeModeSlider(chargeDrag).props.onChange(position);
  chargeDrag.render();
  assert.equal(chargeDrag.calls.length, 0, 'charging mode drag must not save before completion');
}
assert.equal(chargeModeSlider(chargeDrag).props.value, 1);
assert(helpText(chargeDrag, 'charge-mode').includes('Finish adjusting to save'));
chargeModeSlider(chargeDrag).props.onChangeComplete(1);
await chargeDrag.advance(0);
assert.deepEqual(chargeDrag.calls, [['set_mode', 'inhibit-charge-awake']], 'only the final charging notch is written');
assert.equal(chargeModeSlider(chargeDrag).props.renderValue(1), 'Bypass: awake');

const failedChargeMode = harness('en-US');
await failedChargeMode.mount();
failedChargeMode.fail();
chargeModeSlider(failedChargeMode).props.onChangeComplete(2);
await failedChargeMode.advance(0);
assert.deepEqual(failedChargeMode.calls, [['set_mode', 'inhibit-charge']]);
assert.equal(chargeModeSlider(failedChargeMode).props.value, 0, 'failed charging save returns to the confirmed notch');
assert.equal(chargeModeSlider(failedChargeMode).props.renderValue(0), 'Charge');
assert.equal(helpText(failedChargeMode, 'charge-mode'), 'Allow charging up to the limit above.');
assert(failedChargeMode.find('error').children.join('').includes('Could not save charging mode'));

const modeDraft = harness('en-US');
await modeDraft.mount();
chargeModeSlider(modeDraft).props.onChangeStart();
chargeModeSlider(modeDraft).props.onChange(2);
modeDraft.render();
assert.equal(slider(modeDraft).props.disabled, true, 'sleep is locked while the charging-mode draft is active');
assert.equal(modeDraft.calls.length, 0);
chargeModeSlider(modeDraft).props.onChangeComplete(2);
await modeDraft.advance(0);
assert.deepEqual(modeDraft.calls, [['set_mode', 'inhibit-charge']]);
assert.deepEqual(selected(modeDraft), { value: 6, label: 'System default' }, 'mode save leaves sleep preference unchanged');

const sleepDraft = harness('en-US');
await sleepDraft.mount();
slider(sleepDraft).props.onChangeStart();
slider(sleepDraft).props.onChange(4);
sleepDraft.render();
assert.equal(chargeModeSlider(sleepDraft).props.disabled, true, 'charging mode is locked while the sleep draft is active');
assert.equal(sleepDraft.calls.length, 0);
slider(sleepDraft).props.onChangeComplete(4);
await sleepDraft.advance(0);
assert.deepEqual(sleepDraft.calls, [['set_sleep_policy', 'auto', 720]]);
assert.equal(chargeModeSlider(sleepDraft).props.value, 0, 'sleep save leaves charging mode unchanged');

// A physical drag may pass several notches. SliderField completion saves only its last value.
const pointer = harness('en-US');
await pointer.mount();
slider(pointer).props.onChangeStart();
for (const position of [0, 1, 2, 3]) {
  slider(pointer).props.onChange(position);
  pointer.render();
  assert.equal(pointer.calls.length, 0, 'pointer movement must not save before release');
}
assert.deepEqual(selected(pointer), { value: 3, label: '2 hours' });
assert(helpText(pointer, 'sleep-slider').includes('Finish adjusting to save'));
slider(pointer).props.onChangeComplete(3);
await pointer.advance(0);
assert.deepEqual(pointer.calls, [['set_sleep_policy', 'auto', 120]], 'only the final two-hour notch is saved');
assert.deepEqual(selected(pointer), { value: 3, label: '2 hours' });

// Controller-like input follows start → change → complete and saves once at completion.
const controller = harness('en-US');
await controller.mount();
slider(controller).props.onChangeStart();
slider(controller).props.onChange(3);
controller.render();
assert.equal(controller.calls.length, 0, 'controller change does not save before completion');
slider(controller).props.onChangeComplete(3);
await controller.advance(0);
assert.deepEqual(controller.calls, [['set_sleep_policy', 'auto', 120]]);

// Completing at the same visible position is an explicit user choice and still writes.
const sameSystemPosition = harness('en-US');
await sameSystemPosition.mount();
assert.deepEqual(selected(sameSystemPosition), { value: 6, label: 'System default' });
slider(sameSystemPosition).props.onChangeStart();
slider(sameSystemPosition).props.onChange(6);
sameSystemPosition.render();
slider(sameSystemPosition).props.onChangeComplete(6);
await sameSystemPosition.advance(0);
assert.deepEqual(sameSystemPosition.calls, [['set_sleep_policy', 's2idle', 120]],
  'completing at System default notch 6 explicitly selects never/S2idle');

const legacy30 = harness('en-US', { ...initial, mode: 'auto', delay_minutes: 30 });
await legacy30.mount();
assert.deepEqual(selected(legacy30), { value: 1, label: '30 min' }, 'legacy delay remains visible until user input');
assert.equal(slider(legacy30).props.renderValue(1), '30 min', 'assistive text preserves the actual legacy delay');
slider(legacy30).props.onChangeStart();
slider(legacy30).props.onChange(1);
legacy30.render();
slider(legacy30).props.onChangeComplete(1);
await legacy30.advance(0);
assert.deepEqual(legacy30.calls, [['set_sleep_policy', 'auto', 20]],
  'completing at legacy delay position 1 migrates it to the selected 20-minute notch');

// An onChange with no start callback (such as a keyboard edit) still uses the 350ms fallback.
const keyboard = harness('en-US');
await keyboard.mount();
slider(keyboard).props.onChange(5);
keyboard.render();
assert.equal(keyboard.nextTimerDelay(), 350);
await keyboard.advance(349);
assert.equal(keyboard.calls.length, 0, 'keyboard selection waits for the debounce');
await keyboard.advance(1);
assert.deepEqual(keyboard.calls, [['set_sleep_policy', 'auto', 1440]]);

for (const [position, minutes] of [[1, 20], [4, 720], [5, 1440]]) {
  const ui = harness('en-US');
  await ui.mount();
  slider(ui).props.onChange(position);
  await ui.advance(350);
  assert.deepEqual(ui.calls, [['set_sleep_policy', 'auto', minutes]], `${minutes}-minute notch maps to its exact RPC`);
}

// Both endpoint choices keep the last delay in the policy sent to the backend.
for (const [position, mode] of [[0, 'hibernate'], [6, 's2idle']]) {
  const ui = harness('en-US', { ...initial, mode: 'auto', delay_minutes: 720 });
  await ui.mount();
  slider(ui).props.onChange(position);
  await ui.advance(350);
  assert.deepEqual(ui.calls, [['set_sleep_policy', mode, 720]], `${mode} endpoint preserves 12-hour delay`);
}

// A rejected save returns the view to the last confirmed policy; later polling keeps it there.
const failedSave = harness('en-US', { ...initial, mode: 'auto', delay_minutes: 120 });
await failedSave.mount();
failedSave.fail();
slider(failedSave).props.onChange(4);
await failedSave.advance(350);
assert.deepEqual(failedSave.calls, [['set_sleep_policy', 'auto', 720]]);
assert.deepEqual(selected(failedSave), { value: 3, label: '2 hours' });
assert(failedSave.find('error').children.join('').includes('Could not save'));
await failedSave.poll();
assert.deepEqual(selected(failedSave), { value: 3, label: '2 hours' }, 'failed save polling must retain old selection');
assert(failedSave.find('error').children.join('').includes('Could not save'));

// Polling cannot replace an active draft. After save, polling reads and keeps the new confirmed selection.
const polling = harness('en-US', { ...initial, mode: 'auto', delay_minutes: 120 });
await polling.mount();
slider(polling).props.onChange(4);
polling.render();
await polling.poll();
assert.deepEqual(selected(polling), { value: 4, label: '12 hours' }, 'polling must not overwrite a draft');
assert.equal(polling.calls.length, 0);
await polling.advance(350);
assert.deepEqual(polling.calls, [['set_sleep_policy', 'auto', 720]]);
assert.deepEqual(selected(polling), { value: 4, label: '12 hours' });
await polling.poll();
assert.deepEqual(selected(polling), { value: 4, label: '12 hours' }, 'polling must retain a successful selection');

// Sleep integration and each required mode capability gate the slider independently of charging.
const missingIntegration = harness('en-US', { ...initial, router_installed: false, ready: false });
await missingIntegration.mount();
assert.equal(slider(missingIntegration).props.disabled, true);
assert.equal(missingIntegration.calls.length, 0);

for (const language of ['zh-CN', 'en-US']) {
  for (const missingMode of ['s2idle', 'hibernate', 'auto']) {
    const status = { ...initial, available_modes: initial.available_modes.filter(mode => mode !== missingMode) };
    const unsupported = harness(language, status);
    await unsupported.mount();
    assert.equal(slider(unsupported).props.disabled, true, `missing ${missingMode} disables S4 scheduling`);
    const note = helpText(unsupported, 'sleep-slider');
    assert(note.includes(language === 'zh-CN' ? '此设备不支持 S4 定时待机。' : 'S4 scheduling is unavailable on this device.'));
    assert.equal(unsupported.calls.length, 0);
  }
}

const unavailableStatus = harness('en-US', null);
await unavailableStatus.mount();
assert.equal(slider(unavailableStatus).props.disabled, true);
assert.equal(unavailableStatus.calls.length, 0);
assert(unavailableStatus.find('error'));
unavailableStatus.recover();
await unavailableStatus.poll();
assert.equal(unavailableStatus.find('error'), undefined, 'recovered status polling clears the read error');

for (const chargeState of [
  { batteryFailure: true, batteryOverride: {} },
  { batteryFailure: false, batteryOverride: { charge_control_available: false } },
]) {
  const independent = harness('en-US', initial, chargeState.batteryFailure, chargeState.batteryOverride);
  await independent.mount();
  if (!chargeState.batteryFailure) assert(independent.find('charge-unavailable'));
  assert.equal(slider(independent).props.disabled, false, 'charge-control support must not disable sleep control');
  slider(independent).props.onChange(1);
  await independent.advance(350);
  assert.deepEqual(independent.calls, [['set_sleep_policy', 'auto', 20]]);
}

console.log('Passed: bilingual seven-notch mapping, SliderField start/change/complete, same-position completion, keyboard fallback, exact delay RPCs, endpoint preservation, failure readback, polling, and independent capabilities.');
