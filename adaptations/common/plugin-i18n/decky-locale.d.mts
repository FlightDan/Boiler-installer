type PluginLanguage = 'en' | 'zh' | 'zh-TW' | 'ja' | 'ko' | 'de' | 'fr' | 'es';
type Catalog = Record<string, Partial<Record<PluginLanguage, string>>>;

export function normalizePluginLanguage(value: unknown): PluginLanguage;
export function pluginLanguage(): PluginLanguage;
export function pluginTranslation(catalog: Catalog):
  (english: string, chinese?: string, values?: Record<string, string | number>) => string;
