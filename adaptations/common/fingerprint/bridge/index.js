// Derived from podothecat/decky-fingerprint-unlock, MIT; see upstream/LICENSE.
// Decoration only; callers provide geometry and color, never PIN state.
let rendererId = 0;
export function createFingerprintStatusRenderer(getDocument, getLayout) {
  let node = null, halo = null, documentOwner = null, animation = null, lastState = null;
  const states = ['waiting', 'retrying', 'no-match', 'success', 'unavailable'];
  function cancelAnimation() { if (animation) { animation.cancel(); animation = null; } }
  function remove() {
    cancelAnimation();
    try { if (node && node.parentNode) node.parentNode.removeChild(node); } catch (_) {}
    node = null; halo = null; documentOwner = null; lastState = null;
  }
  function render(state) {
    try {
      const doc = getDocument(), layout = doc && getLayout(doc), view = doc?.defaultView;
      const i = layout?.indicators, dot = layout?.digit, blocked = layout?.blocked;
      const valid = r => r && ['top','bottom','left','right'].every(k => Number.isFinite(r[k])) && r.right > r.left && r.bottom > r.top;
      if (!states.includes(state) || !doc?.body || !view || !valid(i) || !valid(dot) || !Array.isArray(blocked) || !blocked.length || !blocked.every(valid) ||
          !Number.isFinite(view.innerWidth) || !Number.isFinite(view.innerHeight)) { remove(); return false; }
      const size = Math.max(56, Math.min(64, (dot.right - dot.left) * 2.5));
      const x = (i.left + i.right - size) / 2, y = i.bottom + 30;
      const motionMargin = state === 'no-match' ? 5 : 0;
      const box = {left:x-motionMargin, top:y, right:x+size+motionMargin, bottom:y+size};
      if (box.left < 0 || y < 0 || box.right > view.innerWidth || box.bottom > view.innerHeight ||
          blocked.some(r => box.left < r.right && box.right > r.left && box.top < r.bottom && box.bottom > r.top)) { remove(); return false; }
      if (node && (documentOwner !== doc || !doc.body.contains(node))) remove();
      if (!node) {
        node = doc.createElementNS('http://www.w3.org/2000/svg', 'svg'); documentOwner = doc;
        node.setAttribute('viewBox','0 0 64 64'); node.setAttribute('aria-hidden','true');
        Object.assign(node.style, {position:'fixed', zIndex:'7000', pointerEvents:'none', fill:'none', stroke:'currentColor', strokeWidth:'2.8', strokeLinecap:'round', strokeLinejoin:'round'});
        const id = `boiler-fingerprint-glow-${++rendererId}`;
        const defs = doc.createElementNS('http://www.w3.org/2000/svg', 'defs');
        const gradient = doc.createElementNS('http://www.w3.org/2000/svg', 'radialGradient');
        gradient.setAttribute('id', id);
        for (const [offset, opacity] of [['0%', '1'], ['100%', '0']]) {
          const stop = doc.createElementNS('http://www.w3.org/2000/svg', 'stop');
          stop.setAttribute('offset', offset); stop.setAttribute('stop-color', '#78b985'); stop.setAttribute('stop-opacity', opacity); gradient.appendChild(stop);
        }
        defs.appendChild(gradient); node.appendChild(defs);
        halo = doc.createElementNS('http://www.w3.org/2000/svg', 'circle');
        halo.setAttribute('cx','32'); halo.setAttribute('cy','32'); halo.setAttribute('r','25');
        halo.setAttribute('fill', `url(#${id})`); halo.setAttribute('stroke','none');
        Object.assign(halo.style, {opacity:'0', transformOrigin:'32px 32px'}); node.appendChild(halo);
        for (const d of ['M13 31c0-11 8-19 19-19s19 8 19 19', 'M19 32c0-8 5-14 13-14s13 6 13 14c0 9 1 15 4 20', 'M25 32c0-5 3-8 7-8s7 3 7 8c0 12 1 20 4 25', 'M32 30c1 0 1 2 1 4 0 12 0 20 3 25', 'M19 38c0 7-1 12-4 17', 'M25 38c0 10-1 15-3 19', 'M51 37c0 4 1 7 2 10']) {
          const path = doc.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); node.appendChild(path);
        }
        doc.body.appendChild(node);
      }
      node.style.left = `${x}px`; node.style.top = `${y}px`; node.style.width = `${size}px`; node.style.height = `${size}px`;
      node.style.color = state === 'no-match' ? '#cf6b6b' : state === 'success' ? '#78b985' : (layout.color || '#68747e');
      if (state !== lastState) {
        cancelAnimation();
        const reduce = view.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        if (!reduce && state === 'no-match' && typeof node.animate === 'function')
          animation = node.animate([{transform:'translateX(0)'},{transform:'translateX(-5px)'},{transform:'translateX(5px)'},{transform:'translateX(-2px)'},{transform:'translateX(0)'}], {duration:340, easing:'ease-out'});
        if (!reduce && state === 'success' && typeof halo.animate === 'function')
          animation = halo.animate([{opacity:0,transform:'scale(.7)'},{opacity:.22,transform:'scale(1)'},{opacity:0,transform:'scale(1.25)'}], {duration:600,easing:'ease-out',iterations:Infinity});
        lastState = state;
      }
      return true;
    } catch (_) { remove(); return false; }
  }
  function setOpacity(value) { if (node) node.style.opacity = String(value); }
  return {render, remove, setOpacity};
}

// Prefer Steam's observed document language; SP URL and navigator are fallbacks.
export function fingerprintLanguage(value) {
  const language = String(value || '').trim().toLowerCase().replaceAll('_', '-');
  return language === 'schinese' || language === 'tchinese' || language === 'zh' || language.startsWith('zh-') ? 'zh' : 'en';
}
function steamLanguage() {
  try {
    const lang = globalThis.document?.documentElement?.lang;
    if (lang) return fingerprintLanguage(lang);
  } catch (_) {}
  try {
    const params = new URLSearchParams(DFL.findSP()?.location?.search || '');
    for (const [key, value] of params) {
      if (key.toUpperCase() === 'LANGUAGE' && value) return fingerprintLanguage(value);
    }
  } catch (_) {}
  try { return fingerprintLanguage(globalThis.navigator?.language); } catch (_) { return 'en'; }
}
const panelText = {
  en: {
    title: 'Fingerprint', toggle: 'Fingerprint unlock', description: 'Experimental',
    list: 'Fingerprints', add: 'Add fingerprint', print: 'Fingerprint', cancel: 'Cancel',
    identifying: 'Complete system authentication, then scan an unenrolled finger.',
    center: 'Touch the center of your finger. Lift and touch again.',
    edge: 'Lightly touch with the same finger. Lift and touch again.',
    edgeTitle: 'Change your grip: scan the fingertip edge',
    press: 'Touch the sensor with the same finger. Lift and touch again.',
    swipe: 'Swipe the same finger across the sensor again.',
    scan: 'Scan the same finger again.',
    retrySwipe: 'Scan could not be used. Swipe the same finger again.',
    retryScan: 'Scan could not be used. Scan the same finger again.',
    retry: 'Scan could not be used. Lift, shift slightly, and touch again.', complete: 'Fingerprint added.',
    starting: 'Starting enrollment…', busy: 'The reader is busy. Try again.',
    full: 'Fingerprint limit reached (6).', failed: 'Enrollment stopped. Try again.',
    repair:'Reinstall fingerprint support', repairing:'Installing…', repairFailed:'Failed',
    errors: {
      list: 'Could not read saved fingerprints.',
      agent: 'Could not open the system authentication prompt.',
      authorization: 'System authentication was denied or cancelled.',
      duplicate: 'This finger is already saved. Use a different finger.',
      capture: 'Enrollment did not complete. Try again.',
      'retry-timeout': 'No new scan was received. Enrollment ended. You can add a fingerprint again.',
      session: 'Could not start enrollment in this Steam session.'
    }
  },
  zh: {
    title: '指纹', toggle: '指纹解锁', description: '实验功能',
    list: '指纹列表', add: '添加指纹', print: '指纹', cancel: '取消',
    identifying: '请完成系统身份验证，再采集尚未录入的手指。',
    center: '轻触指腹中央，抬起后再轻触。',
    edge: '用同一根手指轻触，抬起后再轻触。',
    edgeTitle: '换个握姿，采集指尖边缘',
    press: '用同一根手指轻触传感器，抬起后再轻触。',
    swipe: '用同一根手指再次划过传感器。',
    scan: '请再次采集同一根手指。',
    retrySwipe: '这次采样未通过，请用同一根手指再次划过传感器。',
    retryScan: '这次采样未通过，请再次采集同一根手指。',
    retry: '这次采样未通过。抬起手指，稍换位置再按一次。', complete: '指纹已添加。',
    starting: '正在开始录入…', busy: '指纹识别器正忙，请重试。',
    full: '已达到 6 枚指纹的上限。', failed: '录入已中断，请重试。',
    repair:'重新安装指纹支持', repairing:'安装中…', repairFailed:'失败',
    errors: {
      list: '无法读取已录入的指纹。',
      agent: '无法打开系统授权窗口。',
      authorization: '系统授权被拒绝或取消。',
      duplicate: '这根手指已录入，请换一根。',
      capture: '录入未完成，请重试。',
      'retry-timeout': '没有收到下一次采样，录入已结束。可以重新添加。',
      session: '无法在当前 Steam 会话启动录入。'
    }
  }
};

export function enrollmentPrompt(state, language) {
  if (!state || state.result === 'cancelled') return '';
  const text = panelText[language === 'zh' ? 'zh' : 'en'];
  if (state.result === 'sampling' || state.result === 'retry') {
    const progress = Number.isInteger(state.total) && state.total > 0
      ? state.samples + '/' + state.total + ' · ' : '';
    if (state.result === 'retry') {
      const instruction = state.scan_type === 'swipe' ? text.retrySwipe
        : state.scan_type === 'press' ? text.retry : text.retryScan;
      return progress + instruction;
    }
    if (state.guided) return progress + (state.samples < 4 ? text.center : text.edge);
    const instruction = state.scan_type === 'swipe' ? text.swipe
      : state.scan_type === 'press' ? text.press : text.scan;
    return progress + instruction;
  }
  if (state.result === 'unavailable') return text.errors[state.reason] || text.failed;
  return text[state.result] || '';
}

export default () => {
const internal = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
let api;
try { api = internal.connect(2, 'Boiler Fingerprint Unlock'); }
catch (_) { api = internal.connect(1, 'Boiler Fingerprint Unlock'); }
const backend = { verify: api.callable('verify'), cancel: api.callable('cancel'), enroll: api.callable('enroll'), progress: api.callable('enroll_progress'), fingerprints: api.callable('fingerprints'), status: api.callable('status'), repair: api.callable('repair') };
const key = 'boiler.fingerprint.enabled';
let settingsFault = false;
const enabled = () => { if (settingsFault) return false; try { const value = localStorage.getItem(key); return value === null || value === '1'; } catch { settingsFault = true; return false; } };
const store = () => window.securitystore;
const locked = () => { try { return !!store()?.IsLockScreenActive(); } catch { return false; } };
let generation = 0, previous = false, timer, retryTimer, inFlight = false, mounted = true, deviceRetries = 0;
let cancelBarrier = Promise.resolve(), listBarrier = Promise.resolve(), enrollBarrier = Promise.resolve(), visualState = null, successTimer, enrolling = false, listing = false, cancelFailed = false;
// Capture the actual lock-screen container before Steam starts its exit transition.
function lockScreenElement() {
  try {
    const doc = DFL.findSP()?.document;
    const classes = DFL.findModule(m => m && typeof m === 'object' &&
      ['Container','Indicators','DigitIndicator','NumericButtonInput','ForgotPINButton'].every(k => typeof m[k] === 'string'));
    return doc && classes ? doc.querySelector('.' + classes.Indicators)?.closest('.' + classes.Container) || null : null;
  } catch (_) { return null; }
}
function lockScreenOpacity(element) {
  try {
    if (!element || !element.isConnected) return null;
    const view = element.ownerDocument?.defaultView;
    if (!view) return null;
    let opacity = 1;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = view.getComputedStyle(node), value = Number(style.opacity);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || !Number.isFinite(value)) return null;
      opacity *= Math.max(0, Math.min(1, value));
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && opacity > 0 ? opacity : null;
  } catch (_) { return null; }
}
function stopSuccess() {
  clearTimeout(successTimer); successTimer = undefined; visualState = null; visual.remove();
}
function watchSuccess(element) {
  // The deadline is a cleanup fallback if Steam leaves an invisible/stale DOM mounted.
  const deadline = Date.now() + 10000;
  function check() {
    const opacity = lockScreenOpacity(element);
    if (!mounted || !enabled() || opacity === null || Date.now() >= deadline) { stopSuccess(); return; }
    visual.setOpacity(opacity);
    successTimer = setTimeout(check, 50);
  }
  check();
}
const visual = createFingerprintStatusRenderer(
  () => { try { return DFL.findSP()?.document || null; } catch (_) { return null; } },
  doc => {
    try {
      const classes = DFL.findModule(m => m && typeof m === 'object' &&
        ['Container','Indicators','DigitIndicator','NumericButtonInput','ForgotPINButton'].every(k => typeof m[k] === 'string'));
      if (!classes) return null;
      const indicators = doc.querySelector('.' + classes.Indicators);
      const digit = doc.querySelector('.' + classes.DigitIndicator);
      const keypad = [...doc.querySelectorAll('.' + classes.NumericButtonInput)];
      const footer = [...doc.querySelectorAll('.' + classes.ForgotPINButton)];
      if (!indicators || !digit || !keypad.length) return null;
      const computed = doc.defaultView.getComputedStyle(digit);
      const usable = c => c && c !== 'transparent' && c !== 'rgba(0, 0, 0, 0)';
      const footerRects = footer.map(el => el.getBoundingClientRect()).filter(r =>
        ['top','bottom','left','right'].every(k => Number.isFinite(r[k])) && r.right > r.left && r.bottom > r.top);
      return {indicators:indicators.getBoundingClientRect(), digit:digit.getBoundingClientRect(),
        blocked:[...keypad.map(el => el.getBoundingClientRect()), ...footerRects],
        color:usable(computed.borderTopColor) ? computed.borderTopColor : usable(computed.color) ? computed.color : '#68747e'};
    } catch (_) { return null; }
  });
function show(state) { visualState = state; visual.render(state); }
function abort(preserveSuccess = false) {
  generation++; clearTimeout(retryTimer); retryTimer = undefined;
  if (!preserveSuccess) stopSuccess();
  cancelBarrier = cancelBarrier.then(() => backend.cancel()).then(result => { cancelFailed = result?.cancelled === false; }).catch(() => { cancelFailed = true; });
}
function abortEnrollment() {
  abort();
  cancelBarrier = cancelBarrier.then(() => Promise.allSettled([listBarrier, enrollBarrier]));
}
function schedule(current, delay = 500) {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => { retryTimer = undefined; if (current === generation && mounted && enabled() && locked()) attempt(); }, delay);
}
async function attempt() {
  const current = generation;
  if (inFlight) { schedule(current); return; }
  inFlight = true;
  try {
    await cancelBarrier;
    if (current !== generation || !mounted || !enabled() || !locked()) return;
    show('waiting');
    const response = await backend.verify(30);
    if (current !== generation || !mounted || !enabled() || !locked()) return;
    if (response?.result === 'match') {
      const screen = lockScreenElement();
      show('success');
      const sec = store(), props = sec.GetActiveLockScreenProps();
      if (typeof props?.onSuccess === 'function') props.onSuccess();
      sec.SetActiveLockScreenProps(null);
      watchSuccess(screen);
    } else if (response?.result === 'unavailable' && ['timeout', 'no-match'].includes(response.reason)) {
      show(response.reason === 'no-match' ? 'no-match' : 'retrying');
      schedule(current, response.reason === 'no-match' ? 1050 : 500);
    } else if (response?.result === 'unavailable' && response.reason === 'device-error' && deviceRetries < 2) {
      const delay = [1000, 3000][deviceRetries++];
      show('retrying'); schedule(current, delay);
    } else { show('unavailable'); }
  } catch (_) { if (current === generation && mounted && enabled() && locked()) show('unavailable'); }
  finally { inFlight = false; }
}
function Content() {
  const [on, setOn] = SP_REACT.useState(enabled());
  const [inventory, setInventory] = SP_REACT.useState(null);
  const [enrollment, setEnrollment] = SP_REACT.useState(null);
  const [status, setStatus] = SP_REACT.useState(null);
  const [fixing, setFixing] = SP_REACT.useState(false);
  const [fixFailed, setFixFailed] = SP_REACT.useState(false);
  const language = steamLanguage();
  const text = panelText[language];

  async function refresh() {
    if (locked()) return;
    listing = true;
    try {
      listBarrier = backend.fingerprints();
      const response = await listBarrier;
      if (mounted && !locked()) setInventory(response);
    } catch (_) {
      if (mounted && !locked()) setInventory({result:'unavailable', reason:'list'});
    } finally { listing = false; }
  }

  SP_REACT.useEffect(() => {
    let live = true, statusTimer, waitingForRepair = false;
    const retry = () => { if (live) statusTimer = setTimeout(checkStatus, 2000); };
    async function checkStatus() {
      if (!live || !mounted) return;
      if (locked() || enrolling || listing || fixing) { retry(); return; }
      try {
        const value = await backend.status();
        if (!live || !mounted) return;
        setStatus(value);
        if (value?.available && waitingForRepair) {
          waitingForRepair = false;
          await refresh();
        }
        if (value?.busy || value?.repairable) {
          waitingForRepair = true;
          retry();
        }
      } catch (_) {
        if (live && mounted) setStatus({available:false});
      }
    }
    async function load() {
      await refresh();
      if (live) await checkStatus();
    }
    load();
    return () => { live = false; clearTimeout(statusTimer); if (enrolling || listing) abortEnrollment(); };
  }, [fixing]);

  async function startEnrollment() {
    if (enrolling || listing || locked()) return;
    enrolling = true;
    setEnrollment({result:'starting', samples:0});
    let polling = true;
    const poll = async () => {
      while (polling && mounted && enrolling && !locked()) {
        try {
          const state = await backend.progress();
          if (polling && mounted && !locked() && state?.result !== 'idle') setEnrollment(state);
        } catch (_) {}
        await new Promise(resolve => setTimeout(resolve, 350));
      }
    };
    poll();
    try {
      enrollBarrier = backend.enroll();
      const result = await enrollBarrier;
      polling = false;
      if (mounted && !locked()) {
        setEnrollment(cancelFailed ? {result:'unavailable'} : result);
        if (result?.result === 'complete' || result?.result === 'full') await refresh();
      }
    } catch (_) {
      if (mounted && !locked()) setEnrollment({result:'unavailable'});
    } finally { polling = false; enrolling = false; }
  }

  const h = SP_REACT.createElement;
  const count = inventory?.result === 'available' ? inventory.count : 0;
  const limit = inventory?.limit ?? 6;
  const full = count >= limit;
  const edgePhase = enrollment?.guided && ['sampling', 'retry'].includes(enrollment.result)
    && enrollment.samples >= 4 && enrollment.samples < enrollment.total;
  const message = enrollmentPrompt(enrollment, language)
    || (full ? text.full : enrollmentPrompt(inventory, language));
  const list = h(DFL.PanelSection, {title:text.list},
    ...Array.from({length:count}, (_, index) =>
      h(DFL.PanelSectionRow, {key:index},
        h(DFL.Field, {label:text.print + ' ' + (index + 1), focusable:false}))),
    edgePhase && h(DFL.PanelSectionRow, null,
      h('div', {role:'status', 'aria-live':'polite', style:{fontSize:'1.2em', fontWeight:'bold'}}, text.edgeTitle)),
    message && h(DFL.PanelSectionRow, null, h('div', {role:'status', 'aria-live':'polite'}, message)),
    h(DFL.PanelSectionRow, null, h(DFL.ButtonItem, {
      disabled: !enrolling && (full || !inventory || listing),
      onClick: () => {
        if (!enrolling) {
          if (inventory?.result === 'available' && !full) startEnrollment();
          else refresh();
          return;
        }
        backend.cancel().then(result => {
          if (result?.cancelled === false) { cancelFailed = true; setEnrollment({result:'unavailable'}); }
        }).catch(() => { cancelFailed = true; setEnrollment({result:'unavailable'}); });
      }
    }, enrolling ? text.cancel : text.add)));
  return h(SP_REACT.Fragment || 'div', null,
    h(DFL.PanelSection, {title:text.title},
      h(DFL.PanelSectionRow, null, h(DFL.ToggleField, {
        label:text.toggle, description:text.description, checked:on,
        onChange: value => { try { localStorage.setItem(key, value ? '1':'0'); } catch (_) { settingsFault = true; } setOn(enabled()); if (!value || !enabled()) abort(); else if (locked()) { if (!previous) { previous = true; deviceRetries = 0; } abort(); attempt(); } }
      }))),
    list,
    fixFailed && h(DFL.PanelSectionRow, null,
      h(DFL.Field, {label:text.repair, focusable:false}, text.repairFailed)),
    (fixing || (status?.repairable && !status.busy && !status.available)) && h(DFL.PanelSectionRow, null,
      h(DFL.ButtonItem, {layout:'below', disabled:fixing || enrolling, onClick: async () => {
        if (fixing || enrolling || locked()) return;
        setFixing(true); setFixFailed(false);
        try { setFixFailed(!(await backend.repair())?.ok); }
        catch (_) { setFixFailed(true); }
        finally { setFixing(false); }
      }}, fixing ? text.repairing : text.repair)));
}
  timer = setInterval(() => { const active = locked(); if (active !== previous) { previous = active; if (active) { deviceRetries = 0; if (enrolling || listing) abortEnrollment(); } if (active && enabled()) { if (successTimer) stopSuccess(); attempt(); } else abort(!active && !!successTimer); } else if (active && enabled() && visualState && visualState !== 'success') { visual.render(visualState); } }, 300);
  return {name:'Boiler Fingerprint Unlock', titleView:SP_REACT.createElement('div', null, panelText[steamLanguage()].title), content:SP_REACT.createElement(Content), icon:SP_REACT.createElement('div', null, '◉'), onDismount(){mounted = false; clearInterval(timer); abort();}};
};
