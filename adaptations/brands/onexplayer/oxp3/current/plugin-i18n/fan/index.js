// Compatible with the installed Decky API v2 and native Steam React/UI.
const R = window.SP_REACT;
const U = window.DFL;
const api = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2, "OXP3 Fan");
const statusCall = api.callable("get_status");
const configureCall = api.callable("configure");
const h=R.createElement;
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
  "Firmware auto": { ja: "ファームウェア自動制御", ko: "펌웨어 자동 제어", de: "Firmware-Automatik", fr: "Mode automatique du micrologiciel", es: "Control automático del firmware", "zh-TW": "韌體自動控制" },
  "Balanced curve": { ja: "バランス曲線", ko: "균형 곡선", de: "Ausgewogene Kurve", fr: "Courbe équilibrée", es: "Curva equilibrada", "zh-TW": "均衡曲線" },
  "Performance curve": { ja: "冷却優先の曲線", ko: "냉각 우선 곡선", de: "Kurve für stärkere Kühlung", fr: "Courbe de refroidissement renforcé", es: "Curva de refrigeración intensiva", "zh-TW": "高散熱曲線" },
  "Custom curve": { ja: "カスタム曲線", ko: "사용자 지정 곡선", de: "Benutzerdefinierte Kurve", fr: "Courbe personnalisée", es: "Curva personalizada", "zh-TW": "自訂曲線" },
  "ONEXPLAYER 3 fan": { ja: "ONEXPLAYER 3 ファン", ko: "ONEXPLAYER 3 팬", de: "ONEXPLAYER 3 Lüfter", fr: "Ventilateur ONEXPLAYER 3", es: "Ventilador de ONEXPLAYER 3", "zh-TW": "ONEXPLAYER 3 風扇" },
  "Reading…": { ja: "読み込み中…", ko: "읽는 중…", de: "Wird gelesen…", fr: "Lecture…", es: "Leyendo…", "zh-TW": "讀取中…" },
  "Full speed at 85°C. Speed decreases are delayed. Stopping control restores firmware auto.": { ja: "85°C で最高回転になります。回転数の低下には遅延があります。制御を停止するとファームウェアの自動制御に戻ります。", ko: "85°C에서 최대 속도로 회전합니다. 감속은 지연됩니다. 제어를 중지하면 펌웨어 자동 모드로 돌아갑니다.", de: "Bei 85 °C läuft der Lüfter auf höchster Stufe. Die Drehzahl sinkt verzögert. Beim Beenden übernimmt wieder die Firmware.", fr: "Le ventilateur tourne à plein régime à 85 °C. La vitesse diminue avec un délai. L’arrêt du contrôle réactive le mode automatique du micrologiciel.", es: "El ventilador funciona a máxima velocidad a 85 °C. La velocidad disminuye con retraso. Al detener el control, se restablece el modo automático del firmware.", "zh-TW": "85°C 時全速運轉，降低轉速會延遲。停止控制後會恢復韌體自動控制。" },
  "{temperature}°C fan speed": { ja: "{temperature}°C でのファン回転数", ko: "{temperature}°C에서 팬 속도", de: "Lüfterdrehzahl bei {temperature} °C", fr: "Vitesse du ventilateur à {temperature} °C", es: "Velocidad del ventilador a {temperature} °C", "zh-TW": "{temperature}°C 風扇轉速" },
  "Apply custom curve": { ja: "カスタム曲線を適用", ko: "사용자 지정 곡선 적용", de: "Benutzerdefinierte Kurve anwenden", fr: "Appliquer la courbe personnalisée", es: "Aplicar curva personalizada", "zh-TW": "套用自訂曲線" },
  "OXP3 Fan": { ja: "OXP3 ファン", ko: "OXP3 팬", de: "OXP3-Lüfter", fr: "Ventilateur OXP3", es: "Ventilador de OXP3", "zh-TW": "OXP3 風扇" },
});
function Content(){
  const [data,setData]=R.useState(null);
  const [error,setError]=R.useState("");
  const [busy,setBusy]=R.useState(false);
  const [values,setValues]=R.useState([35,40,55,70,90]);
  const refresh=async()=>{try{setData(await statusCall());}catch(e){setError(String(e));}};
  R.useEffect(()=>{let live=true;const poll=async()=>{try{const d=await statusCall();if(live)setData(d);}catch(e){if(live)setError(String(e));}};poll();const id=setInterval(poll,2000);return()=>{live=false;clearInterval(id);};},[]);
  const apply=async(mode)=>{setBusy(true);setError("");try{const config={mode};if(mode==="custom")config.points=[...values.map((v,i)=>[40+i*10,v]),[85,100]];setData(await configureCall(config));}catch(e){setError(String(e));}finally{setBusy(false);}};
  const names={auto:tr("Firmware auto","固件自动"),balanced:tr("Balanced curve","均衡曲线"),performance:tr("Performance curve","高散热曲线"),custom:tr("Custom curve","自定义曲线")};
  const s=data?.status||{};
  return h(U.PanelSection,{title:tr("ONEXPLAYER 3 fan","ONEXPLAYER 3 风扇")},
    h(U.PanelSectionRow,null,h("div",null,`${names[data?.config?.mode]||tr("Reading…","读取中")} · ${s.rpm??"—"} RPM · ${s.temperature??"—"}°C`)),
    h(U.PanelSectionRow,null,h("div",{style:{fontSize:12,opacity:.8}},tr("Full speed at 85°C. Speed decreases are delayed. Stopping control restores firmware auto.","85°C 全速；降低转速有延迟。停止控制后恢复至固件自动。"))),
    ...["auto","balanced","performance"].map(mode=>h(U.PanelSectionRow,{key:mode},h(U.ButtonItem,{disabled:busy,layout:"below",onClick:()=>apply(mode)},names[mode]))),
    ...values.map((v,i)=>h(U.PanelSectionRow,{key:i},h(U.SliderField,{label:tr("{temperature}°C fan speed","{temperature}°C 风速",{temperature:40+i*10}),value:v,min:35,max:100,step:5,showValue:true,disabled:busy,onChange:n=>setValues(old=>old.map((x,j)=>i===j?n:x))}))),
    h(U.PanelSectionRow,null,h(U.ButtonItem,{disabled:busy,layout:"below",onClick:()=>apply("custom")},tr("Apply custom curve","应用自定义曲线"))),
    (error||s.error)?h(U.PanelSectionRow,null,h("div",{style:{color:"#ffb4b4"}},error||s.error)):null);
}
export default ()=>({title:h("div",null,tr("OXP3 Fan","OXP3 风扇")),content:h(Content),icon:h("span",null,"✺"),onDismount(){}});
