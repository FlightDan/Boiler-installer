"""Display languages and translations for the desktop maintenance frontends."""
from __future__ import annotations

import configparser
import os
from pathlib import Path
import re


DEFAULT_LANGUAGE = 'en-US'
LANGUAGE_NAMES = {
    'zh-CN': '简体中文',
    'en-US': 'English',
    'ja-JP': '日本語',
    'ko-KR': '한국어',
    'de-DE': 'Deutsch',
    'fr-FR': 'Français',
    'es-ES': 'Español',
    'zh-TW': '繁體中文',
}
_LANGUAGE_BY_PREFIX = {tag[:2]: tag for tag in LANGUAGE_NAMES if tag != 'zh-TW'}
_TRANSLATION_COLUMN = {tag: index for index, tag in enumerate((
    'ja-JP', 'ko-KR', 'de-DE', 'fr-FR', 'es-ES', 'zh-TW'))}


def _normalized_tag(value):
    return value.strip().lower().split('@', 1)[0].split('.', 1)[0].replace('_', '-')


def _supported_language(value):
    for entry in value.split(':'):
        tag = _normalized_tag(entry)
        if not re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]{2,8})*', tag):
            continue
        prefix, *subtags = tag.split('-')
        if prefix == 'zh':
            if 'hant' in subtags or ('hans' not in subtags and any(region in subtags for region in ('tw', 'hk', 'mo'))):
                return 'zh-TW'
            return 'zh-CN'
        if prefix in _LANGUAGE_BY_PREFIX:
            return _LANGUAGE_BY_PREFIX[prefix]
    return DEFAULT_LANGUAGE


def language(value=None, *, env=None, config_home=None):
    """Prefer Plasma's display language over the session's regional formats."""
    if value is not None:
        return _supported_language(value)
    env = os.environ if env is None else env
    config_home = Path(config_home or env.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    for filename, section, key in (('plasma-localerc', 'Translations', 'LANGUAGE'),
                                   ('kdeglobals', 'Locale', 'Language')):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(config_home / filename, encoding='utf-8')
            preference = parser.get(section, key, fallback='').strip()
        except (OSError, UnicodeError, configparser.Error):
            continue
        if preference:
            return _supported_language(preference)
    for key in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        if preference := env.get(key, '').strip():
            return _supported_language(preference)
    return DEFAULT_LANGUAGE


def localized_label(lang, value):
    """Read package and guide text while preserving the legacy zh/en fields."""
    if not isinstance(value, dict):
        return str(value or '')
    tags = {
        'zh-CN': ('zh-cn', 'zh-hans', 'zh'),
        'zh-TW': ('zh-tw', 'zh-hant'),
    }.get(lang, (lang.lower(), lang[:2]))
    labels = {_normalized_tag(key): text for key, text in value.items() if isinstance(key, str)}
    return next((labels[tag] for tag in (*tags, 'en-us', 'en')
                 if isinstance(labels.get(tag), str) and labels[tag]), '')


def translate(lang, zh, en):
    if lang == 'zh-CN':
        return zh
    column = _TRANSLATION_COLUMN.get(lang)
    translations = _MESSAGES.get(en)
    return translations[column] if column is not None and translations else en


def desktop_entry():
    """Generate the same localized KDE launcher for installation and updates."""
    name = 'Boiler Repairs and Project Updates'
    comment = 'Review, install and restore device adaptations'
    lines = ['[Desktop Entry]', 'Type=Application', 'Name=' + name, 'Comment=' + comment]
    for lang in LANGUAGE_NAMES:
        locale = lang.replace('-', '_')
        locales = (locale, 'zh_HK', 'zh_MO') if lang == 'zh-TW' else (locale, lang[:2])
        for locale in locales:
            lines.append(f'Name[{locale}]=' + translate(lang, 'Boiler 修复与项目更新', name))
            lines.append(f'Comment[{locale}]=' + translate(lang, '审阅、安装和恢复设备适配', comment))
    lines.extend(['Exec=/usr/local/bin/boiler-maintenance-ui', 'Icon=preferences-system', 'Terminal=false',
                  'Categories=System;Settings;', ''])
    return '\n'.join(lines).encode('utf-8')


# English key followed by Japanese, Korean, German, French, Spanish and Traditional Chinese.
_MESSAGES = {
    # Shared controls and application chrome.
    'Maintenance': ('メンテナンス', '유지 관리', 'Wartung', 'Maintenance', 'Mantenimiento', '維護工具'),
    'Boiler Maintenance': ('Boiler メンテナンス', 'Boiler 유지 관리', 'Boiler-Wartung',
                           'Maintenance Boiler', 'Mantenimiento de Boiler', 'Boiler 維護工具'),
    'OK': ('OK', '확인', 'OK', 'OK', 'Aceptar', '確定'),
    'Cancel': ('キャンセル', '취소', 'Abbrechen', 'Annuler', 'Cancelar', '取消'),
    'Continue': ('続行', '계속', 'Fortfahren', 'Continuer', 'Continuar', '繼續'),
    'Back': ('戻る', '뒤로', 'Zurück', 'Retour', 'Volver', '返回'),
    'Close': ('閉じる', '닫기', 'Schließen', 'Fermer', 'Cerrar', '關閉'),
    'Retry': ('再試行', '다시 시도', 'Erneut versuchen', 'Réessayer', 'Reintentar', '重試'),
    'Interface language': ('表示言語', '인터페이스 언어', 'Anzeigesprache', 'Langue de l’interface',
                           'Idioma de la interfaz', '介面語言'),
    'Boiler Repairs and Project Updates': ('Boiler 修復とプロジェクトの更新', 'Boiler 복구 및 프로젝트 업데이트',
        'Boiler-Reparaturen und Projekt-Updates', 'Réparations et mises à jour Boiler',
        'Reparaciones y actualizaciones de Boiler', 'Boiler 修復與專案更新'),
    'Review, install and restore device adaptations': ('機種別の適合設定を確認、インストール、復元します',
        '기기 설정 검토, 설치 및 복원', 'Geräteanpassungen prüfen, installieren und wiederherstellen',
        'Examiner, installer et restaurer les adaptations de l’appareil',
        'Revisar, instalar y restaurar las adaptaciones del dispositivo', '檢閱、安裝和還原裝置適配'),
    'Run this interface as the desktop user.': (
        'この画面はデスクトップのユーザーとして実行してください。', '이 인터페이스는 데스크톱 사용자로 실행하세요.',
        'Starten Sie diese Oberfläche als Desktop-Benutzer.', 'Lancez cette interface en tant qu’utilisateur du bureau.',
        'Ejecute esta interfaz como usuario de la sesión de escritorio.', '請以桌面使用者身分執行此介面。',
    ),
    'Missing /usr/bin/kdialog. Use a KDE desktop, or run in a terminal:': (
        '/usr/bin/kdialog が見つかりません。KDE デスクトップを使用するか、端末で次を実行してください。',
        '/usr/bin/kdialog를 찾을 수 없습니다. KDE 데스크톱을 사용하거나 터미널에서 다음 명령을 실행하세요.',
        '/usr/bin/kdialog fehlt. Verwenden Sie einen KDE-Desktop oder führen Sie im Terminal diesen Befehl aus:',
        '/usr/bin/kdialog est introuvable. Utilisez un bureau KDE ou lancez cette commande dans un terminal :',
        'Falta /usr/bin/kdialog. Use un escritorio KDE o ejecute este comando en una terminal:',
        '找不到 /usr/bin/kdialog。請使用 KDE 桌面，或在終端機執行以下指令。',
    ),
    'KDialog could not display the interface': ('KDialog で画面を表示できませんでした', 'KDialog에서 인터페이스를 표시하지 못했습니다',
        'KDialog konnte die Oberfläche nicht anzeigen', 'KDialog n’a pas pu afficher l’interface',
        'KDialog no pudo mostrar la interfaz', 'KDialog 無法顯示介面'),
    'Overview': ('概要', '개요', 'Übersicht', 'Vue d’ensemble', 'Resumen', '總覽'),
    'Repairs & Plugins': ('修復とプラグイン', '복구 및 플러그인', 'Reparaturen und Plugins',
                          'Réparations et extensions', 'Reparaciones y complementos', '修復與外掛程式'),
    'Repairs & plugins': ('修復とプラグイン', '복구 및 플러그인', 'Reparaturen und Plugins',
                          'Réparations et extensions', 'Reparaciones y complementos', '修復與外掛程式'),
    'System Check': ('システムチェック', '시스템 점검', 'Systemprüfung', 'Vérification du système',
                     'Comprobación del sistema', '系統檢查'),
    'Recovery': ('復元', '복원', 'Wiederherstellung', 'Récupération', 'Recuperación', '還原'),
    'Settings': ('設定', '설정', 'Einstellungen', 'Paramètres', 'Ajustes', '設定'),
    'Offline mode: ': ('オフラインモード：', '오프라인 모드: ', 'Offline-Modus: ', 'Mode hors ligne : ',
                       'Modo sin conexión: ', '離線模式：'),
    'On': ('オン', '켜짐', 'Ein', 'Activé', 'Activado', '啟用'),
    'Off': ('オフ', '꺼짐', 'Aus', 'Désactivé', 'Desactivado', '停用'),
    'Source: ': ('取得元：', '출처: ', 'Quelle: ', 'Source : ', 'Origen: ', '來源：'),
    'Status': ('状態', '상태', 'Status', 'État', 'Estado', '狀態'),
    'Version': ('バージョン', '버전', 'Version', 'Version', 'Versión', '版本'),
    'Note: ': ('注意：', '참고: ', 'Hinweis: ', 'Remarque : ', 'Nota: ', '注意：'),

    # GTK overview, releases and system check.
    'Updates found · {count} available to review': (
        '更新あり · {count} 件を確認できます', '업데이트 발견 · 검토 가능 {count}개',
        'Updates gefunden · {count} zur Prüfung', 'Mises à jour trouvées · {count} à examiner',
        'Actualizaciones encontradas · {count} para revisar', '找到更新 · 有 {count} 個版本可供檢閱',
    ),
    '{count} available to review': ('{count} 件を確認できます', '검토 가능 {count}개', '{count} zur Prüfung',
                                    '{count} à examiner', '{count} para revisar', '有 {count} 個版本可供檢閱'),
    'No updates': ('更新なし', '업데이트 없음', 'Keine Updates', 'Aucune mise à jour', 'Sin actualizaciones', '沒有更新'),
    'Offline': ('オフライン', '오프라인', 'Offline', 'Hors ligne', 'Sin conexión', '離線'),
    'Check failed': ('確認に失敗しました', '점검 실패', 'Prüfung fehlgeschlagen', 'Échec de la vérification',
                     'Error de comprobación', '檢查失敗'),
    'Unknown': ('不明', '알 수 없음', 'Unbekannt', 'Inconnu', 'Desconocido', '未知'),
    'Device adaptations': ('機種別適合設定', '기기별 설정', 'Geräteanpassungen', 'Adaptations de l’appareil',
                           'Adaptaciones del dispositivo', '機型適配'),
    'Model matched': ('機種を特定しました', '모델 일치', 'Modell erkannt', 'Modèle détecté', 'Modelo identificado', '已辨識機型'),
    'No matching model': ('一致する機種がありません', '일치하는 모델 없음', 'Kein passendes Modell',
                          'Aucun modèle correspondant', 'Ningún modelo compatible', '沒有相符機型'),
    'Multiple matches': ('複数の機種が一致しました', '여러 모델 일치', 'Mehrere passende Modelle',
                         'Plusieurs modèles correspondent', 'Coinciden varios modelos', '有多個相符機型'),
    'Model detection unavailable': ('機種を検出できません', '모델 감지 불가', 'Modellerkennung nicht verfügbar',
                                    'Détection du modèle indisponible', 'Detección de modelo no disponible', '無法辨識機型'),
    'Identifying device': ('デバイスの識別中', '기기 식별 중', 'Gerät wird erkannt', 'Identification de l’appareil',
                           'Identificando el dispositivo', '正在辨識裝置'),
    'Update status': ('更新状況', '업데이트 상태', 'Update-Status', 'État des mises à jour',
                      'Estado de las actualizaciones', '更新狀態'),
    'Checking for updates…': ('更新を確認中…', '업데이트 확인 중…', 'Updates werden gesucht…',
                              'Recherche de mises à jour…', 'Buscando actualizaciones…', '正在檢查更新…'),
    'Check failed · See System Check': ('確認に失敗しました · システムチェックを参照', '점검 실패 · 시스템 점검 참조',
                                       'Prüfung fehlgeschlagen · Siehe Systemprüfung',
                                       'Échec de la vérification · Voir Vérification du système',
                                       'Error de comprobación · Consulte Comprobación del sistema', '檢查失敗 · 請查看系統檢查'),
    'Updates have not been checked': ('更新はまだ確認していません', '아직 업데이트를 확인하지 않았습니다',
                                      'Noch nicht nach Updates gesucht', 'Mises à jour non vérifiées',
                                      'Aún no se han buscado actualizaciones', '尚未檢查更新'),
    'A model maintenance guide is available': ('機種別メンテナンスガイドがあります', '모델 유지 관리 가이드를 볼 수 있습니다',
        'Eine Wartungsanleitung für das Modell ist verfügbar', 'Un guide de maintenance du modèle est disponible',
        'Hay una guía de mantenimiento para el modelo', '有可供查閱的機型維護指南'),
    'Refresh failed · Previous results': ('更新に失敗しました · 前回の結果', '새로 고침 실패 · 이전 결과',
        'Aktualisierung fehlgeschlagen · Vorherige Ergebnisse', 'Actualisation impossible · Résultats précédents',
        'Error al actualizar · Resultados anteriores', '重新整理失敗 · 顯示上次結果'),
    'Check for updates': ('更新を確認', '업데이트 확인', 'Nach Updates suchen', 'Rechercher des mises à jour',
                          'Buscar actualizaciones', '檢查更新'),
    'Working': ('処理中', '작업 중', 'In Bearbeitung', 'Traitement en cours', 'Procesando', '處理中'),
    'Please wait; authorization may be requested.': ('しばらくお待ちください。認証が必要な場合があります。',
        '잠시 기다려 주세요. 인증이 필요할 수 있습니다.', 'Bitte warten. Möglicherweise ist eine Autorisierung erforderlich.',
        'Veuillez patienter. Une autorisation peut être nécessaire.', 'Espere. Es posible que se solicite autorización.',
        '請稍候，系統可能會要求授權。'),
    'Operation did not complete': ('操作を完了できませんでした', '작업이 완료되지 않았습니다', 'Vorgang nicht abgeschlossen',
                                   'Opération inachevée', 'La operación no se completó', '操作未完成'),
    'Check maintenance state and recovery records before continuing.': (
        '続行する前に、メンテナンスの状態と復元記録を確認してください。', '계속하기 전에 유지 관리 상태와 복원 기록을 확인하세요.',
        'Prüfen Sie vor dem Fortfahren den Wartungsstatus und die Wiederherstellungsprotokolle.',
        'Vérifiez l’état de la maintenance et les journaux de récupération avant de continuer.',
        'Revise el estado de mantenimiento y los registros de recuperación antes de continuar.',
        '請先檢查維護狀態與還原紀錄，再繼續操作。',
    ),
    'Check for updates to find releases for this device.': (
        '更新を確認して、このデバイスに対応するリリースを探してください。', '이 기기에 맞는 릴리스를 찾으려면 업데이트를 확인하세요.',
        'Suchen Sie nach Updates, um Versionen für dieses Gerät zu finden.',
        'Recherchez les mises à jour pour trouver les versions disponibles pour cet appareil.',
        'Busque actualizaciones para encontrar versiones para este dispositivo.', '檢查更新以找出適用於此裝置的版本。',
    ),
    'Installed components': ('インストール済みコンポーネント', '설치된 구성 요소', 'Installierte Komponenten',
                             'Composants installés', 'Componentes instalados', '已安裝元件'),
    'Installed model and SSD maintenance': ('インストール済みの機種別設定と SSD のメンテナンス', '설치된 모델 설정 및 SSD 유지 관리',
        'Wartung installierter Geräteanpassungen und SSD', 'Maintenance des adaptations installées et du SSD',
        'Mantenimiento de las adaptaciones instaladas y del SSD', '維護已安裝的機型適配與 SSD'),
    'Run system check': ('システムチェックを実行', '시스템 점검 실행', 'Systemprüfung starten',
                         'Lancer la vérification du système', 'Ejecutar la comprobación del sistema', '執行系統檢查'),
    'View maintenance state and recovery options': ('メンテナンス状況と復元オプションを表示', '유지 관리 상태 및 복원 옵션 보기',
        'Wartungsstatus und Wiederherstellungsoptionen anzeigen', 'Afficher l’état de la maintenance et les options de récupération',
        'Ver el estado de mantenimiento y las opciones de recuperación', '查看維護狀態與還原選項'),
    'Model match': ('機種の照合', '모델 일치 여부', 'Modellerkennung', 'Détection du modèle', 'Detección del modelo', '機型辨識'),
    'Model': ('機種', '모델', 'Modell', 'Modèle', 'Modelo', '機型'),
    'Device identity': ('デバイス識別情報', '기기 식별 정보', 'Gerätekennung', 'Identité de l’appareil',
                        'Identidad del dispositivo', '裝置識別資料'),
    'Fallback source': ('代替の取得元', '대체 출처', 'Alternative Quelle', 'Source de secours', 'Origen alternativo', '備用來源'),
    'Open original maintenance guide': ('元のメンテナンスガイドを開く', '원본 유지 관리 가이드 열기',
        'Ursprüngliche Wartungsanleitung öffnen', 'Ouvrir le guide de maintenance d’origine',
        'Abrir la guía de mantenimiento original', '開啟原始維護指南'),
    'Opti2XMX upstream check': ('Opti2XMX の配布元を確認', 'Opti2XMX 원본 프로젝트 확인',
        'Opti2XMX-Originalprojekt prüfen', 'Vérification du projet Opti2XMX',
        'Comprobación del proyecto Opti2XMX', '檢查 Opti2XMX 上游版本'),
    'New upstream Opti2XMX release': ('Opti2XMX の配布元に新しいリリースがあります', 'Opti2XMX 원본 프로젝트의 새 릴리스',
        'Neue Version des Opti2XMX-Originalprojekts', 'Nouvelle version du projet Opti2XMX',
        'Nueva versión del proyecto Opti2XMX', 'Opti2XMX 上游有新版本'),
    'This is the upstream version. Installable updates come from signed Boiler packages.': (
        '配布元のバージョンです。インストール可能な更新は、署名済みの Boiler パッケージから提供されます。',
        '원본 프로젝트의 버전입니다. 설치 가능한 업데이트는 서명된 Boiler 패키지에서 제공됩니다.',
        'Dies ist die Version des Originalprojekts. Installierbare Updates stammen aus signierten Boiler-Paketen.',
        'Cette version provient du projet d’origine. Les mises à jour installables sont fournies par des paquets Boiler signés.',
        'Esta versión procede del proyecto original. Las actualizaciones instalables se distribuyen en paquetes firmados de Boiler.',
        '此為上游專案版本。可安裝的更新由 Boiler 簽署的套件提供。',
    ),
    'Project updates': ('プロジェクトの更新', '프로젝트 업데이트', 'Projekt-Updates', 'Mises à jour du projet',
                        'Actualizaciones del proyecto', '專案更新'),
    'Review {channel} {version}': ('{channel} {version} を確認', '{channel} {version} 검토', '{channel} {version} prüfen',
                                   'Examiner {channel} {version}', 'Revisar {channel} {version}', '檢閱 {channel} {version}'),
    'View other releases': ('その他のリリースを表示', '다른 릴리스 보기', 'Andere Versionen anzeigen',
                            'Voir les autres versions', 'Ver otras versiones', '查看其他版本'),
    'Historical release, for reference': ('過去のリリース（参照用）', '이전 릴리스 (참고용)', 'Ältere Version zur Referenz',
                                          'Ancienne version pour référence', 'Versión histórica de referencia', '歷史版本，僅供參考'),
    'Model component catalog ({count} items)': ('機種別コンポーネント一覧（{count} 件）', '모델 구성 요소 목록 ({count}개)',
        'Modellkomponenten ({count})', 'Catalogue des composants du modèle ({count})',
        'Catálogo de componentes del modelo ({count})', '機型元件目錄（{count} 項）'),
    'System check results': ('システムチェックの結果', '시스템 점검 결과', 'Ergebnisse der Systemprüfung',
                             'Résultats de la vérification du système', 'Resultados de la comprobación del sistema', '系統檢查結果'),

    # GTK plan, inventory and recovery.
    'Maintenance and recovery': ('メンテナンスと復元', '유지 관리 및 복원', 'Wartung und Wiederherstellung',
                                 'Maintenance et récupération', 'Mantenimiento y recuperación', '維護與還原'),
    'Recover interrupted transactions': ('中断した処理を復旧', '중단된 트랜잭션 복구', 'Unterbrochene Transaktionen wiederherstellen',
        'Récupérer les transactions interrompues', 'Recuperar transacciones interrumpidas', '復原中斷的交易'),
    'Verify: ': ('検証：', '검증: ', 'Prüfen: ', 'Vérifier : ', 'Verificar: ', '驗證：'),
    'Restore: ': ('復元：', '복원: ', 'Wiederherstellen: ', 'Restaurer : ', 'Restaurar: ', '還原：'),
    'Verified': ('検証済み', '검증됨', 'Verifiziert', 'Vérifié', 'Verificado', '已通過驗證'),
    'Not verified': ('未検証', '검증되지 않음', 'Nicht verifiziert', 'Non vérifié', 'Sin verificar', '未通過驗證'),
    'Modified: ': ('変更あり：', '변경됨: ', 'Geändert: ', 'Modifié : ', 'Modificado: ', '已變更：'),
    'No components are recorded.': ('記録済みのコンポーネントはありません。', '기록된 구성 요소가 없습니다.',
        'Es sind keine Komponenten erfasst.', 'Aucun composant n’est enregistré.', 'No hay componentes registrados.', '沒有已記錄的元件。'),
    'There are transactions awaiting recovery.': ('復旧が必要な処理があります。', '복구 대기 중인 트랜잭션이 있습니다.',
        'Für einige Transaktionen steht die Wiederherstellung aus.', 'Des transactions sont en attente de récupération.',
        'Hay transacciones pendientes de recuperación.', '有等待復原的交易。'),
    'Confirm operation': ('操作を確認', '작업 확인', 'Vorgang bestätigen', 'Confirmer l’opération', 'Confirmar la operación', '確認操作'),
    'Confirm and execute': ('確認して実行', '확인 후 실행', 'Bestätigen und ausführen', 'Confirmer et exécuter',
                            'Confirmar y ejecutar', '確認並執行'),
    'Select components': ('コンポーネントを選択', '구성 요소 선택', 'Komponenten auswählen', 'Sélectionner les composants',
                          'Seleccionar componentes', '選擇元件'),
    'Select components, then review the plan.': ('コンポーネントを選択してから、計画を確認してください。',
        '구성 요소를 선택한 후 계획을 검토하세요.', 'Wählen Sie die Komponenten aus und prüfen Sie anschließend den Plan.',
        'Sélectionnez les composants, puis examinez le plan.', 'Seleccione los componentes y revise el plan.', '選擇元件後，請檢閱計畫。'),
    'Not installed; updates cannot install a new component': (
        '未インストール。更新でコンポーネントを新規インストールすることはできません',
        '설치되지 않음. 업데이트로 새 구성 요소를 설치할 수 없습니다', 'Nicht installiert. Updates können keine neue Komponente installieren',
        'Composant non installé. Une mise à jour ne peut pas ajouter de composant',
        'Sin instalar. Las actualizaciones no pueden instalar un componente nuevo', '尚未安裝。更新無法安裝新元件',
    ),
    'Installed files did not pass verification': ('インストール済みファイルの検証に失敗しました', '설치된 파일 검증에 실패했습니다',
        'Installierte Dateien haben die Prüfung nicht bestanden', 'Les fichiers installés ont échoué à la vérification',
        'Los archivos instalados no superaron la verificación', '已安裝的檔案未通過驗證'),
    'This scripted maintenance entry is unsupported': ('このスクリプトのメンテナンス方式には対応していません',
        '이 스크립트 유지 관리 방식은 지원되지 않습니다', 'Dieser skriptbasierte Wartungseinstieg wird nicht unterstützt',
        'Ce point d’entrée de maintenance par script n’est pas pris en charge',
        'Este punto de entrada de mantenimiento por script no es compatible', '不支援此指令碼維護入口'),
    'Select at least one component to update.': ('更新するコンポーネントを 1 つ以上選択してください。',
        '업데이트할 구성 요소를 하나 이상 선택하세요.', 'Wählen Sie mindestens eine Komponente für das Update aus.',
        'Sélectionnez au moins un composant à mettre à jour.', 'Seleccione al menos un componente para actualizar.', '請至少選擇一個可更新的元件。'),
    'Review plan': ('計画を確認', '계획 검토', 'Plan prüfen', 'Examiner le plan', 'Revisar el plan', '檢閱計畫'),
    'Package': ('パッケージ', '패키지', 'Paket', 'Paquet', 'Paquete', '套件'),
    'Scope': ('適用範囲', '적용 범위', 'Geltungsbereich', 'Portée', 'Alcance', '適用範圍'),
    'Execution': ('実行方法', '실행 방식', 'Ausführung', 'Exécution', 'Ejecución', '執行方式'),
    'This plan cannot be executed.': ('この計画は実行できません。', '이 계획은 실행할 수 없습니다.',
        'Dieser Plan kann nicht ausgeführt werden.', 'Ce plan ne peut pas être exécuté.', 'Este plan no se puede ejecutar.', '無法執行此計畫。'),
    'Operation result': ('操作結果', '작업 결과', 'Ergebnis', 'Résultat de l’opération', 'Resultado de la operación', '操作結果'),
    'Receipt': ('実行記録', '실행 기록', 'Ausführungsnachweis', 'Compte rendu', 'Registro de ejecución', '執行紀錄'),
    'Log': ('ログ', '로그', 'Protokoll', 'Journal', 'Registro', '記錄檔'),
    'Transaction': ('トランザクション', '트랜잭션', 'Transaktion', 'Transaction', 'Transacción', '交易'),
    'Transaction: ': ('トランザクション：', '트랜잭션: ', 'Transaktion: ', 'Transaction : ', 'Transacción: ', '交易：'),
    'Back to overview': ('概要に戻る', '개요로 돌아가기', 'Zurück zur Übersicht', 'Retour à la vue d’ensemble', 'Volver al resumen', '返回總覽'),
    'Model maintenance': ('機種別メンテナンス', '모델 유지 관리', 'Modellwartung', 'Maintenance du modèle',
                          'Mantenimiento del modelo', '機型維護'),
    'This existing maintenance workflow requires kdialog.': ('このメンテナンスには kdialog が必要です。',
        '이 유지 관리 작업에는 kdialog가 필요합니다.', 'Für diesen Wartungsvorgang ist kdialog erforderlich.',
        'Cette opération de maintenance nécessite kdialog.', 'Esta tarea de mantenimiento requiere kdialog.', '此維護流程需要安裝 kdialog。'),

    # KDialog model selection and review.
    'This device package may be incompatible with the target device. You can choose whether to continue maintenance.': (
        'このパッケージは対象のデバイスと互換性がない可能性があります。メンテナンスを続けるか選択できます。',
        '이 기기 패키지는 대상 기기와 호환되지 않을 수 있습니다. 유지 관리를 계속할지 선택할 수 있습니다.',
        'Dieses Gerätepaket ist möglicherweise nicht mit dem Zielgerät kompatibel. Sie können entscheiden, ob Sie fortfahren möchten.',
        'Ce paquet peut être incompatible avec l’appareil cible. Vous pouvez choisir de poursuivre la maintenance.',
        'Es posible que el paquete sea incompatible con el dispositivo de destino. Puede decidir si desea continuar.',
        '此機型套件可能與目標裝置不相容。您可以選擇是否繼續維護。',
    ),
    'This adaptation guide does not establish runtime validation on this device.': (
        'この適合ガイドは、このデバイスでの動作検証を証明するものではありません。',
        '이 기기 설정 가이드는 현재 기기의 동작 검증을 보장하지 않습니다.',
        'Diese Anleitung belegt keine Funktionsprüfung auf diesem Gerät.',
        'Ce guide ne garantit pas la validation du fonctionnement sur cet appareil.',
        'Esta guía de adaptación no acredita una validación de funcionamiento en este dispositivo.',
        '此適配指南無法證明已在本機完成實際運作驗證。',
    ),
    'Choose a model. Guides and executable test packages are shown separately.': (
        '機種を選択してください。ガイドと実行可能なテストパッケージは別々に表示されます。',
        '모델을 선택하세요. 가이드와 실행 가능한 테스트 패키지는 별도로 표시됩니다.',
        'Wählen Sie ein Modell. Anleitungen und ausführbare Testpakete werden getrennt angezeigt.',
        'Choisissez un modèle. Les guides et les paquets de test exécutables sont présentés séparément.',
        'Seleccione un modelo. Las guías y los paquetes de prueba ejecutables se muestran por separado.',
        '選擇機型。適配指南與可執行的測試套件會分開顯示。',
    ),
    'Pinned commit: ': ('固定コミット：', '고정된 커밋: ', 'Festgelegter Commit: ', 'Commit épinglé : ',
                       'Confirmación fijada: ', '固定提交：'),
    'Offline: using the pinned local guides.': (
        'オフラインモードで固定されたローカルガイドを使用しています。', '오프라인 모드에서 고정된 로컬 가이드를 사용합니다.',
        'Offline-Modus mit den festgelegten lokalen Anleitungen.', 'Le mode hors ligne utilise les guides locaux épinglés.',
        'El modo sin conexión usa las guías locales fijadas.', '離線模式使用已固定的本機指南。',
    ),
    'Before changing this adaptation, use Restore installed adaptations to fully restore the current adaptation, then install again.\n': (
        'この適合設定を変更する前に、「インストール済みの適合設定を復元」で現在の設定を完全に復元し、再インストールしてください。\n',
        '이 설정을 변경하기 전에 ‘설치된 기기 설정 복원’으로 현재 설정을 완전히 복원한 뒤 다시 설치하세요.\n',
        'Stellen Sie die aktuelle Anpassung über „Installierte Anpassungen wiederherstellen“ vollständig wieder her und installieren Sie sie erneut.\n',
        'Utilisez « Restaurer les adaptations installées » pour restaurer entièrement l’adaptation actuelle, puis réinstallez-la.\n',
        'Use «Restaurar adaptaciones instaladas» para restaurar por completo la adaptación actual y vuelva a instalarla.\n',
        '更變適配前，請先使用「還原已安裝的適配」完整還原目前的適配，再重新安裝。\n',
    ),
    'The operation did not complete:\n': ('操作を完了できませんでした：\n', '작업이 완료되지 않았습니다: \n',
        'Vorgang nicht abgeschlossen: \n', 'L’opération n’a pas abouti : \n', 'La operación no se completó: \n', '操作未完成：\n'),
    'Options and maintenance actions': ('設定とメンテナンス操作', '옵션 및 유지 관리 작업', 'Optionen und Wartungsvorgänge',
                                       'Options et opérations de maintenance', 'Opciones y acciones de mantenimiento', '選項與維護操作'),
    'Additional device repairs': ('機種別の追加修復', '추가 기기 복구', 'Zusätzliche Gerätereparaturen',
                                  'Réparations supplémentaires de l’appareil', 'Reparaciones adicionales del dispositivo', '機型附加修復'),
    'Keyboard configuration and recovery': ('画面キーボードの設定と復元', '화상 키보드 구성 및 복원',
        'Bildschirmtastatur einrichten und wiederherstellen', 'Configuration et récupération du clavier à l’écran',
        'Configuración y recuperación del teclado en pantalla', '螢幕鍵盤設定與還原'),
    'Read the full adaptation guide': ('適合ガイドの全文を表示', '기기 설정 가이드 전체 보기', 'Vollständige Anpassungsanleitung lesen',
                                       'Lire le guide d’adaptation complet', 'Leer la guía de adaptación completa', '查看完整適配指南'),
    'Back to models': ('機種一覧に戻る', '모델 목록으로 돌아가기', 'Zurück zur Modellliste',
                       'Retour aux modèles', 'Volver a los modelos', '返回機型清單'),
    'This is an unpublished local test package. It does not establish runtime validation.\nTrust this package for this session and read this device’s maintenance state?': (
        '未公開のローカルテストパッケージです。実機での動作検証を証明するものではありません。\n'
        'このセッションに限りこのパッケージを信頼し、デバイスのメンテナンス状態を読み取りますか？',
        '공개되지 않은 로컬 테스트 패키지입니다. 실제 기기에서의 동작 검증을 보장하지 않습니다.\n'
        '이 세션에서만 패키지를 신뢰하고 기기의 유지 관리 상태를 읽을까요?',
        'Dieses lokale Testpaket ist unveröffentlicht und belegt keine Funktionsprüfung.\n'
        'Dem Paket für diese Sitzung vertrauen und den Wartungsstatus dieses Geräts lesen?',
        'Ce paquet de test local est inédit. Il ne garantit pas la validation du fonctionnement.\n'
        'Faire confiance à ce paquet pour cette session et lire l’état de maintenance de l’appareil ?',
        'Este paquete de prueba local no se ha publicado y no acredita una validación de funcionamiento.\n'
        '¿Confiar en este paquete durante esta sesión y leer el estado de mantenimiento del dispositivo?',
        '這是尚未公開發行的本機測試套件，不能證明實機運作已通過驗證。\n要在本次工作階段信任此套件並讀取本機維護狀態嗎？',
    ),
    'Guide only; no executable option.': ('ガイドのみ。実行できる項目はありません。', '가이드 전용입니다. 실행 가능한 옵션이 없습니다.',
        'Nur als Anleitung verfügbar. Kein ausführbarer Eintrag.', 'Guide uniquement. Aucune option exécutable.',
        'Solo guía. No hay una opción ejecutable.', '僅有指南，無可執行選項。'),
    'Unavailable adaptation items (guide entries are retained):\n\n': (
        '選択できない適合項目（ガイドの項目を含む）\n\n', '사용할 수 없는 기기 설정 항목 (가이드 항목 포함)\n\n',
        'Nicht verfügbare Anpassungen (einschließlich Anleitungseinträgen)\n\n',
        'Adaptations indisponibles (y compris les éléments du guide)\n\n',
        'Adaptaciones no disponibles (incluidas las entradas de la guía)\n\n', '無法選擇的適配項目（包含指南項目）\n\n',
    ),
    'This model has an adaptation guide and no executable maintenance package.': (
        'この機種には適合ガイドがありますが、実行可能なメンテナンスパッケージはありません。',
        '이 모델에는 기기 설정 가이드가 있으며, 실행 가능한 유지 관리 패키지는 없습니다.',
        'Für dieses Modell gibt es eine Anpassungsanleitung. Ein ausführbares Wartungspaket ist nicht verfügbar.',
        'Ce modèle dispose d’un guide d’adaptation. Aucun paquet de maintenance exécutable n’est disponible.',
        'Este modelo tiene una guía de adaptación. No hay un paquete de mantenimiento ejecutable.',
        '此機型提供適配指南，目前無可執行的維護套件。',
    ),
    'Install / change selected items': ('選択項目をインストール・変更', '선택한 항목 설치 또는 변경',
        'Ausgewählte Elemente installieren oder ändern', 'Installer ou modifier les éléments sélectionnés',
        'Instalar o cambiar los elementos seleccionados', '安裝或變更所選項目'),
    'Verify installed items': ('インストール済み項目を検証', '설치된 항목 검증', 'Installierte Elemente prüfen',
                             'Vérifier les éléments installés', 'Verificar los elementos instalados', '驗證已安裝項目'),
    'Recover interrupted keyboard transaction': ('中断したキーボード処理を復旧', '중단된 키보드 트랜잭션 복구',
        'Unterbrochene Tastaturtransaktion wiederherstellen', 'Récupérer la transaction de clavier interrompue',
        'Recuperar la transacción interrumpida del teclado', '復原中斷的鍵盤交易'),
    'Restore installed adaptations': ('インストール済みの適合設定を復元', '설치된 기기 설정 복원', 'Installierte Anpassungen wiederherstellen',
                                      'Restaurer les adaptations installées', 'Restaurar adaptaciones instaladas', '還原已安裝的適配'),
    'Read adaptation guide': ('適合ガイドを表示', '기기 설정 가이드 보기', 'Anpassungsanleitung lesen',
                              'Lire le guide d’adaptation', 'Leer la guía de adaptación', '查看適配指南'),
    'Installation targets the active OS slot; plugins and configuration in shared /home may also affect the other OS slot.': (
        'アクティブなシステムスロットにインストールします。共有 /home のプラグインや設定は、もう一方のスロットにも影響する場合があります。',
        '현재 활성 시스템 슬롯에 설치합니다. 공유 /home의 플러그인과 구성은 다른 시스템 슬롯에도 영향을 줄 수 있습니다.',
        'Installiert wird im aktiven System-Slot. Plugins und Einstellungen im gemeinsamen /home können auch den anderen Slot betreffen.',
        'L’installation cible l’emplacement système actif. Les extensions et les réglages dans /home partagé peuvent aussi affecter l’autre emplacement.',
        'La instalación se realiza en la ranura activa del sistema. Los complementos y ajustes de /home compartido pueden afectar a la otra ranura.',
        '安裝會套用至目前使用中的系統槽。共用 /home 裡的外掛程式與設定也可能影響另一個系統槽。',
    ),
    'Maintenance was interrupted. Choose Restore first.': ('メンテナンスが中断されました。先に復元を選択してください。',
        '유지 관리가 중단되었습니다. 먼저 복원을 선택하세요.', 'Die Wartung wurde unterbrochen. Wählen Sie zuerst die Wiederherstellung.',
        'La maintenance a été interrompue. Choisissez d’abord la restauration.',
        'El mantenimiento se interrumpió. Seleccione primero Restaurar.', '維護已中斷。請先選擇還原。'),
    'Installer configuration exists. Install it to bring it under Boiler maintenance.': (
        'インストーラーによる設定があります。Boiler のメンテナンス対象にするには、改めてインストールしてください。',
        '설치 프로그램에서 만든 구성이 있습니다. Boiler에서 유지 관리하려면 설치하세요.',
        'Eine Installer-Konfiguration ist vorhanden. Installieren Sie sie, um sie mit Boiler warten zu können.',
        'Une configuration issue de l’installateur existe. Installez-la pour qu’elle soit gérée par Boiler.',
        'Ya existe una configuración del instalador. Instálela para gestionarla con Boiler.',
        '已有安裝程式建立的設定。安裝後才會由 Boiler 維護。',
    ),
    'No installed state yet. Verify and Restore become available after installation.': (
        'インストール記録がありません。検証と復元はインストール後に利用できます。',
        '설치 기록이 없습니다. 설치 후에 검증과 복원을 사용할 수 있습니다.',
        'Es gibt noch keinen Installationsstand. Prüfen und Wiederherstellen stehen nach der Installation zur Verfügung.',
        'Aucune installation n’est enregistrée. La vérification et la restauration seront disponibles après l’installation.',
        'Aún no se ha registrado ninguna instalación. La verificación y la restauración estarán disponibles tras instalar.',
        '尚無安裝紀錄。安裝後即可驗證或還原。',
    ),
    'Changing this adaptation requires fully restoring the current adaptation first.': (
        'この適合設定を変更するには、現在の設定を先に完全に復元する必要があります。',
        '이 설정을 변경하려면 먼저 현재 기기 설정을 완전히 복원해야 합니다.',
        'Stellen Sie die aktuelle Anpassung vollständig wieder her, bevor Sie sie ändern.',
        'Restaurez entièrement l’adaptation actuelle avant de la modifier.',
        'Restaure por completo la adaptación actual antes de cambiarla.', '變更適配前，必須先完整還原目前的適配。',
    ),
    'Compatibility consent: this legacy adaptation may require keeping SteamOS updates on hold. Leave unchecked to stop.': (
        '互換性の確認。この旧式の適合設定では SteamOS の更新を一時停止したままにする必要がある場合があります。中止するにはチェックを入れないでください。',
        '호환성 확인. 이 이전 기기 설정에는 SteamOS 업데이트를 계속 보류해야 할 수 있습니다. 중단하려면 선택하지 마세요.',
        'Kompatibilitätsbestätigung. Bei dieser älteren Anpassung müssen SteamOS-Updates möglicherweise ausgesetzt bleiben. Ohne Auswahl wird der Vorgang abgebrochen.',
        'Confirmation de compatibilité. Cette ancienne adaptation peut nécessiter de maintenir les mises à jour SteamOS en pause. Laissez la case décochée pour arrêter.',
        'Confirmación de compatibilidad. Puede que esta adaptación anterior requiera mantener en pausa las actualizaciones de SteamOS. Deje la casilla sin marcar para detenerse.',
        '相容性確認。這項舊版適配可能需要繼續暫停 SteamOS 更新。若要停止，請保持未勾選。',
    ),
    'I agree to keep system updates on hold and have read the adaptation limits.': (
        'システム更新の一時停止の継続に同意し、適合設定の制限事項を確認しました。',
        '시스템 업데이트를 계속 보류하는 데 동의하며 기기 설정의 제한 사항을 읽었습니다.',
        'Ich stimme zu, Systemupdates weiterhin auszusetzen, und habe die Einschränkungen gelesen.',
        'J’accepte de maintenir les mises à jour système en pause et j’ai lu les limites de l’adaptation.',
        'Acepto mantener en pausa las actualizaciones del sistema y he leído los límites de la adaptación.',
        '我同意繼續暫停系統更新，也已閱讀適配限制。',
    ),
    'Choose the installed item to maintain': ('メンテナンスするインストール済み項目を選択', '유지 관리할 설치 항목 선택',
        'Installiertes Element für die Wartung auswählen', 'Choisir l’élément installé à maintenir',
        'Seleccione el elemento instalado que desea mantener', '選擇要維護的已安裝項目'),
    'Execute the plan just reviewed?\nThe plan specifies effects on the active OS slot and shared /home.\nA local test package does not establish runtime validation.': (
        '確認した計画を実行しますか？\nアクティブなシステムスロットと共有 /home への影響は計画に記載されています。\n'
        'ローカルテストパッケージは実機での動作検証を証明するものではありません。',
        '방금 검토한 계획을 실행할까요?\n활성 시스템 슬롯 및 공유 /home에 미치는 영향은 계획에 나와 있습니다.\n'
        '로컬 테스트 패키지는 실제 기기에서의 동작 검증을 보장하지 않습니다.',
        'Den soeben geprüften Plan ausführen?\nDer Plan beschreibt die Auswirkungen auf den aktiven System-Slot und das gemeinsame /home.\n'
        'Ein lokales Testpaket belegt keine Funktionsprüfung auf dem Gerät.',
        'Exécuter le plan qui vient d’être examiné ?\nLe plan précise les effets sur l’emplacement système actif et sur /home partagé.\n'
        'Un paquet de test local ne garantit pas la validation du fonctionnement sur l’appareil.',
        '¿Ejecutar el plan que acaba de revisar?\nEl plan detalla los efectos en la ranura activa del sistema y en /home compartido.\n'
        'Un paquete de prueba local no acredita una validación de funcionamiento en el dispositivo.',
        '要執行剛檢閱的計畫嗎？\n計畫列出對目前使用中的系統槽與共用 /home 的影響。\n本機測試套件無法證明實機運作已通過驗證。',
    ),
    'Verification failed. Installed files changed or did not pass inspection.': (
        '検証に失敗しました。インストール済みファイルが変更されたか、検査に合格しませんでした。',
        '검증에 실패했습니다. 설치된 파일이 변경되었거나 점검을 통과하지 못했습니다.',
        'Prüfung fehlgeschlagen. Installierte Dateien wurden geändert oder haben die Prüfung nicht bestanden.',
        'Échec de la vérification. Des fichiers installés ont changé ou ont échoué au contrôle.',
        'Falló la verificación. Los archivos instalados cambiaron o no superaron la comprobación.',
        '驗證失敗。已安裝的檔案已變更或未通過檢查。',
    ),
    'Operation completed.': ('操作が完了しました。', '작업이 완료되었습니다.', 'Vorgang abgeschlossen.',
                             'Opération terminée.', 'La operación se completó.', '操作已完成。'),
    'Receipt: ': ('実行記録：', '실행 기록: ', 'Ausführungsnachweis: ', 'Compte rendu : ', 'Registro de ejecución: ', '執行紀錄：'),
    'Log: ': ('ログ：', '로그: ', 'Protokoll: ', 'Journal : ', 'Registro: ', '記錄檔：'),
    'Changed files:\n': ('変更されたファイル：\n', '변경된 파일: \n', 'Geänderte Dateien: \n',
                         'Fichiers modifiés : \n', 'Archivos modificados: \n', '已變更的檔案：\n'),
    'The edited configuration was preserved in full. Keyboard settings may remain; configuration restoration is not claimed.': (
        '編集済みの設定ファイルはすべて保持されました。キーボード設定が残っている場合があります。設定の復元は確認されていません。',
        '편집된 구성 파일 전체를 보존했습니다. 키보드 설정이 남아 있을 수 있으며, 구성 복원은 확인되지 않았습니다.',
        'Die bearbeitete Konfigurationsdatei wurde vollständig erhalten. Tastatureinstellungen können weiterhin vorhanden sein. Eine Wiederherstellung wurde nicht bestätigt.',
        'Le fichier de configuration modifié a été intégralement conservé. Des réglages du clavier peuvent subsister. La restauration de la configuration n’est pas confirmée.',
        'Se conservó íntegramente el archivo de configuración editado. Es posible que queden ajustes del teclado. No se ha confirmado la restauración de la configuración.',
        '已完整保留修改過的設定檔。鍵盤設定可能仍在，尚未確認設定已還原。',
    ),
    'Restart to apply the changes.': ('変更を適用するには再起動してください。', '변경 사항을 적용하려면 다시 시작하세요.',
        'Starten Sie neu, um die Änderungen zu übernehmen.', 'Redémarrez pour appliquer les modifications.',
        'Reinicie para aplicar los cambios.', '重新啟動以套用變更。'),
    'No restart is required for this operation.': ('この操作では再起動は不要です。', '이 작업에는 다시 시작이 필요하지 않습니다.',
        'Für diesen Vorgang ist kein Neustart erforderlich.', 'Cette opération ne nécessite pas de redémarrage.',
        'Esta operación no requiere reiniciar.', '此操作無須重新啟動。'),
    'Review this operation': ('この操作を確認', '이 작업 검토', 'Vorgang prüfen', 'Examiner cette opération',
                              'Revisar esta operación', '檢閱此操作'),
    'Test package version: ': ('テストパッケージのバージョン：', '테스트 패키지 버전: ', 'Testpaketversion: ',
                               'Version du paquet de test : ', 'Versión del paquete de prueba: ', '測試套件版本：'),
    'Scope: active OS slot; plugins and configuration in shared /home may also affect the other slot.': (
        '対象はアクティブなシステムスロットです。共有 /home のプラグインや設定は、もう一方のスロットにも影響する場合があります。',
        '적용 범위는 활성 시스템 슬롯입니다. 공유 /home의 플러그인과 구성은 다른 슬롯에도 영향을 줄 수 있습니다.',
        'Die Änderungen gelten für den aktiven System-Slot. Plugins und Einstellungen im gemeinsamen /home können auch den anderen Slot betreffen.',
        'La portée est l’emplacement système actif. Les extensions et les réglages dans /home partagé peuvent aussi affecter l’autre emplacement.',
        'El alcance es la ranura activa del sistema. Los complementos y ajustes de /home compartido pueden afectar a la otra ranura.',
        '適用範圍為目前使用中的系統槽。共用 /home 裡的外掛程式與設定也可能影響另一個系統槽。',
    ),
    'Scope: ': ('適用範囲：', '적용 범위: ', 'Geltungsbereich: ', 'Portée : ', 'Alcance: ', '適用範圍：'),
    'Active slot: ': ('アクティブなスロット：', '활성 슬롯: ', 'Aktiver Slot: ', 'Emplacement actif : ', 'Ranura activa: ', '使用中的系統槽：'),
    'Partition PARTUUID: ': ('パーティションの PARTUUID：', '파티션 PARTUUID: ', 'Partitions-PARTUUID: ',
                              'PARTUUID de la partition : ', 'PARTUUID de la partición: ', '分割區 PARTUUID：'),
    'Items (including required dependencies):': ('項目（必要な依存関係を含む）', '항목 (필수 종속 항목 포함)',
        'Elemente (mit erforderlichen Abhängigkeiten)', 'Éléments (avec les dépendances requises)',
        'Elementos (incluidas las dependencias necesarias)', '項目（包含必要相依項目）'),
    'Recover items recorded by the interrupted operation.': (
        '中断した操作に記録されている項目を復旧します。', '중단된 작업에 기록된 항목을 복구합니다.',
        'Die für den unterbrochenen Vorgang erfassten Elemente werden wiederhergestellt.',
        'Récupération des éléments enregistrés par l’opération interrompue.',
        'Se recuperan los elementos registrados por la operación interrumpida.', '復原中斷操作所記錄的項目。',
    ),
    'Add: ': ('追加：', '추가: ', 'Hinzufügen: ', 'Ajouts : ', 'Añadir: ', '新增：'),
    'Remove: ': ('削除：', '제거: ', 'Entfernen: ', 'Suppressions : ', 'Quitar: ', '移除：'),
    'Files affected: ': ('対象ファイル数：', '영향받는 파일: ', 'Betroffene Dateien: ', 'Fichiers concernés : ',
                         'Archivos afectados: ', '涉及檔案數：'),
    'Details: ': ('詳細：', '상세 정보: ', 'Details: ', 'Détails : ', 'Detalles: ', '詳細資料：'),
    'Status: ': ('状態：', '상태: ', 'Status: ', 'État : ', 'Estado: ', '狀態：'),
    'Choose repairs': ('修復を選択', '복구 항목 선택', 'Reparaturen auswählen', 'Choisir les réparations', 'Seleccionar reparaciones', '選擇修復項目'),
    'Choose plugins': ('プラグインを選択', '플러그인 선택', 'Plugins auswählen', 'Choisir les extensions',
                       'Seleccionar complementos', '選擇外掛程式'),

    # SSD-specific maintenance.
    'Optional SSD repair': ('任意の SSD 修復', '선택 사항인 SSD 복구', 'Optionale SSD-Reparatur',
                            'Réparation SSD facultative', 'Reparación opcional del SSD', '選用的 SSD 修復'),
    'This repair is unavailable for the current SSD.': ('現在の SSD にはこの修復を適用できません。',
        '현재 SSD에는 이 복구를 사용할 수 없습니다.', 'Diese Reparatur ist für die aktuelle SSD nicht verfügbar.',
        'Cette réparation est indisponible pour le SSD actuel.', 'Esta reparación no está disponible para el SSD actual.',
        '目前使用的 SSD 不適用此修復。'),
    'Recover interrupted transaction': ('中断した処理を復旧', '중단된 트랜잭션 복구', 'Unterbrochene Transaktion wiederherstellen',
        'Récupérer la transaction interrompue', 'Recuperar la transacción interrumpida', '復原中斷的交易'),
    'Select and install SSD repair': ('SSD 修復を選択してインストール', 'SSD 복구 선택 및 설치', 'SSD-Reparatur auswählen und installieren',
        'Sélectionner et installer la réparation SSD', 'Seleccionar e instalar la reparación del SSD', '選擇並安裝 SSD 修復'),
    'Verify SSD repair': ('SSD 修復を検証', 'SSD 복구 검증', 'SSD-Reparatur prüfen', 'Vérifier la réparation SSD',
                          'Verificar la reparación del SSD', '驗證 SSD 修復'),
    'Restore original configuration': ('元の設定を復元', '원래 구성 복원', 'Ursprüngliche Konfiguration wiederherstellen',
                                       'Restaurer la configuration d’origine', 'Restaurar la configuración original', '還原原始設定'),
    'This optional repair is only for the Predator SSD GM7 1TB. It changes the current slot EFI boot configuration and its persistence drop-in.': (
        'この任意の修復は Predator SSD GM7 1TB 専用です。現在のスロットの EFI ブート設定と、それを保持する追加設定ファイルを変更します。',
        '이 선택적 복구는 Predator SSD GM7 1TB 전용입니다. 현재 슬롯의 EFI 부팅 구성과 이를 유지하는 추가 설정 파일을 변경합니다.',
        'Diese optionale Reparatur gilt nur für die Predator SSD GM7 mit 1 TB. Sie ändert die EFI-Startkonfiguration des aktuellen Slots und die ergänzende Datei zur dauerhaften Speicherung.',
        'Cette réparation facultative est réservée au Predator SSD GM7 de 1 To. Elle modifie la configuration de démarrage EFI de l’emplacement actuel et son fichier de persistance.',
        'Esta reparación opcional es exclusiva del Predator SSD GM7 de 1 TB. Cambia la configuración de arranque EFI de la ranura actual y su archivo de configuración persistente.',
        '此選用修復僅適用於 Predator SSD GM7 1TB。它會變更目前系統槽的 EFI 開機設定及用於保留該設定的附加檔案。',
    ),
    'Review this SSD operation': ('SSD 操作を確認', '이 SSD 작업 검토', 'SSD-Vorgang prüfen', 'Examiner cette opération SSD',
                                  'Revisar esta operación del SSD', '檢閱此 SSD 操作'),
    'Slot: ': ('スロット：', '슬롯: ', 'Slot: ', 'Emplacement : ', 'Ranura: ', '系統槽：'),
    'Root PARTUUID: ': ('ルートの PARTUUID：', '루트 PARTUUID: ', 'PARTUUID der Root-Partition: ',
                       'PARTUUID de la partition racine : ', 'PARTUUID de la partición raíz: ', '根分割區 PARTUUID：'),
    'EFI PARTUUID: ': ('EFI の PARTUUID：', 'EFI PARTUUID: ', 'PARTUUID der EFI-Partition: ',
                      'PARTUUID de la partition EFI : ', 'PARTUUID de la partición EFI: ', 'EFI 分割區 PARTUUID：'),
    'Files: ': ('対象ファイル：', '대상 파일: ', 'Dateien: ', 'Fichiers : ', 'Archivos: ', '涉及檔案：'),
    'Physical-device behavior has not been validated. Restart after installation or restoration to apply boot parameters.': (
        '実機での動作は検証されていません。ブートパラメーターを適用するには、インストールまたは復元後に再起動してください。',
        '실제 기기의 동작은 검증되지 않았습니다. 부팅 매개변수를 적용하려면 설치 또는 복원 후 다시 시작하세요.',
        'Die Funktion auf dem Gerät wurde noch nicht geprüft. Starten Sie nach der Installation oder Wiederherstellung neu, um die Startparameter anzuwenden.',
        'Le comportement sur l’appareil physique n’a pas été validé. Redémarrez après l’installation ou la restauration pour appliquer les paramètres de démarrage.',
        'No se ha validado el comportamiento en un dispositivo físico. Reinicie tras la instalación o la restauración para aplicar los parámetros de arranque.',
        '尚未驗證實機效果。安裝或還原後，請重新啟動以套用開機參數。',
    ),
    'Review hash: ': ('確認用ハッシュ：', '검토 해시: ', 'Prüf-Hash: ', 'Empreinte de vérification : ', 'Hash de revisión: ', '檢閱雜湊值：'),
    'Execute the SSD operation just reviewed?': ('確認した SSD 操作を実行しますか？', '방금 검토한 SSD 작업을 실행할까요?',
        'Den soeben geprüften SSD-Vorgang ausführen?', 'Exécuter l’opération SSD qui vient d’être examinée ?',
        '¿Ejecutar la operación del SSD que acaba de revisar?', '要執行剛檢閱的 SSD 操作嗎？'),
    'Boot configuration updated.': ('ブート設定を更新しました。', '부팅 구성을 업데이트했습니다.', 'Startkonfiguration aktualisiert.',
                                    'Configuration de démarrage mise à jour.', 'Configuración de arranque actualizada.', '已更新開機設定。'),
    'Boot configuration was already set; the original recovery record is preserved.': (
        'ブート設定はすでに構成されています。元の復元記録は保持されています。', '부팅 구성이 이미 설정되었습니다. 원래 복원 기록을 보존했습니다.',
        'Die Startkonfiguration war bereits eingerichtet. Der ursprüngliche Wiederherstellungsnachweis bleibt erhalten.',
        'La configuration de démarrage était déjà en place. Le journal de récupération d’origine est conservé.',
        'La configuración de arranque ya estaba aplicada. Se conserva el registro de recuperación original.',
        '開機設定原已套用，並保留原始還原紀錄。',
    ),
    'Configuration files verified.': ('設定ファイルを検証しました。', '구성 파일을 검증했습니다.', 'Konfigurationsdateien geprüft.',
                                      'Fichiers de configuration vérifiés.', 'Archivos de configuración verificados.', '設定檔已通過驗證。'),
    'Original configuration restored.': ('元の設定を復元しました。', '원래 구성을 복원했습니다.', 'Ursprüngliche Konfiguration wiederhergestellt.',
                                          'Configuration d’origine restaurée.', 'Configuración original restaurada.', '已還原原始設定。'),
    'Interrupted transaction recovered.': ('中断した処理を復旧しました。', '중단된 트랜잭션을 복구했습니다.',
        'Unterbrochene Transaktion wiederhergestellt.', 'Transaction interrompue récupérée.', 'Transacción interrumpida recuperada.', '已復原中斷的交易。'),
    'The running kernel NVMe parameter is enabled.': ('実行中のカーネルで NVMe パラメーターが有効です。', '실행 중인 커널의 NVMe 매개변수가 활성화되었습니다.',
        'Der NVMe-Parameter des laufenden Kernels ist aktiv.', 'Le paramètre NVMe du noyau en cours d’exécution est activé.',
        'El parámetro NVMe del kernel en ejecución está habilitado.', '目前核心的 NVMe 參數已啟用。'),
    'The running kernel NVMe parameter is disabled; a restart may be required.': (
        '実行中のカーネルで NVMe パラメーターが無効です。再起動が必要な場合があります。',
        '실행 중인 커널의 NVMe 매개변수가 비활성화되었습니다. 다시 시작해야 할 수 있습니다.',
        'Der NVMe-Parameter des laufenden Kernels ist deaktiviert. Möglicherweise ist ein Neustart erforderlich.',
        'Le paramètre NVMe du noyau en cours d’exécution est désactivé. Un redémarrage peut être nécessaire.',
        'El parámetro NVMe del kernel en ejecución está deshabilitado. Es posible que deba reiniciar.',
        '目前核心的 NVMe 參數已停用，可能需要重新啟動。',
    ),
    'The running kernel NVMe parameter is unknown; a restart may be required.': (
        '実行中のカーネルの NVMe パラメーターは不明です。再起動が必要な場合があります。',
        '실행 중인 커널의 NVMe 매개변수를 확인할 수 없습니다. 다시 시작해야 할 수 있습니다.',
        'Der NVMe-Parameter des laufenden Kernels ist unbekannt. Möglicherweise ist ein Neustart erforderlich.',
        'Le paramètre NVMe du noyau en cours d’exécution est inconnu. Un redémarrage peut être nécessaire.',
        'Se desconoce el parámetro NVMe del kernel en ejecución. Es posible que deba reiniciar.',
        '無法判定目前核心的 NVMe 參數，可能需要重新啟動。',
    ),
    'The kernel parameter can be observed after the first boot.': ('初回起動後にカーネルパラメーターを確認できます。',
        '첫 부팅 후 커널 매개변수를 확인할 수 있습니다.', 'Der Kernelparameter kann nach dem ersten Start geprüft werden.',
        'Le paramètre du noyau peut être consulté après le premier démarrage.',
        'El parámetro del kernel se puede consultar tras el primer arranque.', '首次開機後才能查看核心參數。'),
    'SSD operation completed.': ('SSD 操作が完了しました。', 'SSD 작업을 완료했습니다.', 'SSD-Vorgang abgeschlossen.',
                                 'Opération SSD terminée.', 'La operación del SSD se completó.', 'SSD 操作已完成。'),
    'This result does not establish suspend or resume behavior on the device.': (
        'この結果は、デバイスのサスペンドや復帰の動作を証明するものではありません。',
        '이 결과는 기기의 절전 또는 절전 해제 동작을 검증하지 않습니다.',
        'Dieses Ergebnis belegt nicht, dass der Ruhezustand oder das Aufwachen auf dem Gerät funktioniert.',
        'Ce résultat ne valide pas le comportement de la mise en veille ni de la reprise sur l’appareil.',
        'Este resultado no acredita la suspensión ni la reanudación en el dispositivo.', '此結果無法證明裝置的睡眠或喚醒功能正常。',
    ),

    # Feedback, preview and consent.
    'Send feedback': ('フィードバックを送信', '의견 보내기', 'Feedback senden', 'Envoyer des commentaires', 'Enviar comentarios', '傳送意見回饋'),
    'Maintenance did not complete. You can also write feedback.': (
        'メンテナンスが完了しませんでした。フィードバックを記入することもできます。', '유지 관리가 완료되지 않았습니다. 의견을 직접 작성할 수도 있습니다.',
        'Die Wartung wurde nicht abgeschlossen. Sie können auch Feedback schreiben.',
        'La maintenance n’a pas abouti. Vous pouvez aussi rédiger un commentaire.',
        'El mantenimiento no se completó. También puede escribir comentarios.', '維護未完成。您也可以手動填寫意見回饋。',
    ),
    'Timed out waiting for confirmation. Sending is unconfirmed; no automatic retry will occur. The maintenance result is unchanged.': (
        '送信の確認がタイムアウトしました。送信は未確認で、自動再試行は行われません。メンテナンスの結果に変更はありません。',
        '전송 확인 시간이 초과되었습니다. 전송 여부를 확인하지 못했으며 자동으로 다시 시도하지 않습니다. 유지 관리 결과는 그대로입니다.',
        'Die Bestätigung ist nicht rechtzeitig eingetroffen. Der Versand ist unbestätigt und wird nicht automatisch wiederholt. Das Wartungsergebnis bleibt unverändert.',
        'Le délai de confirmation est dépassé. L’envoi n’est pas confirmé et ne sera pas retenté automatiquement. Le résultat de la maintenance reste inchangé.',
        'Se agotó el tiempo de espera de la confirmación. El envío sigue sin confirmar y no se reintentará automáticamente. El resultado del mantenimiento no cambia.',
        '等待傳送確認逾時。尚未確認是否送出，也不會自動重試。維護操作的結果不受影響。',
    ),
    'Report sent.': ('フィードバックを送信しました。', '의견을 보냈습니다.', 'Bericht gesendet.', 'Rapport envoyé.', 'Informe enviado.', '已傳送意見回饋。'),
    'Report was not sent. Try Send feedback later; the maintenance result is unchanged.': (
        'フィードバックは送信されませんでした。後で「フィードバックを送信」から再試行してください。メンテナンスの結果に変更はありません。',
        '의견을 보내지 못했습니다. 나중에 ‘의견 보내기’에서 다시 시도하세요. 유지 관리 결과는 그대로입니다.',
        'Der Bericht wurde nicht gesendet. Versuchen Sie es später über „Feedback senden“ erneut. Das Wartungsergebnis bleibt unverändert.',
        'Le rapport n’a pas été envoyé. Réessayez plus tard via « Envoyer des commentaires ». Le résultat de la maintenance reste inchangé.',
        'No se envió el informe. Vuelva a intentarlo más tarde desde «Enviar comentarios». El resultado del mantenimiento no cambia.',
        '未傳送意見回饋。稍後可透過「傳送意見回饋」重試。維護操作的結果不受影響。',
    ),
    ('Send a report for this failed operation? Each report requires your consent.\n'
     'Only fixed error codes, maintenance version, source, SteamOS platform label and a random report ID are sent; '
     'original errors, logs, paths and device information are excluded.\n'
     'Recipient: Boiler feedback service (Cloudflare). Sending waits at most 6 seconds.'): (
        'この操作の失敗を報告しますか？送信するたびに同意が必要です。\n'
        '定義済みのエラーコード、メンテナンスツールのバージョン、取得元、SteamOS のプラットフォーム名、ランダムな報告 ID のみを送信します。元のエラー、ログ、パス、デバイス情報は送信しません。\n'
        '送信先は Boiler フィードバックサービス (Cloudflare) です。送信の待機時間は最大 6 秒です。',
        '실패한 작업에 대한 보고서를 보낼까요? 보고서를 보낼 때마다 동의가 필요합니다.\n'
        '정해진 오류 코드, 유지 관리 도구 버전, 출처, SteamOS 플랫폼 이름 및 임의 보고서 ID만 전송합니다. 원본 오류, 로그, 경로 및 기기 정보는 보내지 않습니다.\n'
        '수신처는 Boiler 의견 서비스(Cloudflare)입니다. 전송은 최대 6초 동안 기다립니다.',
        'Einen Bericht für diesen fehlgeschlagenen Vorgang senden? Jeder Bericht erfordert Ihre Zustimmung.\n'
        'Gesendet werden nur festgelegte Fehlercodes, Wartungsversion, Quelle, SteamOS-Plattformkennung und eine zufällige Berichts-ID. Ursprüngliche Fehler, Protokolle, Pfade und Geräteinformationen sind ausgeschlossen.\n'
        'Empfänger ist der Boiler-Feedbackdienst (Cloudflare). Der Sendevorgang wartet höchstens 6 Sekunden.',
        'Envoyer un rapport sur cette opération qui a échoué ? Chaque envoi requiert votre accord.\n'
        'Seuls les codes d’erreur prédéfinis, la version de l’outil de maintenance, la source, l’identifiant de plateforme SteamOS et un identifiant de rapport aléatoire sont envoyés. Les erreurs d’origine, les journaux, les chemins et les informations sur l’appareil sont exclus.\n'
        'Le destinataire est le service de retour Boiler (Cloudflare). L’attente d’envoi dure au plus 6 secondes.',
        '¿Enviar un informe de esta operación fallida? Cada envío requiere su consentimiento.\n'
        'Solo se envían códigos de error predefinidos, la versión de mantenimiento, el origen, el identificador de plataforma SteamOS y un ID de informe aleatorio. Se excluyen los errores originales, los registros, las rutas y los datos del dispositivo.\n'
        'El destinatario es el servicio de comentarios de Boiler (Cloudflare). El envío espera hasta 6 segundos.',
        '要傳送此次操作失敗的錯誤報告嗎？每次傳送都需要您的同意。\n'
        '只會傳送固定錯誤碼、維護工具版本、來源、SteamOS 平台標示與隨機報告編號。原始錯誤、記錄檔、路徑和裝置資料均不會傳送。\n'
        '接收方為 Boiler 意見回饋服務（Cloudflare）。傳送最多等待 6 秒。',
    ),
    'Reporting is unavailable. The maintenance result is unchanged.': (
        'フィードバックを利用できません。メンテナンスの結果に変更はありません。', '의견 보내기를 사용할 수 없습니다. 유지 관리 결과는 그대로입니다.',
        'Die Feedbackfunktion ist nicht verfügbar. Das Wartungsergebnis bleibt unverändert.',
        'L’envoi de rapports est indisponible. Le résultat de la maintenance reste inchangé.',
        'El envío de informes no está disponible. El resultado del mantenimiento no cambia.', '暫時無法傳送意見回饋。維護操作的結果不受影響。',
    ),
    'Choose a feedback method': ('フィードバック方法を選択', '의견 보내기 방법 선택', 'Feedbackmethode wählen',
                                 'Choisir une méthode de retour', 'Elegir una forma de enviar comentarios', '選擇意見回饋方式'),
    'Select a log file': ('ログファイルを選択', '로그 파일 선택', 'Protokolldatei auswählen', 'Sélectionner un journal',
                          'Seleccionar un archivo de registro', '選擇記錄檔'),
    'Write feedback': ('フィードバックを記入', '의견 작성', 'Feedback schreiben', 'Rédiger un commentaire', 'Escribir comentarios', '填寫意見回饋'),
    ('Only up to the final 64 KiB of the selected file is read; only its redacted final 4 KiB is included. '
     'The file path is not uploaded, and the exact report is previewed before sending.'): (
        '選択したファイルの末尾を最大 64 KiB 読み取り、機密情報を除去した末尾の最大 4 KiB のみを含めます。ファイルのパスはアップロードしません。送信前に実際の内容を確認できます。',
        '선택한 파일의 끝부분에서 최대 64 KiB만 읽으며, 민감한 정보를 가린 끝부분 최대 4 KiB만 포함합니다. 파일 경로는 업로드하지 않으며 전송 전에 실제 내용을 미리 볼 수 있습니다.',
        'Vom Ende der ausgewählten Datei werden höchstens 64 KiB gelesen. Der Bericht enthält höchstens die letzten 4 KiB der bereinigten Daten. Der Dateipfad wird nicht hochgeladen. Vor dem Senden wird der genaue Inhalt angezeigt.',
        'Seuls les 64 Kio de fin du fichier sélectionné sont lus, au maximum. Le rapport contient jusqu’aux 4 derniers Kio après occultation. Le chemin n’est pas envoyé. Le contenu exact s’affiche avant l’envoi.',
        'Se leen como máximo los últimos 64 KiB del archivo seleccionado. El informe incluye hasta los últimos 4 KiB después de ocultar los datos sensibles. No se sube la ruta del archivo y se muestra el contenido exacto antes de enviarlo.',
        '僅讀取所選檔案末尾最多 64 KiB，並只將遮蔽敏感資料後的末尾最多 4 KiB 納入回饋。檔案路徑不會上傳，傳送前可預覽確切內容。',
    ),
    'This log could not be read. Select a nonempty regular UTF-8 or BOM-marked UTF-16 text file.': (
        'ログを読み取れませんでした。空でない UTF-8 または BOM 付き UTF-16 の通常のテキストファイルを選択してください。',
        '로그를 읽을 수 없습니다. 비어 있지 않은 UTF-8 또는 BOM이 있는 UTF-16 일반 텍스트 파일을 선택하세요.',
        'Das Protokoll konnte nicht gelesen werden. Wählen Sie eine nicht leere reguläre Textdatei in UTF-8 oder UTF-16 mit BOM.',
        'Impossible de lire ce journal. Sélectionnez un fichier texte ordinaire non vide en UTF-8 ou en UTF-16 avec BOM.',
        'No se pudo leer el registro. Seleccione un archivo de texto normal no vacío en UTF-8 o UTF-16 con BOM.',
        '無法讀取此記錄檔。請選擇非空的 UTF-8 或含 BOM 的 UTF-16 一般文字檔。',
    ),
    'Feedback title (up to 240 UTF-8 bytes)': ('フィードバックの件名（UTF-8 で最大 240 バイト）', '의견 제목 (UTF-8 최대 240바이트)',
        'Feedback-Titel (bis zu 240 UTF-8-Bytes)', 'Titre du commentaire (240 octets UTF-8 au maximum)',
        'Título de los comentarios (hasta 240 bytes UTF-8)', '回饋標題（最多 240 個 UTF-8 位元組）'),
    'Describe the problem, steps or suggestion (up to 2048 UTF-8 bytes).': (
        '問題、再現手順、または提案を記載してください（UTF-8 で最大 2048 バイト）。', '문제, 재현 단계 또는 제안을 입력하세요 (UTF-8 최대 2048바이트).',
        'Beschreiben Sie das Problem, die Schritte oder Ihren Vorschlag (bis zu 2048 UTF-8-Bytes).',
        'Décrivez le problème, les étapes de reproduction ou votre suggestion (2048 octets UTF-8 au maximum).',
        'Describa el problema, los pasos para reproducirlo o su sugerencia (hasta 2048 bytes UTF-8).',
        '請描述問題、重現步驟或建議（最多 2048 個 UTF-8 位元組）。',
    ),
    'Provide a nonempty title and description within the limits.': (
        'タイトルと説明を入力し、それぞれの上限内に収めてください。', '제목과 설명을 비워 두지 말고 제한 이내로 작성하세요.',
        'Geben Sie einen Titel und eine Beschreibung innerhalb der Längenlimits ein.',
        'Saisissez un titre et une description non vides, dans les limites indiquées.',
        'Escriba un título y una descripción sin superar los límites indicados.', '請填寫標題與描述，並符合長度限制。',
    ),
    ('These are the exact redacted contents to send. The logs field contains only the redacted final 4 KiB. '
     'Check for remaining sensitive information; the file path is not uploaded. Cancel to discard.\n\n'): (
        '機密情報を除去した送信内容です。ログ欄には除去後の末尾の最大 4 KiB のみが含まれます。機密情報が残っていないか確認してください。ファイルのパスは送信しません。キャンセルすると破棄します。\n\n',
        '민감한 정보를 가린 실제 전송 내용입니다. 로그에는 민감한 정보를 가린 끝부분 최대 4 KiB만 포함합니다. 민감한 정보가 남아 있는지 확인하세요. 파일 경로는 업로드하지 않습니다. 취소하면 내용을 버립니다.\n\n',
        'Dies ist der genaue bereinigte Inhalt. Das Protokoll enthält nur die letzten 4 KiB nach der Bereinigung. Prüfen Sie den Inhalt auf verbliebene sensible Daten. Der Dateipfad wird nicht hochgeladen. Abbrechen verwirft den Bericht.\n\n',
        'Voici le contenu exact après occultation. Le journal contient au plus les 4 derniers Kio après occultation. Vérifiez qu’il ne reste aucune information sensible. Le chemin du fichier n’est pas envoyé. Annulez pour abandonner.\n\n',
        'Este es el contenido exacto después de ocultar los datos sensibles. El campo de registros solo incluye hasta los últimos 4 KiB tras depurarlos. Compruebe si quedan datos sensibles. No se sube la ruta del archivo. Cancele para descartar.\n\n',
        '以下是遮蔽敏感資料後的確切傳送內容。記錄檔欄位只包含遮蔽後的末尾最多 4 KiB。請檢查是否仍有敏感資料。檔案路徑不會上傳。可取消以捨棄回饋。\n\n',
    ),
    ('These are the exact redacted contents to send. Check for remaining sensitive information; '
     'cancel to return and rewrite.\nNo logs or files are attached.\n\n'): (
        '機密情報を除去した送信内容です。機密情報が残っていないか確認してください。キャンセルすると入力し直せます。\nログやファイルは添付されません。\n\n',
        '민감한 정보를 가린 실제 전송 내용입니다. 민감한 정보가 남아 있는지 확인하세요. 취소하면 돌아가서 다시 작성할 수 있습니다.\n로그나 파일을 첨부하지 않습니다.\n\n',
        'Dies ist der genaue bereinigte Inhalt. Prüfen Sie, ob sensible Daten verblieben sind. Brechen Sie ab, um zurückzukehren und den Text neu zu schreiben.\nEs werden keine Protokolle oder Dateien angehängt.\n\n',
        'Voici le contenu exact après occultation. Vérifiez qu’il ne reste aucune information sensible. Annulez pour revenir à la rédaction.\nAucun journal ni fichier n’est joint.\n\n',
        'Este es el contenido exacto después de ocultar los datos sensibles. Compruebe si quedan datos sensibles. Cancele para volver a redactarlo.\nNo se adjuntan registros ni archivos.\n\n',
        '以下是遮蔽敏感資料後的確切傳送內容。請檢查是否仍有敏感資料。可以取消並重新填寫。\n不會附加記錄檔或其他檔案。\n\n',
    ),
    'Send the feedback just previewed to the Boiler feedback service (Cloudflare)? Sending waits at most 6 seconds.': (
        '確認した内容を Boiler フィードバックサービス (Cloudflare) に送信しますか？送信の待機時間は最大 6 秒です。',
        '방금 미리 본 의견을 Boiler 의견 서비스(Cloudflare)에 보낼까요? 전송은 최대 6초 동안 기다립니다.',
        'Das soeben angezeigte Feedback an den Boiler-Feedbackdienst (Cloudflare) senden? Der Sendevorgang wartet höchstens 6 Sekunden.',
        'Envoyer le commentaire que vous venez de prévisualiser au service de retour Boiler (Cloudflare) ? L’attente d’envoi dure au plus 6 secondes.',
        '¿Enviar los comentarios que acaba de revisar al servicio de comentarios de Boiler (Cloudflare)? El envío espera hasta 6 segundos.',
        '要將剛預覽的回饋傳送到 Boiler 意見回饋服務（Cloudflare）嗎？傳送最多等待 6 秒。',
    ),
}
