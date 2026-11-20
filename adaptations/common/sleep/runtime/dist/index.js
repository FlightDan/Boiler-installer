const R = window.SP_REACT, U = window.DFL, h = R.createElement;
const api = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2, "Boiler S2/S4 switcher");
const getStatus = api.callable("get_sleep_status"), savePolicy = api.callable("set_sleep_policy"), setEnabled = api.callable("set_enabled");
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
const catalog = {
  "boot parameters unavailable": ["起動パラメーターを読み取れません", "부팅 매개변수를 읽을 수 없습니다", "Startparameter nicht verfügbar", "Paramètres de démarrage indisponibles", "Parámetros de arranque no disponibles", "無法讀取開機參數"],
  "external resume configuration detected": ["外部の復帰設定が検出されました", "외부 복원 설정이 감지되었습니다", "Externe Fortsetzungskonfiguration erkannt", "Configuration de reprise externe détectée", "Se detectó una configuración externa de reanudación", "偵測到外部恢復設定"],
  "EFI variables unavailable": ["EFI 変数を利用できません", "EFI 변수를 사용할 수 없습니다", "EFI-Variablen nicht verfügbar", "Variables EFI indisponibles", "Variables EFI no disponibles", "EFI 變數無法使用"],
  "pending EFI location needs inspection": ["保留中の EFI 位置を確認してください", "대기 중인 EFI 위치를 확인해야 합니다", "Ausstehenden EFI-Speicherort prüfen", "Emplacement EFI en attente à vérifier", "Debe revisarse la ubicación EFI pendiente", "需要檢查待處理的 EFI 位置"],
  "resume is already configured": ["復帰が既に設定されています", "복원이 이미 설정되어 있습니다", "Fortsetzung ist bereits konfiguriert", "La reprise est déjà configurée", "La reanudación ya está configurada", "恢復功能已設定"],
  "resume configuration unavailable": ["復帰設定を取得できません", "복원 설정을 확인할 수 없습니다", "Fortsetzungskonfiguration nicht verfügbar", "Configuration de reprise indisponible", "Configuración de reanudación no disponible", "無法讀取恢復設定"],
  "other swap is active": ["別のスワップが有効です", "다른 스왑이 활성화되어 있습니다", "Anderer Swap ist aktiv", "Un autre espace d’échange est actif", "Hay otro espacio de intercambio activo", "其他交換區正在使用中"],
  "systemd sleep support is unavailable": ["systemd のスリープ機能を利用できません", "systemd 절전 기능을 사용할 수 없습니다", "systemd-Schlafunterstützung nicht verfügbar", "Prise en charge de la veille systemd indisponible", "Compatibilidad de suspensión de systemd no disponible", "systemd 睡眠功能無法使用"],
  "s2idle unavailable": ["s2idle を利用できません", "s2idle을 사용할 수 없습니다", "s2idle nicht verfügbar", "s2idle indisponible", "s2idle no disponible", "s2idle 無法使用"],
  "kernel has no disk sleep state": ["カーネルに disk 休止状態がありません", "커널에 디스크 절전 상태가 없습니다", "Kernel unterstützt keinen Disk-Ruhezustand", "Le noyau ne prend pas en charge la veille sur disque", "El núcleo no admite la hibernación en disco", "核心沒有 disk 休眠狀態"],
  "kernel hibernation is disabled": ["カーネルの休止機能が無効です", "커널 최대 절전이 비활성화됨", "Ruhezustand im Kernel deaktiviert", "Hibernation du noyau désactivée", "Hibernación del núcleo desactivada", "核心休眠已停用"],
  "a SteamOS hibernation operation needs inspection": ["SteamOS の休止処理を確認してください", "SteamOS 최대 절전 작업을 확인해야 합니다", "SteamOS-Ruhezustandsvorgang muss geprüft werden", "Une opération d’hibernation SteamOS doit être vérifiée", "Debe revisarse una operación de hibernación de SteamOS", "需要檢查 SteamOS 休眠作業"],
  "/home must be a separate ext4 mount": ["/home は独立してマウントされた ext4 である必要があります", "/home은 별도로 마운트된 ext4여야 합니다", "/home muss ein separat eingehängtes ext4-Dateisystem sein", "/home doit être un montage ext4 distinct", "/home debe ser un montaje ext4 independiente", "/home 必須是獨立掛載的 ext4"],
  "EFI boot is required": ["EFI 起動が必要です", "EFI 부팅이 필요합니다", "EFI-Start erforderlich", "Démarrage EFI requis", "Se requiere arranque EFI", "需要 EFI 開機"],
  "owned swap needs inspection": ["専用スワップを確認してください", "전용 스왑을 확인해야 합니다", "Zugeordneter Swap muss geprüft werden", "L’espace d’échange dédié doit être vérifié", "Debe revisarse el espacio de intercambio dedicado", "需要檢查專用交換區"],
  "resume hook unavailable": ["復帰フックを利用できません", "복원 훅을 사용할 수 없습니다", "Fortsetzungs-Hook nicht verfügbar", "Hook de reprise indisponible", "Hook de reanudación no disponible", "恢復掛鉤無法使用"],
  "EFI or resume target unavailable": ["EFI または復帰先を利用できません", "EFI 또는 복원 대상을 사용할 수 없습니다", "EFI oder Fortsetzungsziel nicht verfügbar", "Cible EFI ou de reprise indisponible", "Destino EFI o de reanudación no disponible", "EFI 或恢復目標無法使用"],
  "RTC unavailable": ["RTC を利用できません", "RTC를 사용할 수 없습니다", "RTC nicht verfügbar", "RTC indisponible", "RTC no disponible", "RTC 無法使用"],
  "hibernation or resume disabled by boot parameters": ["起動パラメーターで休止または復帰が無効です", "부팅 매개변수에서 최대 절전 또는 복원이 비활성화됨", "Ruhezustand oder Fortsetzung durch Startparameter deaktiviert", "Hibernation ou reprise désactivée par les paramètres de démarrage", "La hibernación o reanudación está desactivada por parámetros de arranque", "開機參數已停用休眠或恢復"],
  "system sleep policy could not be read": ["システムのスリープ設定を読み取れません", "시스템 절전 정책을 읽을 수 없습니다", "System-Energiesparrichtlinie konnte nicht gelesen werden", "Impossible de lire la politique de veille système", "No se pudo leer la política de suspensión del sistema", "無法讀取系統睡眠策略"],
  "system policy disables hibernation": ["システム設定で休止が無効です", "시스템 정책에서 최대 절전이 비활성화됨", "Systemrichtlinie deaktiviert den Ruhezustand", "La politique système désactive l’hibernation", "La política del sistema desactiva la hibernación", "系統策略已停用休眠"],
  "system policy disables suspend-then-hibernate": ["システム設定でスリープ後の休止が無効です", "시스템 정책에서 절전 후 최대 절전이 비활성화됨", "Systemrichtlinie deaktiviert Energiesparen mit anschließendem Ruhezustand", "La politique système désactive la veille suivie de l’hibernation", "La política del sistema desactiva la suspensión seguida de hibernación", "系統策略已停用待機後休眠"],
  "Immediately": ["すぐに", "즉시", "Sofort", "Immédiatement", "Inmediatamente", "立即"],
  "{minutes} min": ["{minutes} 分", "{minutes}분", "{minutes} Min.", "{minutes} min", "{minutes} min", "{minutes} 分鐘"],
  "20 min": ["20 分", "20분", "20 Min.", "20 min", "20 min", "20 分鐘"],
  "1 hour": ["1 時間", "1시간", "1 Stunde", "1 heure", "1 hora", "1 小時"],
  "2 hours": ["2 時間", "2시간", "2 Stunden", "2 heures", "2 horas", "2 小時"],
  "12 hours": ["12 時間", "12시간", "12 Stunden", "12 heures", "12 horas", "12 小時"],
  "24 hours": ["24 時間", "24시간", "24 Stunden", "24 heures", "24 horas", "24 小時"],
  "Enable S4 sleep": ["S4 休止を有効にする", "S4 최대 절전 사용", "S4-Ruhezustand aktivieren", "Activer l’hibernation S4", "Activar hibernación S4", "開啟 S4 休眠"],
  "Enter S4": ["S4 に移行", "S4 진입", "S4 aktivieren", "Passer en S4", "Entrar en S4", "進入 S4"],
  "Dedicated swap: {size} MiB; leave at least 500 MiB free. Physical resume is unverified.": ["専用スワップ: {size} MiB。空き容量を 500 MiB 以上残してください。実機での復帰は未検証です。", "전용 스왑: {size} MiB; 최소 500 MiB의 여유 공간을 남기세요. 실제 기기 복원은 검증되지 않았습니다.", "Dedizierter Swap: {size} MiB; mindestens 500 MiB frei lassen. Fortsetzung auf Hardware nicht geprüft.", "Échange dédié : {size} MiB ; laisser au moins 500 MiB libres. Reprise sur matériel non vérifiée.", "Intercambio dedicado: {size} MiB; deja al menos 500 MiB libres. La reanudación en hardware no se ha verificado.", "專用交換區：{size} MiB；至少保留 500 MiB 空間。尚未驗證實機恢復。"],
  "Swap size is unavailable. Physical resume is unverified.": ["スワップサイズを取得できません。実機での復帰は未検証です。", "스왑 크기를 확인할 수 없습니다. 실제 기기 복원은 검증되지 않았습니다.", "Swap-Größe nicht verfügbar. Fortsetzung auf Hardware nicht geprüft.", "Taille de l’échange indisponible. Reprise sur matériel non vérifiée.", "Tamaño de intercambio no disponible. La reanudación en hardware no se ha verificado.", "無法讀取交換區大小。尚未驗證實機恢復。"],
  "Reading status…": ["状態を読み込み中…", "상태를 읽는 중…", "Status wird gelesen…", "Lecture de l’état…", "Leyendo estado…", "正在讀取狀態…"],
  "System integration is not active; see setup instructions.": ["システム連携が有効ではありません。設定手順を確認してください。", "시스템 연동이 활성화되지 않았습니다. 설정 지침을 확인하세요.", "Systemintegration ist nicht aktiv; siehe Einrichtungsanleitung.", "L’intégration système est inactive ; consultez les instructions de configuration.", "La integración del sistema no está activa; consulta las instrucciones de configuración.", "系統整合未啟用；請參閱設定說明。"],
  "Swap cleanup requires attention.": ["スワップのクリーンアップを確認してください。", "스왑 정리를 확인해야 합니다.", "Swap-Bereinigung erfordert Aufmerksamkeit.", "Le nettoyage de l’échange nécessite une intervention.", "La limpieza del intercambio requiere atención.", "交換區清理需要處理。"],
  "S4 is off; system sleep is used.": ["S4 はオフです。システムのスリープを使用します。", "S4가 꺼져 있으며 시스템 절전을 사용합니다.", "S4 ist aus; die Systemeinstellung wird verwendet.", "S4 est désactivé ; la veille système est utilisée.", "S4 está desactivado; se usa la suspensión del sistema.", "S4 已關閉；使用系統睡眠。"],
  "Selected mode unavailable: ": ["選択したモードを利用できません: ", "선택한 모드를 사용할 수 없음: ", "Gewählter Modus nicht verfügbar: ", "Mode sélectionné indisponible : ", "Modo seleccionado no disponible: ", "所選模式無法使用："],
  "Saved mode is unavailable; system suspend will be used.": ["保存したモードを利用できません。システムのスリープを使用します。", "저장된 모드를 사용할 수 없어 시스템 절전을 사용합니다.", "Gespeicherter Modus nicht verfügbar; System-Energiesparen wird verwendet.", "Mode enregistré indisponible ; la veille système sera utilisée.", "El modo guardado no está disponible; se usará la suspensión del sistema.", "已儲存模式無法使用；將使用系統待機。"],
  "Eligible; physical S4 resume has not been verified.": ["条件を満たしています。実機の S4 復帰は未検証です。", "조건을 충족합니다. 실제 기기의 S4 복원은 검증되지 않았습니다.", "Voraussetzungen erfüllt; S4-Fortsetzung auf Hardware nicht geprüft.", "Conditions remplies ; reprise S4 sur matériel non vérifiée.", "Requisitos cumplidos; la reanudación S4 en hardware no se ha verificado.", "條件檢測通過；尚未驗證實機 S4 恢復。"],
  "Retry cleanup": ["クリーンアップを再試行", "정리 다시 시도", "Bereinigung erneut versuchen", "Réessayer le nettoyage", "Reintentar limpieza", "重試清理"],
  "Save": ["保存", "저장", "Speichern", "Enregistrer", "Guardar", "儲存"]
};
const translated = Object.fromEntries(Object.entries(catalog).map(([key, values]) => [key, Object.fromEntries(['ja', 'ko', 'de', 'fr', 'es', 'zh-TW'].map((language, i) => [language, values[i]]))]));
const tr = pluginTranslation(translated);
const delays = [20, 60, 120, 720, 1440];
const reasons = {
  cmdline_unavailable: ["boot parameters unavailable", "无法读取启动参数"],
  external_resume: ["external resume configuration detected", "检测到外部恢复配置"],
  efivars_unavailable: ["EFI variables unavailable", "EFI 变量不可用"],
  pending_efi_location: ["pending EFI location needs inspection", "待处理的 EFI 位置需要检查"],
  resume_configured: ["resume is already configured", "恢复功能已配置"],
  resume_unavailable: ["resume configuration unavailable", "无法读取恢复配置"],
  other_swap: ["other swap is active", "其他交换区正在使用"],
  systemd_unsupported: ["systemd sleep support is unavailable", "systemd 睡眠功能不可用"],
  s2idle_missing: ["s2idle unavailable", "不支持 s2idle"],
  disk_state_missing: ["kernel has no disk sleep state", "内核没有 disk 休眠状态"],
  disk_disabled: ["kernel hibernation is disabled", "内核休眠已禁用"],
  native_metadata: ["a SteamOS hibernation operation needs inspection", "SteamOS 休眠状态需要检查"],
  filesystem_unsupported: ["/home must be a separate ext4 mount", "/home 必须是独立挂载的 ext4"],
  efi_required: ["EFI boot is required", "需要 EFI 启动"],
  swap_unready: ["owned swap needs inspection", "专用交换区需要检查"],
  resume_hook_missing: ["resume hook unavailable", "恢复钩子不可用"],
  resume_target_missing: ["EFI or resume target unavailable", "EFI 或恢复目标不可用"],
  rtc_missing: ["RTC unavailable", "RTC 不可用"],
  boot_disabled: ["hibernation or resume disabled by boot parameters", "启动参数禁用了休眠或恢复"],
  policy_unknown: ["system sleep policy could not be read", "无法读取系统睡眠策略"],
  policy_disabled: ["system policy disables hibernation", "系统策略禁用了休眠"],
  auto_policy_disabled: ["system policy disables suspend-then-hibernate", "系统策略禁用了待机后休眠"],
};
const reasonText = code => reasons[code] ? tr(...reasons[code]) : code;
function position(data) {
  if (data.mode === "hibernate") return 0;
  const i = delays.indexOf(data.delay_minutes);
  return i < 0 ? 3 : i + 1;
}
function Content() {
  const [status, setStatus] = R.useState(null);
  const [enabled, setEnabledState] = R.useState(false);
  const [step, setStep] = R.useState(3);
  const [legacy, setLegacy] = R.useState(null);
  const [busy, setBusy] = R.useState(false);
  const [error, setError] = R.useState("");
  R.useEffect(() => {
    let live = true;
    getStatus().then(data => {
      if (!live) return;
      setStatus(data);
      setEnabledState(data.enabled === true);
      setStep(position(data));
      if (data.mode === "auto" && !delays.includes(data.delay_minutes)) setLegacy(data.delay_minutes);
    }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, []);
  const mode = step === 0 && legacy == null ? "hibernate" : "auto";
  const supported = !!status && status.available_modes.includes(mode);
  const changeEnabled = async value => {
    setBusy(true); setError("");
    try {
      const data = await setEnabled(value);
      setStatus(data); setEnabledState(data.enabled === true);
      setStep(position(data));
      setLegacy(data.mode === "auto" && !delays.includes(data.delay_minutes) ? data.delay_minutes : null);
    } catch (e) {
      setError(String(e));
      try {
        const data = await getStatus(); setStatus(data); setEnabledState(data.enabled === true);
      } catch (readError) {
        setStatus(null); setError(String(e) + "; " + String(readError));
      }
    } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setError("");
    try {
      const value = mode === "auto" ? (legacy ?? delays[step - 1]) : status.delay_minutes;
      const data = await savePolicy(mode, value);
      setStatus(data);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const label = n => n === 0 ? tr("Immediately", "立即") :
    n === step && legacy != null ? tr("{minutes} min", "{minutes} 分钟", {minutes: legacy}) :
    ["", tr("20 min", "20 分钟"), tr("1 hour", "1 小时"), tr("2 hours", "2 小时"), tr("12 hours", "12 小时"), tr("24 hours", "24 小时")][n];
  const missing = status?.reasons?.[mode];
  const details = missing ? (Array.isArray(missing) ? missing : [missing]).map(reasonText).join("; ") : "";
  return h(U.PanelSection, {title: "Boiler S2/S4 switcher"},
    h(U.PanelSectionRow, {key: "switch"}, h(U.ToggleField, {
      label: tr("Enable S4 sleep", "开启 S4 睡眠"), checked: enabled,
      disabled: !status || !status.router_installed || busy,
      onChange: changeEnabled,
    })),
    h(U.PanelSectionRow, {key: "delay"}, h(U.SliderField, {
      label: tr("Enter S4", "进入 S4"), value: step, min: 0, max: 5, step: 1, notchCount: 6,
      showValue: true, renderValue: label,
      disabled: !enabled || !status?.router_installed || busy,
      onChange: value => { setLegacy(null); setStep(Math.max(0, Math.min(5, Math.round(value)))); },
    })),
    h(U.PanelSectionRow, {key: "explanation"}, h("div", null,
      status?.required_swap_bytes != null
        ? tr("Dedicated swap: {size} MiB; leave at least 500 MiB free. Physical resume is unverified.",
             "专用交换区：{size} MiB；至少保留 500 MiB 空间。尚未验证实机恢复。", {size: Math.ceil(status.required_swap_bytes / 1048576)})
        : tr("Swap size is unavailable. Physical resume is unverified.", "无法读取交换区大小。尚未验证实机恢复。"))),
    h(U.PanelSectionRow, {key: "status"}, h("div", null,
      !status ? tr("Reading status…", "正在读取状态…") :
      !status.router_installed ? tr("System integration is not active; see setup instructions.", "系统集成未启用；请参阅设置说明。") :
      !enabled && status.swap_state !== "absent" ? tr("Swap cleanup requires attention.", "交换区清理需要处理。") :
      !enabled ? tr("S4 is off; system sleep is used.", "S4 已关闭；使用系统睡眠。") :
      !supported ? tr("Selected mode unavailable: ", "所选模式不可用：") + details :
      !status.ready ? tr("Saved mode is unavailable; system suspend will be used.", "已保存模式不可用；将使用系统待机。") :
      tr("Eligible; physical S4 resume has not been verified.", "条件检测通过；尚未验证实机 S4 恢复。"))),
    status && !enabled && status.swap_state && status.swap_state !== "absent"
      ? h(U.PanelSectionRow, {key: "cleanup"}, h(U.ButtonItem, {
          disabled: busy, onClick: () => changeEnabled(false),
        }, tr("Retry cleanup", "重试清理"))) : null,
    h(U.PanelSectionRow, {key: "save"}, h(U.ButtonItem, {
      disabled: busy || !status?.router_installed || !enabled || !supported,
      onClick: save,
    }, tr("Save", "保存"))),
    error ? h(U.PanelSectionRow, {key: "error"}, h("div", null, error)) : null);
}
export default () => ({ title: "Boiler S2/S4 switcher", content: h(Content, null) });

