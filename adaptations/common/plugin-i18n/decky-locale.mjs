const steamLanguages = {
  english: 'en', schinese: 'zh', tchinese: 'zh-TW', japanese: 'ja',
  koreana: 'ko', korean: 'ko', german: 'de', french: 'fr',
  spanish: 'es', latam: 'es',
};

export function normalizePluginLanguage(value) {
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

export function pluginLanguage() {
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

export function pluginTranslation(catalog) {
  return (english, chinese, values = {}) => {
    const language = pluginLanguage();
    const translated = (language === 'zh' ? chinese : catalog[english]?.[language]) || english;
    return translated.replace(/\{(\w+)\}/g, (placeholder, name) =>
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : placeholder,
    );
  };
}
