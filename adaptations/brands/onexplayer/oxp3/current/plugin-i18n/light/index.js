// OXP3 Light 0.1.1. Native Steam/Decky UI, matching OXP3 CPU/Fan/Battery.
// HueSync/hhd protocol provenance and licenses accompany this distribution.
const R=window.SP_REACT,U=window.DFL,h=R.createElement;
const api=window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit.connect(2,"OXP3 Light");
const statusCall=api.callable("get_status"),setZoneCall=api.callable("set_zone");
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
  "OXP3 Light": { ja: "OXP3 ライティング", ko: "OXP3 조명", de: "OXP3-Beleuchtung", fr: "Éclairage OXP3", es: "Iluminación OXP3", "zh-TW": "OXP3 燈效" },
  "About OXP3 Light": {
    "ja": "OXP3 Light について",
    "ko": "OXP3 Light 정보",
    "de": "Über OXP3 Light",
    "fr": "À propos d’OXP3 Light",
    "es": "Acerca de OXP3 Light",
    "zh-TW": "關於 OXP3 Light"
  },
  "Applying…": {
    "ja": "適用中…",
    "ko": "적용 중…",
    "de": "Wird angewendet…",
    "fr": "Application…",
    "es": "Aplicando…",
    "zh-TW": "正在套用…"
  },
  "Blue": {
    "ja": "青",
    "ko": "파란색",
    "de": "Blau",
    "fr": "Bleu",
    "es": "Azul",
    "zh-TW": "藍色"
  },
  "Brightness": {
    "ja": "明るさ",
    "ko": "밝기",
    "de": "Helligkeit",
    "fr": "Luminosité",
    "es": "Brillo",
    "zh-TW": "亮度"
  },
  "Color": {
    "ja": "色",
    "ko": "색상",
    "de": "Farbe",
    "fr": "Couleur",
    "es": "Color",
    "zh-TW": "顏色"
  },
  "Could not read lighting status. Retry below: ": {
    "ja": "ライトの状態を読み取れません。下で再試行してください: ",
    "ko": "조명 상태를 읽을 수 없습니다. 아래에서 재시도하세요: ",
    "de": "Beleuchtungsstatus nicht lesbar. Unten erneut versuchen: ",
    "fr": "État de l’éclairage illisible. Réessayez ci-dessous : ",
    "es": "No se pudo leer el estado de iluminación. Reintenta abajo: ",
    "zh-TW": "無法讀取燈光狀態，請在下方重試："
  },
  "Custom RGB": {
    "ja": "カスタム RGB",
    "ko": "사용자 지정 RGB",
    "de": "Benutzerdefiniertes RGB",
    "fr": "RGB personnalisé",
    "es": "RGB personalizado",
    "zh-TW": "自訂 RGB"
  },
  "Green": {
    "ja": "緑",
    "ko": "초록색",
    "de": "Grün",
    "fr": "Vert",
    "es": "Verde",
    "zh-TW": "綠色"
  },
  "Light enabled": {
    "ja": "ライトを有効にする",
    "ko": "조명 켜기",
    "de": "Beleuchtung aktivieren",
    "fr": "Activer l’éclairage",
    "es": "Activar iluminación",
    "zh-TW": "開啟燈光"
  },
  "Lighting": {
    "ja": "ライト",
    "ko": "조명",
    "de": "Beleuchtung",
    "fr": "Éclairage",
    "es": "Iluminación",
    "zh-TW": "燈光"
  },
  "Lighting could not be applied. The zone may be partially changed: ": {
    "ja": "ライトを適用できません。一部が変更された可能性があります: ",
    "ko": "조명을 적용할 수 없습니다. 영역이 일부 변경되었을 수 있습니다: ",
    "de": "Beleuchtung konnte nicht angewendet werden. Der Bereich wurde möglicherweise teilweise geändert: ",
    "fr": "Impossible d’appliquer l’éclairage. La zone peut être partiellement modifiée : ",
    "es": "No se pudo aplicar la iluminación. La zona puede haber cambiado parcialmente: ",
    "zh-TW": "無法套用燈光，該區域可能已部分變更："
  },
  "Lighting unavailable": {
    "ja": "ライトを利用できません",
    "ko": "조명을 사용할 수 없음",
    "de": "Beleuchtung nicht verfügbar",
    "fr": "Éclairage indisponible",
    "es": "Iluminación no disponible",
    "zh-TW": "燈光不可用"
  },
  "Orange": {
    "ja": "オレンジ",
    "ko": "주황색",
    "de": "Orange",
    "fr": "Orange",
    "es": "Naranja",
    "zh-TW": "橙色"
  },
  "Paired joystick rings": {
    "ja": "左右スティックのリング",
    "ko": "양쪽 스틱 링",
    "de": "Beide Stickringe",
    "fr": "Anneaux des deux joysticks",
    "es": "Anillos de ambos joysticks",
    "zh-TW": "左右搖桿燈環"
  },
  "Purple": {
    "ja": "紫",
    "ko": "보라색",
    "de": "Violett",
    "fr": "Violet",
    "es": "Morado",
    "zh-TW": "紫色"
  },
  "Red": {
    "ja": "赤",
    "ko": "빨간색",
    "de": "Rot",
    "fr": "Rouge",
    "es": "Rojo",
    "zh-TW": "紅色"
  },
  "Retry": {
    "ja": "再試行",
    "ko": "재시도",
    "de": "Erneut versuchen",
    "fr": "Réessayer",
    "es": "Reintentar",
    "zh-TW": "重試"
  },
  "Retry lighting": {
    "ja": "ライト設定を再試行",
    "ko": "조명 설정 재시도",
    "de": "Beleuchtung erneut versuchen",
    "fr": "Réessayer l’éclairage",
    "es": "Reintentar iluminación",
    "zh-TW": "重試燈光設定"
  },
  "Solid colors only. Slider changes apply when adjustment ends; presets and switches apply immediately. Settings are not replayed on load or resume.": {
    "ja": "単色のみ対応。スライダーは調整終了時、プリセットとスイッチは即時に適用されます。読み込み時や復帰時には設定を再適用しません。",
    "ko": "단색만 지원합니다. 슬라이더는 조정이 끝나면 적용되며 프리셋과 스위치는 즉시 적용됩니다. 로드 또는 재개 시 설정을 다시 적용하지 않습니다.",
    "de": "Nur Vollfarben. Regleränderungen werden nach der Anpassung, Vorgaben und Schalter sofort angewendet. Einstellungen werden beim Laden oder Fortsetzen nicht erneut angewendet.",
    "fr": "Couleurs unies uniquement. Les curseurs s’appliquent après réglage, les préréglages et interrupteurs immédiatement. Les paramètres ne sont pas réappliqués au chargement ou à la reprise.",
    "es": "Solo colores sólidos. Los controles se aplican al terminar el ajuste; los preajustes e interruptores, inmediatamente. Los ajustes no se reaplican al cargar o reanudar.",
    "zh-TW": "僅支援純色。滑桿調整結束後套用，預設顏色與開關立即生效。載入及喚醒時不會重放設定。"
  },
  "Top slogan": {
    "ja": "上部スローガン",
    "ko": "상단 문구",
    "de": "Oberer Schriftzug",
    "fr": "Inscription supérieure",
    "es": "Lema superior",
    "zh-TW": "頂部標語燈"
  },
  "White": {
    "ja": "白",
    "ko": "흰색",
    "de": "Weiß",
    "fr": "Blanc",
    "es": "Blanco",
    "zh-TW": "白色"
  },
  "Xbox button": {
    "ja": "Xbox ボタン",
    "ko": "Xbox 버튼",
    "de": "Xbox-Taste",
    "fr": "Bouton Xbox",
    "es": "Botón Xbox",
    "zh-TW": "Xbox 按鈕燈"
  }
});
const zoneNames={rings:()=>tr("Paired joystick rings","左右摇杆灯环"),xbox:()=>tr("Xbox button","Xbox 按钮灯"),slogan:()=>tr("Top slogan","顶部标语灯")};
const draft=()=>({enabled:true,color:[255,80,0],brightness:60});
const presets=[
  {data:"orange",label:()=>tr("Orange","橙色"),color:[255,80,0]},
  {data:"red",label:()=>tr("Red","红色"),color:[255,0,0]},
  {data:"green",label:()=>tr("Green","绿色"),color:[0,255,0]},
  {data:"blue",label:()=>tr("Blue","蓝色"),color:[0,80,255]},
  {data:"purple",label:()=>tr("Purple","紫色"),color:[160,0,255]},
  {data:"white",label:()=>tr("White","白色"),color:[255,255,255]}
];
// Steam moves dropdowns into a modal and unmounts Quick Access. Keep the
// user's session outside Content so its menu callback survives that transition.
// Nothing is replayed on mount/resume; only explicit edit completion queues RPCs.
let state={data:null,error:"",values:Object.fromEntries(Object.keys(zoneNames).map(zone=>[zone,draft()])),
  colorModes:Object.fromEntries(Object.keys(zoneNames).map(zone=>[zone,"orange"])),dirty:{},failed:{},active:null};
const listeners=new Set(),queue=new Map();
let disposed=false,writing=false,readSequence=0;
const update=patch=>{if(disposed)return;state={...state,...patch};for(const listener of listeners)listener(state);};
const refresh=async()=>{
  if(disposed||writing)return;
  const sequence=++readSequence;
  try{const data=await statusCall();if(!disposed&&sequence===readSequence)update({data,error:""});}
  catch(e){if(!disposed&&sequence===readSequence)update({error:tr("Could not read lighting status. Retry below: ","无法读取灯光状态，请重试：")+String(e)});}
};
const edit=(zone,patch)=>{
  if(disposed||!state.data?.available)return;
  const value={...state.values[zone],...patch};
  if(JSON.stringify(value)===JSON.stringify(state.values[zone]))return;
  update({values:{...state.values,[zone]:value},dirty:{...state.dirty,[zone]:true}});
};
const drain=async()=>{
  if(disposed||writing)return;
  writing=true;readSequence++;
  try{
    while(!disposed&&queue.size){
      const [zone,config]=queue.entries().next().value;queue.delete(zone);
      update({active:zone,error:""});
      try{
        const data=await setZoneCall(zone,config);
        update({data,failed:{...state.failed,[zone]:""}});
      }catch(e){update({failed:{...state.failed,[zone]:tr("Lighting could not be applied. The zone may be partially changed: ","灯光应用失败，该区域可能已部分改变：")+String(e)}});}
    }
  }finally{writing=false;update({active:null});}
};
const apply=zone=>{
  if(disposed||!state.data?.available)return;
  const value=state.values[zone];
  // Snapshot at completion, then serialize writes. Later completed edits replace
  // queued edits for that zone without dropping changes made during an RPC.
  queue.set(zone,{...value,color:[...value.color]});
  update({dirty:{...state.dirty,[zone]:false}});
  return drain();
};
const complete=(zone,patch)=>{edit(zone,patch);if(state.dirty[zone])return apply(zone);};
function Content(){
  const [view,setView]=R.useState(state);
  R.useEffect(()=>{listeners.add(setView);setView(state);void refresh();return()=>listeners.delete(setView);},[]);
  const {data,error,values,colorModes,failed,active}=view;
  const row=(key,child)=>h(U.PanelSectionRow,{key},child);
  const label=(name,value)=>h("span",{style:{display:"flex",justifyContent:"space-between",width:"100%",gap:8}},h("span",null,name),h("span",{style:{fontWeight:700,whiteSpace:"nowrap"}},value));
  const colorPatch=(zone,i,n)=>({color:state.values[zone].color.map((c,j)=>j===i?Math.round(n):c)});
  return h(R.Fragment,null,
    error||data?.error||data?.available===false?h(U.PanelSection,{title:tr("Lighting","灯光")},
      row("error",h("div",{role:"alert",style:{color:"#ffb4b4"}},error||data.error||tr("Lighting unavailable","灯光不可用"))),
      row("retry",h(U.ButtonItem,{layout:"below",disabled:!!active,onClick:refresh},tr("Retry","重试")))):null,
    ...Object.entries(zoneNames).map(([zone,name])=>{
      const value=values[zone],disabled=!data?.available;
      const hex="#"+value.color.map(v=>v.toString(16).padStart(2,"0")).join("").toUpperCase();
      const selected=colorModes[zone];
      return h(U.PanelSection,{key:zone,title:name()},
        row("enabled",h(U.ToggleField,{label:tr("Light enabled","开启灯光"),checked:value.enabled,disabled,onChange:enabled=>{edit(zone,{enabled});return apply(zone);}})),
        row("color",h(U.DropdownItem,{label:label(tr("Color","颜色"),hex),rgOptions:[...presets.map(p=>({data:p.data,label:p.label()})),{data:"custom",label:tr("Custom RGB","自定义 RGB")}],selectedOption:selected,disabled,onChange:option=>{
          if(disposed||!state.data?.available)return;
          const preset=presets.find(p=>p.data===option.data);
          if(!preset&&option.data!=="custom")return;
          update({colorModes:{...state.colorModes,[zone]:option.data}});
          if(preset){edit(zone,{color:[...preset.color]});return apply(zone);}
        }})),
        ...(selected==="custom"?[tr("Red","红色"),tr("Green","绿色"),tr("Blue","蓝色")].map((channel,i)=>row("rgb"+i,h(U.SliderField,{
          label:channel,min:0,max:255,step:1,value:value.color[i],showValue:true,disabled,
          onChange:n=>edit(zone,colorPatch(zone,i,n)),onChangeComplete:n=>complete(zone,colorPatch(zone,i,n))
        }))):[]),
        row("brightness",h(U.SliderField,{label:label(tr("Brightness","亮度"),value.brightness+"%"),min:0,max:100,step:1,value:value.brightness,showValue:false,disabled,
          onChange:n=>edit(zone,{brightness:Math.round(n)}),onChangeComplete:n=>complete(zone,{brightness:Math.round(n)})})),
        active===zone?row("applying",h("div",{role:"status",style:{fontSize:12}},tr("Applying…","正在应用…"))):null,
        failed[zone]?row("failure",h("div",{role:"alert",style:{color:"#ffb4b4"}},failed[zone])):null,
        failed[zone]?row("retry-zone",h(U.ButtonItem,{layout:"below",disabled:!!active||disabled,onClick:()=>apply(zone)},tr("Retry lighting","重试灯光设置"))):null);
    }),
    h(U.PanelSection,{title:tr("About OXP3 Light","关于 OXP3 Light")},
      row("limits",h("div",{style:{fontSize:12}},tr("Solid colors only. Slider changes apply when adjustment ends; presets and switches apply immediately. Settings are not replayed on load or resume.","仅支持纯色。滑块调整结束后自动应用，预设颜色和开关立即生效。加载及唤醒时不自动重放设置。"))),
      row("authors",h("div",{style:{fontSize:12}},"0.1.1 · HueSync: honjow / HHHHanasak1 · HHD: Antheas Kapenekakis"))));
}
const icon=()=>h("svg",{viewBox:"0 0 24 24",width:20,height:20,fill:"none",stroke:"currentColor",strokeWidth:1.7,"aria-hidden":true},h("path",{d:"M8 17h8M9 20h6M8 14a7 7 0 1 1 8 0v3H8z"}));
export default()=>({title:h("div",null,tr("OXP3 Light","OXP3 灯效")),content:h(Content),icon:icon(),onDismount(){disposed=true;readSequence++;queue.clear();listeners.clear();}});
