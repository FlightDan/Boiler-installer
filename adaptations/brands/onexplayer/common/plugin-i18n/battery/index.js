const R = window.SP_REACT, U = window.DFL, h = R.createElement;
const api = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2, "OXP3 Battery");
const getStatus = api.callable("get_status"), setLimit = api.callable("set_limit"), setMode = api.callable("set_mode");
const getSleepStatus = api.callable("get_sleep_status"), setSleepPolicy = api.callable("set_sleep_policy");
// BEGIN BOILER PLUGIN LOCALE
const steamLanguages = {
  english: 'en', schinese: 'zh', tchinese: 'zh-TW', japanese: 'ja',
  koreana: 'ko', korean: 'ko', german: 'de', french: 'fr',
  spanish: 'es', latam: 'es',
};

function normalizePluginLanguage(value) {
  const tag = String(value ?? '').trim().toLowerCase().split(/[.@]/, 1)[0].replaceAll('_', '-');
  if (Object.prototype.hasOwnProperty.call(steamLanguages, tag)) return steamLanguages[tag];
  const [language, ...subtags] = tag.split('-');
  if (language === 'zh') {
    if (subtags.includes('hans') || subtags.includes('chs')) return 'zh';
    return subtags.some(part => ['hant', 'cht', 'tw', 'hk', 'mo'].includes(part)) ? 'zh-TW' : 'zh';
  }
  return ['ja', 'ko', 'de', 'fr', 'es'].includes(language) ? language : 'en';
}

function steamLanguageFromSearch(search) {
  const params = new URLSearchParams(search);
  return params.get('LANGUAGE')?.trim() || params.get('language')?.trim();
}

function pluginLanguage() {
  let steamLanguage;
  try {
    steamLanguage = steamLanguageFromSearch(globalThis.DFL?.findSP?.()?.location?.search);
  } catch {
    // Decky's Steam window is not available in every view.
  }
  return normalizePluginLanguage(
    steamLanguage || steamLanguageFromSearch(globalThis.location?.search) ||
    globalThis.document?.documentElement?.lang?.trim() ||
    globalThis.navigator?.language || globalThis.navigator?.languages?.[0],
  );
}

function pluginTranslation(catalog) {
  return (english, chinese, values = {}) => {
    const language = pluginLanguage();
    const translated = (language === 'zh' ? chinese : catalog[english]?.[language]) || english;
    return translated.replace(/\{(\w+)\}/g, (placeholder, name) =>
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : placeholder,
    );
  };
}
// END BOILER PLUGIN LOCALE
const tr = pluginTranslation({
  "OXP3 Battery": { ja: "OXP3 バッテリー", ko: "OXP3 배터리", de: "OXP3-Akku", fr: "Batterie OXP3", es: "Batería OXP3", "zh-TW": "OXP3 電池" },
  "1 hour": {
    "ja": "1 時間",
    "ko": "1시간",
    "de": "1 Stunde",
    "fr": "1 heure",
    "es": "1 hora",
    "zh-TW": "1 小時"
  },
  "12 hours": {
    "ja": "12 時間",
    "ko": "12시간",
    "de": "12 Stunden",
    "fr": "12 heures",
    "es": "12 horas",
    "zh-TW": "12 小時"
  },
  "2 hours": {
    "ja": "2 時間",
    "ko": "2시간",
    "de": "2 Stunden",
    "fr": "2 heures",
    "es": "2 horas",
    "zh-TW": "2 小時"
  },
  "20 min": {
    "ja": "20 分",
    "ko": "20분",
    "de": "20 Min.",
    "fr": "20 min",
    "es": "20 min",
    "zh-TW": "20 分鐘"
  },
  "24 hours": {
    "ja": "24 時間",
    "ko": "24시간",
    "de": "24 Stunden",
    "fr": "24 heures",
    "es": "24 horas",
    "zh-TW": "24 小時"
  },
  "Allow charging up to the limit above.": {
    "ja": "上記の上限まで充電します。",
    "ko": "위 제한까지 충전합니다.",
    "de": "Laden bis zur obigen Grenze erlauben.",
    "fr": "Autoriser la charge jusqu’à la limite ci-dessus.",
    "es": "Permitir la carga hasta el límite anterior.",
    "zh-TW": "允許充電，遵循上方上限。"
  },
  "Bypass: all": {
    "ja": "常にバイパス",
    "ko": "항상 우회",
    "de": "Bypass: immer",
    "fr": "Dérivation : toujours",
    "es": "Desvío: siempre",
    "zh-TW": "全程旁路"
  },
  "Bypass: awake": {
    "ja": "起動中はバイパス",
    "ko": "깨어 있을 때 우회",
    "de": "Bypass: aktiv",
    "fr": "Dérivation : actif",
    "es": "Desvío: activo",
    "zh-TW": "開機旁路"
  },
  "Charge": {
    "ja": "充電",
    "ko": "충전",
    "de": "Laden",
    "fr": "Charger",
    "es": "Cargar",
    "zh-TW": "正常充電"
  },
  "Charge controls unavailable": {
    "ja": "充電制御は利用できません",
    "ko": "충전 제어를 사용할 수 없음",
    "de": "Ladesteuerung nicht verfügbar",
    "fr": "Contrôles de charge indisponibles",
    "es": "Controles de carga no disponibles",
    "zh-TW": "充電控制不可用"
  },
  "Charge limit": {
    "ja": "充電上限",
    "ko": "충전 제한",
    "de": "Ladegrenze",
    "fr": "Limite de charge",
    "es": "Límite de carga",
    "zh-TW": "充電上限"
  },
  "Charging": {
    "ja": "充電中",
    "ko": "충전 중",
    "de": "Wird geladen",
    "fr": "En charge",
    "es": "Cargando",
    "zh-TW": "充電中"
  },
  "Charging mode": {
    "ja": "充電モード",
    "ko": "충전 모드",
    "de": "Lademodus",
    "fr": "Mode de charge",
    "es": "Modo de carga",
    "zh-TW": "充電方式"
  },
  "Discharging": {
    "ja": "放電中",
    "ko": "방전 중",
    "de": "Wird entladen",
    "fr": "Décharge",
    "es": "Descargando",
    "zh-TW": "放電中"
  },
  "Finish adjusting to save": {
    "ja": "調整後に保存",
    "ko": "조정 완료 후 저장",
    "de": "Nach der Anpassung speichern",
    "fr": "Terminer le réglage pour enregistrer",
    "es": "Termina el ajuste para guardar",
    "zh-TW": "完成調整後自動儲存"
  },
  "Full": {
    "ja": "満充電",
    "ko": "완충",
    "de": "Voll",
    "fr": "Chargée",
    "es": "Completa",
    "zh-TW": "已充滿"
  },
  "Immediately": {
    "ja": "すぐに",
    "ko": "즉시",
    "de": "Sofort",
    "fr": "Immédiatement",
    "es": "Inmediatamente",
    "zh-TW": "立即進入"
  },
  "Keep fast sleep; do not enter S4.\nApplies on the next sleep.": {
    "ja": "高速スリープを維持し、S4 に移行しません。\n次回のスリープから適用されます。",
    "ko": "빠른 절전을 유지하고 S4로 전환하지 않습니다.\n다음 절전부터 적용됩니다.",
    "de": "Schnellen Energiesparmodus beibehalten, kein S4.\nGilt ab dem nächsten Schlaf.",
    "fr": "Conserver la veille rapide sans passer en S4.\nApplicable à la prochaine veille.",
    "es": "Mantener suspensión rápida sin entrar en S4.\nSe aplica en la próxima suspensión.",
    "zh-TW": "保持快速待機，不進入 S4。\n下次待機生效。"
  },
  "Keep the system sleep policy.\nSlide to set S4 for the next sleep.": {
    "ja": "システムのスリープ設定を維持します。\n次回のスリープに S4 を設定するにはスライドしてください。",
    "ko": "시스템 절전 정책을 유지합니다.\n슬라이더로 다음 절전에 S4를 설정하세요.",
    "de": "Systemrichtlinie beibehalten.\nFür den nächsten Ruhezustand S4 einstellen.",
    "fr": "Conserver la politique système.\nGlissez pour activer S4 à la prochaine veille.",
    "es": "Mantener la política del sistema.\nDesliza para usar S4 en la próxima suspensión.",
    "zh-TW": "沿用系統待機策略。\n滑動設定 S4，下次待機生效。"
  },
  "Never": {
    "ja": "移行しない",
    "ko": "사용 안 함",
    "de": "Nie",
    "fr": "Jamais",
    "es": "Nunca",
    "zh-TW": "永不進入"
  },
  "Next sleep goes straight to S4.\nSaving does not start hibernation.": {
    "ja": "次回のスリープで S4 に移行します。\n保存してもすぐには休止しません。",
    "ko": "다음 절전에서 바로 S4로 전환합니다.\n저장해도 최대 절전이 시작되지 않습니다.",
    "de": "Beim nächsten Schlaf direkt zu S4.\nSpeichern startet keinen Ruhezustand.",
    "fr": "La prochaine veille passe directement en S4.\nEnregistrer ne lance pas l’hibernation.",
    "es": "La próxima suspensión pasa directamente a S4.\nGuardar no inicia la hibernación.",
    "zh-TW": "下次待機直接進入 S4。\n儲存設定不會立即休眠。"
  },
  "Not charging": {
    "ja": "充電していません",
    "ko": "충전하지 않음",
    "de": "Lädt nicht",
    "fr": "Pas en charge",
    "es": "Sin cargar",
    "zh-TW": "未充電"
  },
  "On battery": {
    "ja": "バッテリー駆動",
    "ko": "배터리 사용 중",
    "de": "Akkubetrieb",
    "fr": "Sur batterie",
    "es": "Con batería",
    "zh-TW": "電池供電"
  },
  "Plugged in": {
    "ja": "電源接続中",
    "ko": "전원 연결됨",
    "de": "Netzbetrieb",
    "fr": "Sur secteur",
    "es": "Conectado",
    "zh-TW": "接電"
  },
  "Reading battery…": {
    "ja": "バッテリーを読み込み中…",
    "ko": "배터리 읽는 중…",
    "de": "Akkustatus wird gelesen…",
    "fr": "Lecture de la batterie…",
    "es": "Leyendo batería…",
    "zh-TW": "讀取電池…"
  },
  "Reading sleep settings…": {
    "ja": "スリープ設定を読み込み中…",
    "ko": "절전 설정 읽는 중…",
    "de": "Energiespareinstellungen werden gelesen…",
    "fr": "Lecture des paramètres de veille…",
    "es": "Leyendo ajustes de suspensión…",
    "zh-TW": "讀取待機設定…"
  },
  "Reading…": {
    "ja": "読み込み中…",
    "ko": "읽는 중…",
    "de": "Wird gelesen…",
    "fr": "Lecture…",
    "es": "Leyendo…",
    "zh-TW": "讀取中…"
  },
  "S4 scheduling is unavailable on this device.": {
    "ja": "このデバイスでは S4 の予約を利用できません。",
    "ko": "이 장치에서는 S4 예약을 사용할 수 없습니다.",
    "de": "S4-Planung ist auf diesem Gerät nicht verfügbar.",
    "fr": "La planification S4 est indisponible sur cet appareil.",
    "es": "La programación S4 no está disponible en este dispositivo.",
    "zh-TW": "此裝置不支援 S4 定時待機。"
  },
  "Saving…": {
    "ja": "保存中…",
    "ko": "저장 중…",
    "de": "Wird gespeichert…",
    "fr": "Enregistrement…",
    "es": "Guardando…",
    "zh-TW": "儲存中…"
  },
  "Sleep integration needs an update.": {
    "ja": "スリープ連携の更新が必要です。",
    "ko": "절전 연동을 업데이트해야 합니다.",
    "de": "Die Ruhezustandsintegration muss aktualisiert werden.",
    "fr": "L’intégration de veille doit être mise à jour.",
    "es": "Hay que actualizar la integración de suspensión.",
    "zh-TW": "待機元件需要更新。"
  },
  "Stop charging at this battery level.\nAuto-save after adjusting.": {
    "ja": "この残量で充電を停止します。\n調整後に自動保存します。",
    "ko": "이 배터리 잔량에서 충전을 중지합니다.\n조정 후 자동 저장됩니다.",
    "de": "Bei diesem Akkustand laden stoppen.\nNach der Anpassung automatisch speichern.",
    "fr": "Arrêter la charge à ce niveau.\nEnregistrement automatique après réglage.",
    "es": "Detener la carga en este nivel.\nGuardado automático tras ajustar.",
    "zh-TW": "電量達到此上限時停止充電。\n完成調整後自動儲存。"
  },
  "Stop charging while awake and asleep.\nChoose Charge to charge again.": {
    "ja": "起動中もスリープ中も充電を停止します。\n再開するには「充電」を選択してください。",
    "ko": "깨어 있을 때와 절전 중 모두 충전을 중지합니다.\n다시 충전하려면 충전을 선택하세요.",
    "de": "Laden im Betrieb und Schlaf stoppen.\nZum Laden „Laden“ wählen.",
    "fr": "Arrêter la charge en activité et en veille.\nChoisir Charger pour reprendre.",
    "es": "Detener la carga en activo y en suspensión.\nElige Cargar para reanudarla.",
    "zh-TW": "開機與待機時均停止充電。\n選擇正常充電以恢復充電。"
  },
  "Stop charging while awake.\nAllow charging during sleep.": {
    "ja": "起動中は充電を停止します。\nスリープ中は充電します。",
    "ko": "깨어 있을 때 충전을 중지합니다.\n절전 중에는 충전합니다.",
    "de": "Laden im aktiven Zustand stoppen.\nIm Schlafzustand laden.",
    "fr": "Arrêter la charge en activité.\nAutoriser la charge en veille.",
    "es": "Detener la carga en activo.\nPermitirla durante la suspensión.",
    "zh-TW": "開機時停止充電。\n待機時允許充電。"
  },
  "System default": {
    "ja": "システム既定",
    "ko": "시스템 기본값",
    "de": "Systemstandard",
    "fr": "Valeur système",
    "es": "Predeterminado del sistema",
    "zh-TW": "系統預設"
  },
  "This sleep setting is unavailable.": {
    "ja": "このスリープ設定は利用できません。",
    "ko": "이 절전 설정을 사용할 수 없습니다.",
    "de": "Diese Energiespareinstellung ist nicht verfügbar.",
    "fr": "Ce paramètre de veille est indisponible.",
    "es": "Este ajuste de suspensión no está disponible.",
    "zh-TW": "目前待機設定不可用。"
  },
  "Timed on battery power.\nOn AC, keep fast sleep in memory.": {
    "ja": "バッテリー駆動時に時間を計測します。\nAC 接続時は高速スリープを維持します。",
    "ko": "배터리 사용 시 시간을 측정합니다.\nAC 연결 시 빠른 절전을 유지합니다.",
    "de": "Zeitsteuerung im Akkubetrieb.\nAm Netz bleibt der schnelle Energiesparmodus aktiv.",
    "fr": "Minuterie sur batterie.\nSur secteur, conserver la veille rapide.",
    "es": "Temporizador con batería.\nCon corriente, mantener suspensión rápida.",
    "zh-TW": "電池供電時計時進入 S4。\n接電時保持快速待機。"
  },
  "Unknown": {
    "ja": "不明",
    "ko": "알 수 없음",
    "de": "Unbekannt",
    "fr": "Inconnu",
    "es": "Desconocido",
    "zh-TW": "未知"
  },
  "Use S4 hibernation": {
    "ja": "S4 休止状態を使用",
    "ko": "S4 최대 절전 사용",
    "de": "S4-Ruhezustand verwenden",
    "fr": "Utiliser l’hibernation S4",
    "es": "Usar hibernación S4",
    "zh-TW": "使用 S4 待機"
  },
  "{minutes} min": {
    "ja": "{minutes} 分",
    "ko": "{minutes}분",
    "de": "{minutes} Min.",
    "fr": "{minutes} min",
    "es": "{minutes} min",
    "zh-TW": "{minutes} 分鐘"
  }
});
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
  return step ? step.label() : tr("{minutes} min", "{minutes} 分钟", {minutes: sleep.delay_minutes});
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
export default () => ({ title: h("div", null, tr("OXP3 Battery", "OXP3 电池")), content: h(Content), icon: h("span", null, "▰"), onDismount() {} });
