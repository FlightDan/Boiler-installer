import { de } from './locales/de';
import { en } from './locales/en';
import { es } from './locales/es';
import { fr } from './locales/fr';
import { ja } from './locales/ja';
import { ko } from './locales/ko';
import { zhTW } from './locales/zh-TW';

export type Locale = 'zh' | 'en' | 'ja' | 'ko' | 'de' | 'fr' | 'es' | 'zh-TW';

function normalizeLocale(value: string): Locale {
  const [language, variant] = value.toLowerCase().replace(/_/g, '-').split('-');
  switch (language) {
    case 'schinese':
      return 'zh';
    case 'tchinese':
      return 'zh-TW';
    case 'zh':
      return ['hant', 'tw', 'hk', 'mo'].includes(variant) ? 'zh-TW' : 'zh';
    case 'japanese':
    case 'ja':
      return 'ja';
    case 'koreana':
    case 'korean':
    case 'ko':
      return 'ko';
    case 'german':
    case 'de':
      return 'de';
    case 'french':
    case 'fr':
      return 'fr';
    case 'spanish':
    case 'latam':
    case 'es':
      return 'es';
    default:
      return 'en';
  }
}

function queryLocale(search?: string): string | undefined {
  if (!search) return undefined;
  const query = new URLSearchParams(search);
  return query.get('l')?.trim()
    || query.get('lang')?.trim()
    || query.get('language')?.trim()
    || query.get('lng')?.trim();
}

type DeckyGlobals = typeof globalThis & { DFL?: { findSP?: () => Window | undefined } };

function steamWindowLocale(): string | undefined {
  // The Steam window may be closing or inaccessible from this context.
  try {
    return queryLocale((globalThis as DeckyGlobals).DFL?.findSP?.()?.location?.search);
  } catch {
    return undefined;
  }
}

export function language(): Locale {
  const requested = queryLocale(typeof location === 'undefined' ? undefined : location.search)
    || steamWindowLocale()
    || (typeof document !== 'undefined' && document.documentElement.lang.trim())
    || (typeof navigator !== 'undefined' && navigator.language.trim())
    || 'en';
  return normalizeLocale(requested);
}

type Catalog = Readonly<Partial<Record<string, string>>>;

const catalogs: Record<Exclude<Locale, 'zh'>, Catalog> = { de, en, es, fr, ja, ko, 'zh-TW': zhTW };
const english: Catalog = en;
const prefixes = Object.keys(english).filter((key) => key.endsWith('：'));

export function t(text: string): string {
  const locale = language();
  if (locale === 'zh') return text;

  const key = Object.prototype.hasOwnProperty.call(english, text)
    ? text : prefixes.find((prefix) => text.startsWith(prefix));
  if (key === undefined) return text;

  const translated = catalogs[locale][key] || english[key] || key;
  return translated + text.slice(key.length);
}
