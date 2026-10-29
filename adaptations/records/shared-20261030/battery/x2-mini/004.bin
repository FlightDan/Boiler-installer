const R = window.SP_REACT, U = window.DFL, h = R.createElement;
const api = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2, "OXP3 Battery");
const getStatus = api.callable("get_status"), setLimit = api.callable("set_limit"), setMode = api.callable("set_mode");
const getSleepStatus = api.callable("get_sleep_status"), setSleepPolicy = api.callable("set_sleep_policy");
const tr = (en, zh) => /^zh(?:[-_]|$)/i.test(globalThis.navigator?.language || "") ? zh : en;
// Slider positions are choices, not a linear scale of elapsed minutes.
const sleepSteps = [
  { mode: "hibernate", label: () => tr("Immediately", "直接进入") },
  { mode: "auto", minutes: 20, label: () => tr("20 min", "20 分钟") },
  { mode: "auto", minutes: 60, label: () => tr("1 hour", "1 小时") },
  { mode: "auto", minutes: 120, label: () => tr("2 hours", "2 小时") },
  { mode: "auto", minutes: 720, label: () => tr("12 hours", "12 小时") },
  { mode: "auto", minutes: 1440, label: () => tr("24 hours", "24 小时") },
  { mode: "s2idle", label: () => tr("Never", "永不进入") },
];
function sleepPosition(sleep) {
  if (sleep?.mode === "hibernate") return 0;
  if (sleep?.mode !== "auto") return 6;
  // Keep older custom delays intact until the user chooses a new position.
  return sleepSteps.reduce((best, step, i) => step.minutes != null &&
    Math.abs(step.minutes - sleep.delay_minutes) < Math.abs(sleepSteps[best].minutes - sleep.delay_minutes) ? i : best, 1);
}
function sleepLabel(sleep) {
  if (!sleep) return tr("Reading…", "读取中…");
  if (sleep.mode === "system") return tr("System default", "跟随系统");
  if (sleep.mode !== "auto") return sleepSteps[sleepPosition(sleep)].label();
  const step = sleepSteps.find(item => item.minutes === sleep.delay_minutes);
  return step ? step.label() : tr(`${sleep.delay_minutes} min`, `${sleep.delay_minutes} 分钟`);
}
function Content() {
  const modes = {
    auto: tr("Charge", "正常充电"),
    "inhibit-charge-awake": tr("Bypass: awake", "开机旁路"),
    "inhibit-charge": tr("Bypass: all", "全程旁路"),
  };
  const statuses = { Charging: tr("Charging", "充电中"), Discharging: tr("Discharging", "放电中"),
    "Not charging": tr("Not charging", "未充电"), Full: tr("Full", "已充满"), Unknown: tr("Unknown", "未知") };
  const [data, setData] = R.useState(null), [sleep, setSleep] = R.useState(null);
  const [chargeDraft, setChargeDraft] = R.useState(null), [sleepDraft, setSleepDraft] = R.useState(null);
  const [modeDraft, setModeDraft] = R.useState(null);
  const [busy, setBusy] = R.useState(false), [pending, setPending] = R.useState(null);
  const [error, setError] = R.useState(""), [pollError, setPollError] = R.useState("");
  const timer = R.useRef(null), draft = R.useRef(null), dragging = R.useRef(false);
  const working = R.useRef(false), live = R.useRef(true), sequence = R.useRef(0);
  const clearDraft = () => { draft.current = null; setPending(null); setChargeDraft(null); setModeDraft(null); setSleepDraft(null); };
  const write = async request => {
    if (request.kind === "sleep") {
      const step = sleepSteps[request.value];
      return setSleepPolicy(step.mode, step.minutes ?? request.previousMinutes ?? 120);
    }
    return request.kind === "limit" ? setLimit(request.value) : setMode(Object.keys(modes)[request.value]);
  };
  const perform = async request => {
    if (working.current) return;
    working.current = true; sequence.current++; setBusy(true); setError("");
    try {
      const result = await write(request);
      if (live.current) request.kind === "sleep" ? setSleep(result) : setData(result);
    } catch (e) {
      if (live.current) setError(String(e));
    } finally {
      working.current = false;
      if (live.current) {
        setBusy(false);
        if (draft.current === request) clearDraft();
        else if (draft.current) schedule();
      }
    }
  };
  const schedule = (delay = draft.current?.kind === "limit" ? 1000 : 350) => {
    clearTimeout(timer.current);
    if (!draft.current || dragging.current) return;
    timer.current = setTimeout(() => {
      if (working.current) schedule();
      else if (draft.current) perform(draft.current);
    }, delay);
  };
  const change = (kind, value) => {
    if (kind === "sleep") {
      value = Math.max(0, Math.min(6, Math.round(value)));
      setSleepDraft(value);
    } else if (kind === "mode") {
      value = Math.max(0, Math.min(2, Math.round(value)));
      setModeDraft(value);
    } else {
      value = Math.max(60, Math.min(100, Math.round(value)));
      setChargeDraft(value);
    }
    sequence.current++;
    draft.current = { kind, value, previousMinutes: sleep?.delay_minutes };
    setPending(kind); schedule();
  };
  const beginDrag = () => { dragging.current = true; clearTimeout(timer.current); };
  const finishChange = (kind, value) => {
    dragging.current = false;
    change(kind, value);
    schedule(kind === "limit" ? 1000 : 0);
  };
  R.useEffect(() => {
    live.current = true;
    const poll = async () => {
      if (working.current) return;
      const token = sequence.current;
      const results = await Promise.allSettled([getStatus(), getSleepStatus()]);
      if (!live.current || working.current || token !== sequence.current) return;
      const failures = [];
      if (results[0].status === "fulfilled") setData(results[0].value);
      else { setData(null); failures.push(String(results[0].reason)); }
      if (results[1].status === "fulfilled") setSleep(results[1].value);
      else { setSleep(null); failures.push(String(results[1].reason)); }
      setPollError(failures.join(" · "));
    };
    poll(); const id = setInterval(poll, 3000);
    return () => {
      live.current = false; clearTimeout(timer.current); clearInterval(id);
    };
  }, []);
  const row = (child, key) => h(U.PanelSectionRow, { key }, child);
  const position = sleepDraft ?? sleepPosition(sleep);
  const selectedLabel = sleepDraft == null ? sleepLabel(sleep) : sleepSteps[position].label();
  const selectedMode = sleepDraft == null ? sleep?.mode : sleepSteps[position].mode;
  const sleepSupported = ["s2idle", "hibernate", "auto"].every(mode => sleep?.available_modes?.includes(mode));
  const description = !sleep ? tr("Reading sleep settings…", "读取待机设置…")
    : !sleep.router_installed ? tr("Sleep integration needs an update.", "待机组件需要更新。")
    : !sleepSupported ? tr("S4 scheduling is unavailable on this device.", "此设备不支持 S4 定时待机。")
    : !sleep.ready ? tr("This sleep setting is unavailable.", "当前待机设置不可用。")
    : selectedMode === "system" ? tr("Keep the system sleep policy.\nSlide to set S4 for the next sleep.", "当前沿用系统待机策略。\n滑动设置 S4，下次待机生效。")
    : selectedMode === "auto" ? tr("Timed on battery power.\nOn AC, keep fast sleep in memory.", "电池供电时计时转入 S4。\n接电时保持快速待机。")
    : selectedMode === "hibernate" ? tr("Next sleep goes straight to S4.\nSaving does not start hibernation.", "下次待机直接进入 S4。\n保存设置不会立即休眠。")
    : tr("Keep fast sleep; do not enter S4.\nApplies on the next sleep.", "保持快速待机，不转入 S4。\n下次待机生效。");
  const modeKeys = Object.keys(modes);
  const modePosition = modeDraft ?? Math.max(0, modeKeys.indexOf(data?.mode));
  const modeDescriptions = {
    auto: tr("Allow charging up to the limit above.", "允许充电，遵循上方上限。"),
    "inhibit-charge-awake": tr("Stop charging while awake.\nAllow charging during sleep.", "开机时停止充电。\n待机时允许充电。"),
    "inhibit-charge": tr("Stop charging while awake and asleep.\nChoose Charge to charge again.", "开机、待机均停止充电。\n需要充电时切回正常充电。"),
  };
  const noteStyle = { fontSize: 12, lineHeight: "18px", minHeight: 36, whiteSpace: "pre-line", opacity: 0.85 };
  const note = (kind, text) => pending === kind ? busy ? tr("Saving…", "保存中…") : tr("Finish adjusting to save", "完成调整后自动保存") : text;
  const group = (key, title, props, text) => row(h("div", { "data-battery-setting": key,
    style: { display: "flex", flexDirection: "column", gap: 4 } },
    h(U.SliderField, { label: h("strong", null, title), showValue: true,
      padding: "none", bottomSeparator: "none", "aria-describedby": `battery-${key}-help`, ...props }),
    h("div", { id: `battery-${key}-help`, style: noteStyle }, text)), key);
  return h(R.Fragment, null,
    h(U.PanelSection, null,
      row(h("div", { style: { fontSize: 12, marginBottom: 12 } }, data
        ? `${data.capacity}% · ${data.ac ? tr("Plugged in", "接电") : tr("On battery", "电池供电")} · ${statuses[data.status] || data.status}${data.power_now != null ? ` · ${(Number(data.power_now) / 1e6).toFixed(1)} W` : ""}`
        : tr("Reading battery…", "读取电池…")), "state"),
      h("div", { style: { display: "flex", flexDirection: "column", gap: 20 } },
        ...(data?.charge_control_available === false ? [
          row(h("div", { style: noteStyle }, tr("Charge controls unavailable", "此设备不支持充电控制")), "charge-unavailable"),
        ] : [
          group("limit", tr("Charge limit", "充电上限"), {
            min: 60, max: 100, step: 5, notchCount: 9,
            value: chargeDraft ?? Math.max(60, data?.limit ?? 80), valueSuffix: "%",
            disabled: busy || (pending && pending !== "limit") || !data,
            onChange: value => change("limit", value),
            onChangeStart: beginDrag, onChangeComplete: value => finishChange("limit", value),
          }, note("limit", tr("Stop charging at this battery level.\nAuto-save after adjusting.", "电量达到上限时停止充电。\n完成调整后自动保存。"))),
          group("charge-mode", tr("Charging mode", "充电方式"), {
            min: 0, max: 2, step: 1, notchCount: 3, value: modePosition,
            renderValue: value => modes[modeKeys[Math.max(0, Math.min(2, Math.round(value)))]],
            disabled: busy || (pending && pending !== "mode") || !data,
            onChange: value => change("mode", value),
            onChangeStart: beginDrag, onChangeComplete: value => finishChange("mode", value),
          }, note("mode", modeDescriptions[modeKeys[modePosition]])),
        ]),
        group("sleep-slider", tr("Use S4 hibernation", "使用 S4 待机"), {
          min: 0, max: 6, step: 1, notchCount: 7, value: position,
          disabled: busy || (pending && pending !== "sleep") || !sleep?.router_installed || !sleepSupported,
          onChange: value => change("sleep", value),
          onChangeStart: beginDrag, onChangeComplete: value => finishChange("sleep", value),
          renderValue: value => Math.round(value) === position ? selectedLabel
            : sleepSteps[Math.max(0, Math.min(6, Math.round(value)))].label(),
        }, note("sleep", description)))),
    error || pollError ? h(U.PanelSection, null,
      row(h("div", { style: { color: "#ffb4b4" } }, error || pollError), "error")) : null);
}
export default () => ({ title: h("div", null, "OXP3 Battery"), content: h(Content), icon: h("span", null, "▰"), onDismount() {} });
