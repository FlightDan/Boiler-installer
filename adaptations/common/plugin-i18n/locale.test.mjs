import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import {normalizePluginLanguage, pluginTranslation} from './decky-locale.mjs';

const root = new URL('../../', import.meta.url);
const cases = [
  ['brands/onexplayer/common/plugin-i18n/battery/index.js', ['OXP3 Battery', 'OXP3 バッテリー', 'OXP3 배터리', 'OXP3-Akku', 'Batterie OXP3', 'Batería OXP3', 'OXP3 電池']],
  ['brands/onexplayer/oxp3/current/plugin-i18n/cpu/index.js', ['OXP3 CPU', 'OXP3 CPU 設定', 'OXP3 CPU 설정', 'OXP3-CPU', 'Processeur OXP3', 'Procesador OXP3', 'OXP3 處理器']],
  ['brands/onexplayer/oxp3/current/plugin-i18n/fan/index.js', ['OXP3 Fan', 'OXP3 ファン', 'OXP3 팬', 'OXP3-Lüfter', 'Ventilateur OXP3', 'Ventilador de OXP3', 'OXP3 風扇']],
  ['brands/onexplayer/oxp3/current/plugin-i18n/light/index.js', ['OXP3 Light', 'OXP3 ライティング', 'OXP3 조명', 'OXP3-Beleuchtung', 'Éclairage OXP3', 'Iluminación OXP3', 'OXP3 燈效']],
  ['common/plugin-i18n/fingerprint/index.js', ['Fingerprint', '指紋', '지문', 'Fingerabdruck', 'Empreinte digitale', 'Huella digital', '指紋']],
  ['common/plugin-i18n/restart-windows/dist/index.js', ['Restart into Windows', '再起動して Windows を起動', 'Windows로 다시 시작', 'In Windows neu starten', 'Redémarrer sous Windows', 'Reiniciar en Windows', '重新啟動並進入 Windows']],
];

function visibleText(node) {
  if (typeof node === 'string') return [node];
  if (Array.isArray(node)) return node.flatMap(visibleText);
  if (!node || typeof node !== 'object' || !node.children) return [];
  return [
    ...['title', 'label', 'description'].flatMap(key => visibleText(node.props?.[key])),
    ...visibleText(node.children),
  ];
}

test('normalizes Steam aliases and locale variants', () => {
  for (const [input, expected] of [
    ['ja_JP.UTF-8', 'ja'], ['koreana', 'ko'], ['de-CH', 'de'], ['fr-CA', 'fr'],
    ['latam', 'es'], ['es-419', 'es'], ['tchinese', 'zh-TW'], ['zh_Hant_HK', 'zh-TW'],
    ['zh-Hans-SG', 'zh'], ['schinese', 'zh'], ['pt-BR', 'en'], ['C.UTF-8', 'en'], ['', 'en'],
  ]) assert.equal(normalizePluginLanguage(input), expected);
  const translate = pluginTranslation({});
  assert.equal(translate('English fallback {count}', undefined, {count: 3}), 'English fallback 3');
});

for (const [member, titles] of cases) {
  test(member + ' renders the displayed Steam language and English fallback', async () => {
    const source = await readFile(new URL(member, root), 'utf8');
    let initialState;
    const react = {
      createElement(kind, props, ...children) { return {kind, props, children: children.length ? children : [props?.children]}; },
      useEffect() {},
      useRef(value) { return {current: value}; },
      useState(value) { return [initialState?.shift() ?? value, () => {}]; },
    };
    const steam = {location: {}};
    const decky = {findSP: () => steam, staticClasses: {Title: 'title'}};
    const context = vm.createContext({
      console, URLSearchParams,
      SP_REACT: react, DFL: decky,
      SP_JSX: {jsx: react.createElement, jsxs: react.createElement},
      document: {documentElement: {lang: 'en-US'}},
      navigator: {language: 'en-US'},
      setInterval: () => 1, clearInterval() {},
      __DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit: {
        connect: version => ({_version: version, callable: () => () => { throw new Error('UI test must not call the backend'); }}),
      },
    });
    context.window = context;
    const entry = new vm.SourceTextModule(source, {context});
    await entry.link(() => { throw new Error('Expected a self-contained frontend'); });
    await entry.evaluate();
    const readTitle = plugin => visibleText(plugin.titleView ?? plugin.title).join('');
    let englishBody;
    for (const [index, language] of ['portuguese', 'japanese', 'koreana', 'de-AT', 'fr-CA', 'latam', 'tchinese'].entries()) {
      steam.location.search = '?LANGUAGE=' + language;
      const plugin = entry.namespace.default();
      assert.equal(readTitle(plugin), titles[index], language);
      const bootReason = member.includes('/restart-windows/')
        ? 'Windows boot entry not found. / 未找到 Windows 启动项。' : undefined;
      initialState = bootReason ? [{ready: false, reason: bootReason}] : [];
      const body = visibleText(plugin.content.kind()).join('\n');
      assert.ok(body.length > 30, language);
      if (index === 0) {
        englishBody = body;
        if (bootReason) {
          assert.ok(body.includes('Windows boot entry not found.'));
          assert.ok(!body.includes('未找到 Windows 启动项。'));
        }
      } else {
        assert.notEqual(body, englishBody, language);
        if (bootReason && language === 'fr-CA') {
          assert.ok(body.includes('L’entrée de démarrage Windows est introuvable.'));
        }
      }
    }
    steam.location.search = '';
    context.location = {search: '?LANGUAGE=koreana'};
    assert.equal(readTitle(entry.namespace.default()), titles[2]);
    context.location.search = '';
    context.document.documentElement.lang = 'fr-BE';
    assert.equal(readTitle(entry.namespace.default()), titles[4]);
    context.document.documentElement.lang = '';
    context.navigator.language = 'es-MX';
    assert.equal(readTitle(entry.namespace.default()), titles[5]);
  });
}
