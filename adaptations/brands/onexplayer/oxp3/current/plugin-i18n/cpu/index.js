const R=window.SP_REACT, U=window.DFL, h=R.createElement;
const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,"OXP3 CPU");
const getStatus=api.callable("get_status"), configure=api.callable("configure"), restore=api.callable("restore");
const optionalCallable=name=>{try{return api.callable(name);}catch(error){return()=>Promise.reject(error);}};
const getPowerStatus=optionalCallable("get_power_status"), setPL1=optionalCallable("set_pl1"), setPL2=optionalCallable("set_pl2");
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
  "OXP3 CPU": { ja: "OXP3 CPU 設定", ko: "OXP3 CPU 설정", de: "OXP3-CPU", fr: "Processeur OXP3", es: "Procesador OXP3", "zh-TW": "OXP3 處理器" },
  "Apply CPU settings": {
    "ja": "CPU 設定を適用",
    "ko": "CPU 설정 적용",
    "de": "CPU-Einstellungen anwenden",
    "fr": "Appliquer les paramètres CPU",
    "es": "Aplicar ajustes de CPU",
    "zh-TW": "套用 CPU 設定"
  },
  "Balanced performance": {
    "ja": "バランス型パフォーマンス",
    "ko": "균형 성능",
    "de": "Ausgewogene Leistung",
    "fr": "Performance équilibrée",
    "es": "Rendimiento equilibrado",
    "zh-TW": "均衡效能"
  },
  "Balanced power saving": {
    "ja": "バランス型省電力",
    "ko": "균형 절전",
    "de": "Ausgewogenes Energiesparen",
    "fr": "Économie d’énergie équilibrée",
    "es": "Ahorro equilibrado",
    "zh-TW": "均衡節能"
  },
  "CPU efficiency and frequency": {
    "ja": "CPU 効率と周波数",
    "ko": "CPU 효율 및 주파수",
    "de": "CPU-Effizienz und Frequenz",
    "fr": "Efficacité et fréquence du CPU",
    "es": "Eficiencia y frecuencia de CPU",
    "zh-TW": "CPU 能效與頻率"
  },
  "E cores": {
    "ja": "E コア",
    "ko": "E 코어",
    "de": "E-Kerne",
    "fr": "Cœurs E",
    "es": "Núcleos E",
    "zh-TW": "E 核"
  },
  "EPP preference": {
    "ja": "EPP 優先設定",
    "ko": "EPP 선호 설정",
    "de": "EPP-Einstellung",
    "fr": "Préférence EPP",
    "es": "Preferencia EPP",
    "zh-TW": "EPP 能效偏好"
  },
  "IBC disabled": {
    "ja": "IBC 無効",
    "ko": "IBC 비활성화됨",
    "de": "IBC deaktiviert",
    "fr": "IBC désactivé",
    "es": "IBC desactivado",
    "zh-TW": "IBC 未啟用"
  },
  "IBC enabled": {
    "ja": "IBC 有効",
    "ko": "IBC 활성화됨",
    "de": "IBC aktiviert",
    "fr": "IBC activé",
    "es": "IBC activado",
    "zh-TW": "IBC 已啟用"
  },
  "Intel IBC automatic power allocation": {
    "ja": "Intel IBC 自動電力配分",
    "ko": "Intel IBC 자동 전력 할당",
    "de": "Automatische Intel-IBC-Leistungszuweisung",
    "fr": "Allocation automatique Intel IBC",
    "es": "Asignación automática de potencia Intel IBC",
    "zh-TW": "Intel IBC 自動功率分配"
  },
  "LP E cores": {
    "ja": "低電力 E コア",
    "ko": "저전력 E 코어",
    "de": "LP-E-Kerne",
    "fr": "Cœurs E basse consommation",
    "es": "Núcleos E de bajo consumo",
    "zh-TW": "低功耗 E 核"
  },
  "P cores": {
    "ja": "P コア",
    "ko": "P 코어",
    "de": "P-Kerne",
    "fr": "Cœurs P",
    "es": "Núcleos P",
    "zh-TW": "P 核"
  },
  "PL1 is synced with Steam TDP control": {
    "ja": "PL1 は Steam の TDP 制御と同期します",
    "ko": "PL1은 Steam TDP 제어와 동기화됩니다",
    "de": "PL1 ist mit der Steam-TDP-Steuerung synchronisiert",
    "fr": "PL1 est synchronisé avec le contrôle TDP de Steam",
    "es": "PL1 se sincroniza con el control TDP de Steam",
    "zh-TW": "PL1 與 Steam TDP 控制同步"
  },
  "PL1 sustained power": {
    "ja": "PL1 持続電力",
    "ko": "PL1 지속 전력",
    "de": "PL1 Dauerleistung",
    "fr": "PL1 puissance soutenue",
    "es": "PL1 potencia sostenida",
    "zh-TW": "PL1 持續功耗"
  },
  "PL2 burst power": {
    "ja": "PL2 短時間電力",
    "ko": "PL2 순간 전력",
    "de": "PL2 Spitzenleistung",
    "fr": "PL2 puissance de pointe",
    "es": "PL2 potencia máxima",
    "zh-TW": "PL2 短時功耗"
  },
  "Pending apply": {
    "ja": "適用待ち",
    "ko": "적용 대기 중",
    "de": "Anwendung ausstehend",
    "fr": "Application en attente",
    "es": "Pendiente de aplicar",
    "zh-TW": "待套用"
  },
  "Performance": {
    "ja": "パフォーマンス優先",
    "ko": "성능 우선",
    "de": "Leistung",
    "fr": "Performance",
    "es": "Rendimiento",
    "zh-TW": "效能優先"
  },
  "Power controls are unavailable or could not be read": {
    "ja": "電力制御が利用できないか読み取れません",
    "ko": "전력 제어를 사용할 수 없거나 읽을 수 없습니다",
    "de": "Leistungssteuerung nicht verfügbar oder nicht lesbar",
    "fr": "Contrôles de puissance indisponibles ou illisibles",
    "es": "Controles de potencia no disponibles o ilegibles",
    "zh-TW": "功率控制不可用或讀取失敗"
  },
  "Power limit write failed": {
    "ja": "電力制限の書き込みに失敗しました",
    "ko": "전력 제한 쓰기 실패",
    "de": "Schreiben der Leistungsgrenze fehlgeschlagen",
    "fr": "Échec de l’écriture de la limite de puissance",
    "es": "Error al escribir el límite de potencia",
    "zh-TW": "功率限制寫入失敗"
  },
  "Power limits": {
    "ja": "電力制限",
    "ko": "전력 제한",
    "de": "Leistungsgrenzen",
    "fr": "Limites de puissance",
    "es": "Límites de potencia",
    "zh-TW": "功耗限制"
  },
  "Power saving": {
    "ja": "省電力",
    "ko": "절전",
    "de": "Energiesparen",
    "fr": "Économie d’énergie",
    "es": "Ahorro de energía",
    "zh-TW": "節能優先"
  },
  "Reading IBC status…": {
    "ja": "IBC 状態を読み込み中…",
    "ko": "IBC 상태 읽는 중…",
    "de": "IBC-Status wird gelesen…",
    "fr": "Lecture de l’état IBC…",
    "es": "Leyendo estado de IBC…",
    "zh-TW": "讀取 IBC 狀態…"
  },
  "Restore original settings": {
    "ja": "元の設定に戻す",
    "ko": "원래 설정 복원",
    "de": "Ursprüngliche Einstellungen wiederherstellen",
    "fr": "Restaurer les paramètres d’origine",
    "es": "Restaurar ajustes originales",
    "zh-TW": "還原原始設定"
  },
  "Retry power controls": {
    "ja": "電力制御を再試行",
    "ko": "전력 제어 재시도",
    "de": "Leistungssteuerung erneut versuchen",
    "fr": "Réessayer les contrôles de puissance",
    "es": "Reintentar controles de potencia",
    "zh-TW": "重試功率控制"
  },
  "Writing…": {
    "ja": "書き込み中…",
    "ko": "쓰는 중…",
    "de": "Wird geschrieben…",
    "fr": "Écriture…",
    "es": "Escribiendo…",
    "zh-TW": "正在寫入…"
  },
  "Steam TDP profile is unavailable": {
    "ja": "Steam TDP プロファイルを利用できません",
    "ko": "Steam TDP 프로필을 사용할 수 없습니다",
    "de": "Das Steam-TDP-Profil ist nicht verfügbar",
    "fr": "Le profil TDP Steam est indisponible",
    "es": "El perfil de TDP de Steam no está disponible",
    "zh-TW": "Steam TDP 設定檔無法使用"
  },
  "Steam TDP update API is unavailable": {
    "ja": "Steam TDP 更新 API を利用できません",
    "ko": "Steam TDP 업데이트 API를 사용할 수 없습니다",
    "de": "Die Schnittstelle für Steam-TDP-Änderungen ist nicht verfügbar",
    "fr": "L’API de mise à jour du TDP Steam est indisponible",
    "es": "La API de actualización del TDP de Steam no está disponible",
    "zh-TW": "Steam TDP 更新介面無法使用"
  },
  "Steam TDP profile changed before apply": {
    "ja": "適用前に Steam TDP プロファイルが変更されました",
    "ko": "적용 전에 Steam TDP 프로필이 변경되었습니다",
    "de": "Das Steam-TDP-Profil wurde vor der Übernahme geändert",
    "fr": "Le profil TDP Steam a changé avant l’application",
    "es": "El perfil de TDP de Steam cambió antes de aplicar el ajuste",
    "zh-TW": "套用前 Steam TDP 設定檔已變更"
  },
  "Steam TDP profile changed during apply": {
    "ja": "適用中に Steam TDP プロファイルが変更されました",
    "ko": "적용 중에 Steam TDP 프로필이 변경되었습니다",
    "de": "Das Steam-TDP-Profil wurde während der Übernahme geändert",
    "fr": "Le profil TDP Steam a changé pendant l’application",
    "es": "El perfil de TDP de Steam cambió al aplicar el ajuste",
    "zh-TW": "套用時 Steam TDP 設定檔已變更"
  },
  "Steam TDP profile update was not confirmed": {
    "ja": "Steam TDP プロファイルの更新を確認できませんでした",
    "ko": "Steam TDP 프로필 업데이트를 확인하지 못했습니다",
    "de": "Die Aktualisierung des Steam-TDP-Profils wurde nicht bestätigt",
    "fr": "La mise à jour du profil TDP Steam n’a pas été confirmée",
    "es": "No se confirmó la actualización del perfil de TDP de Steam",
    "zh-TW": "未能確認 Steam TDP 設定檔已更新"
  },
  "power status API unavailable": {
    "ja": "電力状態 API を利用できません",
    "ko": "전력 상태 API를 사용할 수 없습니다",
    "de": "Die Leistungsstatus-Schnittstelle ist nicht verfügbar",
    "fr": "L’API d’état de la puissance est indisponible",
    "es": "La API de estado de potencia no está disponible",
    "zh-TW": "功率狀態介面無法使用"
  },
  "power status response is invalid": {
    "ja": "電力状態の応答が無効です",
    "ko": "전력 상태 응답이 올바르지 않습니다",
    "de": "Die Antwort zum Leistungsstatus ist ungültig",
    "fr": "La réponse d’état de la puissance est invalide",
    "es": "La respuesta de estado de potencia no es válida",
    "zh-TW": "功率狀態回應無效"
  },
  "power write response is invalid": {
    "ja": "電力設定の応答が無効です",
    "ko": "전력 설정 응답이 올바르지 않습니다",
    "de": "Die Antwort zum Setzen der Leistung ist ungültig",
    "fr": "La réponse à la modification de la puissance est invalide",
    "es": "La respuesta al ajustar la potencia no es válida",
    "zh-TW": "功率寫入回應無效"
  },
  "{limit} write API unavailable": {
    "ja": "{limit} の書き込み API を利用できません",
    "ko": "{limit} 쓰기 API를 사용할 수 없습니다",
    "de": "Die Schnittstelle zum Setzen von {limit} ist nicht verfügbar",
    "fr": "L’API de modification de {limit} est indisponible",
    "es": "La API para ajustar {limit} no está disponible",
    "zh-TW": "{limit} 寫入介面無法使用"
  },
  "{cores} max frequency · {count} cores · GHz": {
    "ja": "{cores} 最大周波数 · {count} コア · GHz",
    "ko": "{cores} 최대 주파수 · {count}개 코어 · GHz",
    "de": "{cores} maximale Frequenz · {count} Kerne · GHz",
    "fr": "Fréquence maximale {cores} · {count} cœurs · GHz",
    "es": "Frecuencia máxima de {cores} · {count} núcleos · GHz",
    "zh-TW": "{cores}最高頻率 · {count} 核 · GHz"
  }
});
const finiteNumber=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value));
const maxLivePL1=power=>{
  const values=Array.isArray(power?.pl1_values)?power.pl1_values:[];
  const numeric=values.filter(finiteNumber).map(Number);
  return numeric.length?Math.max(...numeric):Number(power?.pl1_watts)||10;
};
const minimumPL2=power=>Math.max(10,Number(power?.pl2_min_watts??10),maxLivePL1(power));
const nativeProfileID=()=>{
  const id=window.SystemPerfStore?.nActiveProfileGameID;
  if(id===undefined||id===null)throw new Error(tr("Steam TDP profile is unavailable","Steam TDP 配置不可用"));
  return String(id);
};
const updateNativeTDP=async(watts,profileID,isCurrent)=>{
  const store=window.settingsStore;
  const settingsModule=store&&typeof U.findModule==="function"?U.findModule(m=>m?.rV===store&&typeof m.qt==="function"):null;
  if(!settingsModule)
    throw new Error(tr("Steam TDP update API is unavailable","Steam TDP 更新接口不可用"));
  if(nativeProfileID()!==profileID)throw new Error(tr("Steam TDP profile changed before apply","应用前 Steam TDP 配置已改变"));
  // Use the same unified settings writer and fields as Steam's native TDP widget.
  await settingsModule.qt("steamos_tdp_limit",watts);
  if(!isCurrent())return;
  if(nativeProfileID()!==profileID)throw new Error(tr("Steam TDP profile changed during apply","应用时 Steam TDP 配置已改变"));
  await settingsModule.qt("steamos_tdp_limit_enabled",true);
  // Steam's bridge can return void; confirm the native widget's observed settings.
  for(let attempt=0;attempt<=15;attempt++){
    if(!isCurrent())return;
    if(nativeProfileID()!==profileID)throw new Error(tr("Steam TDP profile changed during apply","应用时 Steam TDP 配置已改变"));
    const current=store.clientSettings;
    if(current?.steamos_tdp_limit===watts&&current?.steamos_tdp_limit_enabled===true)return;
    if(attempt===15)throw new Error(tr("Steam TDP profile update was not confirmed","未确认 Steam TDP 配置已更新"));
    await new Promise(resolve=>setTimeout(resolve,100));
  }
};
function Content(){
  const names={performance:tr("Performance","性能优先"),balance_performance:tr("Balanced performance","均衡性能"),balance_power:tr("Balanced power saving","均衡节能"),power:tr("Power saving","节能优先")};
  const [data,setData]=R.useState(null),[error,setError]=R.useState(""),[busy,setBusy]=R.useState(false);
  const [epp,setEpp]=R.useState("balance_performance");
  const [caps,setCaps]=R.useState({p:4.7,e:3.6,lp:3.1});
  const initialized=R.useRef(false);
  const [power,setPower]=R.useState(null),[powerError,setPowerError]=R.useState(""),[powerWriteError,setPowerWriteError]=R.useState("");
  const [powerDraft,setPowerDraft]=R.useState({}),[powerWriting,setPowerWriting]=R.useState(false);
  const powerAlive=R.useRef(false),powerWritingRef=R.useRef(false),powerTimer=R.useRef(null);
  const powerQueue=R.useRef({}),powerDraftRef=R.useRef({}),powerReadSequence=R.useRef(0),powerWriteSequence=R.useRef(0),powerPollRef=R.useRef(null);

  R.useEffect(()=>{let live=true;const poll=async()=>{try{const d=await getStatus();if(!live)return;setData(d);if(!initialized.current){initialized.current=true;setEpp(d.config.epp||d.groups.p.epp[0]);setCaps(Object.fromEntries(["p","e","lp"].map(k=>[k,(d.config[k+"_mhz"]??d.groups[k].limit_mhz[0])/1000])));}}catch(e){if(live)setError(String(e));}};poll();const id=setInterval(poll,3000);return()=>{live=false;clearInterval(id);};},[]);

  const pollPowerRef=powerPollRef;
  R.useEffect(()=>{
    let live=true;powerAlive.current=true;
    const poll=async()=>{
      if(!live||powerWritingRef.current)return;
      const sequence=++powerReadSequence.current;
      try{
        if(typeof getPowerStatus!=="function")throw new Error(tr("power status API unavailable","功率状态接口不可用"));
        const result=await getPowerStatus();
        if(!live||sequence!==powerReadSequence.current)return;
        if(!result||typeof result!=="object")throw new Error(tr("power status response is invalid","功率状态响应无效"));
        setPower(result);setPowerError("");
      }catch(e){if(live&&sequence===powerReadSequence.current)setPowerError(`${tr("Power controls are unavailable or could not be read","功率控制不可用或读取失败")}: ${String(e)}`);}
    };
    pollPowerRef.current=poll;void poll();
    const id=setInterval(poll,1500);
    return()=>{
      live=false;powerAlive.current=false;powerWriteSequence.current++;
      if(powerTimer.current!==null){clearTimeout(powerTimer.current);powerTimer.current=null;}
      powerQueue.current={};powerDraftRef.current={};powerPollRef.current=null;
      clearInterval(id);
    };
  },[]);

  const flushPowerEdits=async()=>{
    if(!powerAlive.current||powerWritingRef.current)return;
    const edits=Object.entries(powerQueue.current);
    if(!edits.length)return;
    powerQueue.current={};powerWritingRef.current=true;setPowerWriting(true);
    const sequence=++powerWriteSequence.current;
    // Any read started before this write must not replace its newer readback.
    powerReadSequence.current++;
    let latest=null,failed=false;
    setPowerWriteError("");
    try{
      for(const [key,edit] of edits){
        const {value,profileID}=edit;
        const setter=key==="pl1"?setPL1:setPL2;
        if(typeof setter!=="function")throw new Error(tr("{limit} write API unavailable","{limit} 写入接口不可用",{limit:key.toUpperCase()}));
        if(key==="pl1"){
          await updateNativeTDP(value,profileID,()=>powerAlive.current&&sequence===powerWriteSequence.current);
          if(!powerAlive.current||sequence!==powerWriteSequence.current)return;
          if(nativeProfileID()!==profileID)throw new Error(tr("Steam TDP profile changed during apply","应用时 Steam TDP 配置已改变"));
        }
        latest=await setter(value);
        if(!powerAlive.current||sequence!==powerWriteSequence.current)return;
        if(key==="pl1"&&nativeProfileID()!==profileID)throw new Error(tr("Steam TDP profile changed during apply","应用时 Steam TDP 配置已改变"));
        if(!latest||typeof latest!=="object")throw new Error(tr("power write response is invalid","功率写入响应无效"));
      }
      if(powerAlive.current&&sequence===powerWriteSequence.current){
        setPower(latest);powerDraftRef.current={};setPowerDraft({});
      }
    }catch(e){
      failed=true;
      if(powerAlive.current&&sequence===powerWriteSequence.current){
        const attempted=edits.map(([key,{value}])=>`${key.toUpperCase()} ${value} W`).join(", ");
        setPowerWriteError(`${tr("Power limit write failed","功率限制写入失败")} (${attempted}): ${String(e)}`);
        powerQueue.current={};powerDraftRef.current={};setPowerDraft({});
      }
    }finally{
      if(powerAlive.current&&sequence===powerWriteSequence.current){
        powerWritingRef.current=false;setPowerWriting(false);
        if(failed&&powerPollRef.current)void powerPollRef.current();
      }
    }
  };
  const queuePowerEdit=(key,rawValue)=>{
    if(!powerAlive.current||powerWritingRef.current)return;
    const low=key==="pl1"?10:minimumPL2(power);
    const high=key==="pl1"?40:80;
    const value=Math.max(low,Math.min(high,Math.round(Number(rawValue))));
    let profileID;
    try{if(key==="pl1")profileID=nativeProfileID();}
    catch(e){setPowerWriteError(`${tr("Power limit write failed","功率限制写入失败")}: ${String(e)}`);return;}
    powerQueue.current={...powerQueue.current,[key]:{value,profileID}};
    powerDraftRef.current={...powerDraftRef.current,[key]:value};
    setPowerDraft(powerDraftRef.current);setPowerWriteError("");
    if(powerTimer.current!==null)clearTimeout(powerTimer.current);
    powerTimer.current=setTimeout(()=>{powerTimer.current=null;void flushPowerEdits();},250);
  };

  const perform=async(reset)=>{setBusy(true);setError("");try{setData(reset?await restore():await configure({enabled:true,epp,...Object.fromEntries(Object.entries(caps).map(([k,v])=>[k+"_mhz",Math.round(v*1000)]))}));}catch(e){setError(String(e));}finally{setBusy(false);}};
  const row=(child,key)=>h(U.PanelSectionRow,{key},child);
  const pl1Min=Number(power?.pl1_min_watts??10),pl1Max=Number(power?.pl1_max_watts??40);
  const pl2Min=minimumPL2(power),pl2Max=Number(power?.pl2_max_watts??80);
  const pl1Value=powerDraft.pl1??Number(power?.pl1_watts??pl1Min);
  const pl2Value=powerDraft.pl2??Number(power?.pl2_watts??pl2Min);
  const powerLabel=(label,value,yellow=false)=>h("span",{style:{display:"flex",alignItems:"center",justifyContent:"space-between",width:"100%",gap:8}},
    h("span",null,label),h("span",{style:{fontWeight:700,whiteSpace:"nowrap",color:yellow?"#f6c85f":"inherit"}},`${value} W`));
  const pl1Label=powerLabel(tr("PL1 sustained power","PL1 持续功耗"),pl1Value);
  const pl2Label=powerLabel(tr("PL2 burst power","PL2 短时功耗"),pl2Value,pl2Value>=52);
  const queued=Object.keys(powerDraft).length>0&&!powerWriting;
  return h(R.Fragment,null,
    h(U.PanelSection,{title:tr("Power limits","功耗限制")},
      row(h(U.SliderField,{label:pl1Label,min:pl1Min,max:pl1Max,step:1,value:pl1Value,showValue:false,disabled:!power||powerWriting,onChange:v=>queuePowerEdit("pl1",v)}),"pl1"),
      row(h(U.SliderField,{label:pl2Label,min:pl2Min,max:pl2Max,step:1,value:pl2Value,showValue:false,disabled:!power||powerWriting,onChange:v=>queuePowerEdit("pl2",v)}),"pl2"),
      row(h("div",{style:{fontSize:12}},tr("PL1 is synced with Steam TDP control","PL1 与 Steam TDP 控制同步")),"pl1-note"),
      queued?row(h("div",{style:{fontSize:12}},tr("Pending apply","待应用")),"power-pending"):null,
      powerWriting?row(h("div",{style:{fontSize:12}},tr("Writing…","正在写入…")),"power-writing"):null,
      powerError?row(h("div",{style:{color:"#ffb4b4"}},powerError),"power-error"):null,
      powerWriteError?row(h("div",{style:{color:"#ffb4b4"}},powerWriteError),"power-write-error"):null,
      powerError?row(h(U.ButtonItem,{layout:"below",disabled:powerWriting,onClick:()=>{if(powerPollRef.current)void powerPollRef.current();}},tr("Retry power controls","重试功率控制")),"power-retry"):null),
    h(U.PanelSection,{title:tr("CPU efficiency and frequency","CPU 能效与频率")},
      row(h(U.DropdownItem,{label:tr("EPP preference","EPP 能效偏好"),rgOptions:Object.entries(names).map(([data,label])=>({data,label})),selectedOption:epp,disabled:busy||!data,onChange:o=>setEpp(o.data)}),"epp"),
      ...[["p","P cores","P 核",2],["e","E cores","E 核",8],["lp","LP E cores","低功耗 E 核",4]].map(([k,en,zh,count])=>row(h(U.SliderField,{label:tr("{cores} max frequency · {count} cores · GHz","{cores}最高频率 · {count}核 · GHz",{cores:tr(en,zh),count}),min:0.8,max:(data?.groups[k].hardware_max_mhz??Math.round(caps[k]*1000))/1000,step:0.1,value:caps[k],showValue:true,disabled:busy||!data,onChange:v=>setCaps(old=>({...old,[k]:Math.round(v*10)/10}))}),k)),
      row(h(U.ButtonItem,{layout:"below",disabled:busy||!data,onClick:()=>perform(false)},tr("Apply CPU settings","应用 CPU 设置")),"apply"),
      row(h(U.ButtonItem,{layout:"below",disabled:busy||!data,onClick:()=>perform(true)},tr("Restore original settings","恢复原始设置")),"restore"),
      data?row(h("div",{style:{fontSize:12}},["p","e","lp"].map(k=>`${k.toUpperCase()}: ${[...new Set(data.groups[k].limit_mhz)].join("/")} MHz`).join(" · ")),"readback"):null,
      error?row(h("div",{style:{color:"#ffb4b4"}},error),"error"):null),
    h(U.PanelSection,{title:tr("Intel IBC automatic power allocation","Intel IBC 自动功率分配")},
      row(h("div",null,data?.ibc.enabled===true?tr("IBC enabled","IBC 已启用"):data?.ibc.enabled===false?tr("IBC disabled","IBC 未启用"):tr("Reading IBC status…","IBC 状态读取中")),"ibc")));
}
export default()=>({title:h("div",null,tr("OXP3 CPU","OXP3 处理器")),content:h(Content),icon:h("span",null,"▦"),onDismount(){}});
