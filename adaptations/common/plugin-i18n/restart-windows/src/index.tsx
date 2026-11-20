import { ButtonItem, ConfirmModal, PanelSection, PanelSectionRow, showModal, staticClasses } from '@decky/ui';
import { callable, definePlugin } from '@decky/api';
import { useEffect, useRef, useState } from 'react';
import { pluginTranslation } from '../../decky-locale.mjs';

type Status = { ready: boolean; reason: string; target: string | null };
type Reply<T> = { ok: boolean; data?: T; error?: string };
const status = callable<[], Reply<Status>>('status');
const reboot = callable<[string, boolean], Reply<{ reboot_requested: boolean }>>('reboot');
const managerStatus = callable<[], Reply<Status>>('manager_status');
const rebootManager = callable<[string, boolean], Reply<{ reboot_requested: boolean }>>('reboot_manager');
const messages = {
  'Operation failed': {ja: '操作に失敗しました', ko: '작업 실패', de: 'Vorgang fehlgeschlagen', fr: 'Échec de l’opération', es: 'La operación ha fallado', 'zh-TW': '操作失敗'},
  'Restart into Windows?': {ja: '再起動して Windows を起動しますか？', ko: 'Windows로 다시 시작하시겠습니까?', de: 'In Windows neu starten?', fr: 'Redémarrer sous Windows ?', es: '¿Reiniciar en Windows?', 'zh-TW': '重新啟動並進入 Windows？'},
  'Restart into boot manager?': {ja: 'ブートマネージャーに再起動しますか？', ko: '부팅 관리자로 다시 시작하시겠습니까?', de: 'In den Bootmanager neu starten?', fr: 'Redémarrer dans le gestionnaire de démarrage ?', es: '¿Reiniciar en el gestor de arranque?', 'zh-TW': '重新啟動並進入開機管理員？'},
  'Your session will close. Save games and files first. Only the next boot will enter Windows.': {ja: '現在のセッションは終了します。ゲームとファイルを保存してください。Windows が起動するのは次回だけです。', ko: '현재 세션이 종료됩니다. 게임과 파일을 먼저 저장하세요. 다음 부팅에만 Windows로 진입합니다.', de: 'Die Sitzung wird beendet. Speichere zuerst Spielstände und Dateien. Nur der nächste Start führt zu Windows.', fr: 'Votre session va se terminer. Enregistrez vos jeux et fichiers. Seul le prochain démarrage se fera sous Windows.', es: 'Se cerrará la sesión. Guarda primero las partidas y los archivos. Solo el próximo arranque abrirá Windows.', 'zh-TW': '目前工作階段將結束。請先儲存遊戲進度和檔案。只有下次開機會進入 Windows。'},
  'Your session will close. Save games and files first. The next boot will use the boot manager at the SteamOS entry.': {ja: '現在のセッションは終了します。ゲームとファイルを保存してください。次回は SteamOS エントリのブートマネージャーが起動します。', ko: '현재 세션이 종료됩니다. 게임과 파일을 먼저 저장하세요. 다음 부팅은 SteamOS 항목의 부팅 관리자를 사용합니다.', de: 'Die Sitzung wird beendet. Speichere zuerst Spielstände und Dateien. Beim nächsten Start wird der Bootmanager des SteamOS-Eintrags geöffnet.', fr: 'Votre session va se terminer. Enregistrez vos jeux et fichiers. Le prochain démarrage utilisera le gestionnaire de l’entrée SteamOS.', es: 'Se cerrará la sesión. Guarda primero las partidas y los archivos. El próximo arranque usará el gestor de arranque de la entrada de SteamOS.', 'zh-TW': '目前工作階段將結束。請先儲存遊戲進度和檔案。下次開機將使用 SteamOS 開機項目的開機管理員。'},
  'Restart': {ja: '再起動', ko: '다시 시작', de: 'Neu starten', fr: 'Redémarrer', es: 'Reiniciar', 'zh-TW': '重新啟動'},
  'Cancel': {ja: 'キャンセル', ko: '취소', de: 'Abbrechen', fr: 'Annuler', es: 'Cancelar', 'zh-TW': '取消'},
  'Restart requested. Starting Windows…': {ja: '再起動を要求しました。Windows を起動しています…', ko: '다시 시작을 요청했습니다. Windows를 시작하는 중…', de: 'Neustart angefordert. Windows wird gestartet…', fr: 'Redémarrage demandé. Démarrage sous Windows…', es: 'Reinicio solicitado. Iniciando Windows…', 'zh-TW': '已要求重新啟動，正在啟動 Windows…'},
  'Restart requested. Starting boot manager…': {ja: '再起動を要求しました。ブートマネージャーを起動しています…', ko: '다시 시작을 요청했습니다. 부팅 관리자를 시작하는 중…', de: 'Neustart angefordert. Der Bootmanager wird gestartet…', fr: 'Redémarrage demandé. Démarrage du gestionnaire…', es: 'Reinicio solicitado. Iniciando el gestor de arranque…', 'zh-TW': '已要求重新啟動，正在啟動開機管理員…'},
  'Switch system': {ja: 'システムの切り替え', ko: '시스템 전환', de: 'System wechseln', fr: 'Changer de système', es: 'Cambiar de sistema', 'zh-TW': '切換系統'},
  'Working…': {ja: '処理中…', ko: '처리 중…', de: 'Wird ausgeführt…', fr: 'Traitement…', es: 'Procesando…', 'zh-TW': '處理中…'},
  'Windows boot entry is ready': {ja: 'Windows の起動エントリは準備済みです', ko: 'Windows 부팅 항목이 준비되었습니다', de: 'Der Windows-Starteintrag ist bereit', fr: 'L’entrée de démarrage Windows est prête', es: 'La entrada de arranque de Windows está lista', 'zh-TW': 'Windows 開機項目已就緒'},
  'Refresh the boot status': {ja: '起動状態を更新', ko: '부팅 상태 새로 고침', de: 'Startstatus aktualisieren', fr: 'Actualiser l’état du démarrage', es: 'Actualizar el estado del arranque', 'zh-TW': '重新整理開機狀態'},
  'Restart into Windows': {ja: '再起動して Windows を起動', ko: 'Windows로 다시 시작', de: 'In Windows neu starten', fr: 'Redémarrer sous Windows', es: 'Reiniciar en Windows', 'zh-TW': '重新啟動並進入 Windows'},
  'SteamOS boot-manager entry is ready': {ja: 'SteamOS ブートマネージャーのエントリは準備済みです', ko: 'SteamOS 부팅 관리자 항목이 준비되었습니다', de: 'Der SteamOS-Bootmanager-Eintrag ist bereit', fr: 'L’entrée du gestionnaire de démarrage SteamOS est prête', es: 'La entrada del gestor de arranque de SteamOS está lista', 'zh-TW': 'SteamOS 開機管理員項目已就緒'},
  'Restart into boot manager': {ja: 'ブートマネージャーに再起動', ko: '부팅 관리자로 다시 시작', de: 'In den Bootmanager neu starten', fr: 'Redémarrer dans le gestionnaire de démarrage', es: 'Reiniciar en el gestor de arranque', 'zh-TW': '重新啟動並進入開機管理員'},
  'Refresh boot status': {ja: '起動状態を更新', ko: '부팅 상태 새로 고침', de: 'Startstatus aktualisieren', fr: 'Actualiser l’état du démarrage', es: 'Actualizar el estado del arranque', 'zh-TW': '重新整理開機狀態'},
  'Only affects the next boot. Later boots follow your default order.': {ja: '変更は次回の起動にのみ適用されます。以降は既定の起動順序に戻ります。', ko: '다음 부팅에만 적용됩니다. 이후에는 기본 부팅 순서를 따릅니다.', de: 'Gilt nur für den nächsten Start. Danach gilt wieder die Standardreihenfolge.', fr: 'S’applique uniquement au prochain démarrage. Les suivants reprendront l’ordre par défaut.', es: 'Solo afecta al próximo arranque. Los siguientes seguirán el orden predeterminado.', 'zh-TW': '只影響下次開機，之後會依原本的預設順序開機。'},
  'SteamOS boot-manager entry not found.': {ja: 'SteamOS のブートマネージャー項目が見つかりません。', ko: 'SteamOS 부팅 관리자 항목을 찾을 수 없습니다.', de: 'SteamOS-Bootmanager-Eintrag nicht gefunden.', fr: 'L’entrée du gestionnaire de démarrage SteamOS est introuvable.', es: 'No se encontró la entrada del gestor de arranque de SteamOS.', 'zh-TW': '找不到 SteamOS 開機管理員項目。'},
  'Multiple boot-manager entries found.': {ja: 'ブートマネージャー項目が複数あります。', ko: '부팅 관리자 항목이 여러 개 있습니다.', de: 'Mehrere Bootmanager-Einträge gefunden.', fr: 'Plusieurs entrées du gestionnaire de démarrage ont été trouvées.', es: 'Se encontraron varias entradas del gestor de arranque.', 'zh-TW': '找到多個開機管理員項目，請先整理開機項目。'},
  'Boot-manager entry is disabled.': {ja: 'ブートマネージャー項目が無効です。', ko: '부팅 관리자 항목이 비활성화되었습니다.', de: 'Der Bootmanager-Eintrag ist deaktiviert.', fr: 'L’entrée du gestionnaire de démarrage est désactivée.', es: 'La entrada del gestor de arranque está desactivada.', 'zh-TW': '開機管理員項目已停用。'},
  'Windows boot entry not found.': {ja: 'Windows の起動項目が見つかりません。', ko: 'Windows 부팅 항목을 찾을 수 없습니다.', de: 'Windows-Starteintrag nicht gefunden.', fr: 'L’entrée de démarrage Windows est introuvable.', es: 'No se encontró la entrada de arranque de Windows.', 'zh-TW': '找不到 Windows 開機項目。'},
  'Multiple Windows boot entries found.': {ja: 'Windows の起動項目が複数あります。', ko: 'Windows 부팅 항목이 여러 개 있습니다.', de: 'Mehrere Windows-Starteinträge gefunden.', fr: 'Plusieurs entrées de démarrage Windows ont été trouvées.', es: 'Se encontraron varias entradas de arranque de Windows.', 'zh-TW': '找到多個 Windows 開機項目，請先整理開機項目。'},
  'Windows boot entry is disabled.': {ja: 'Windows の起動項目が無効です。', ko: 'Windows 부팅 항목이 비활성화되었습니다.', de: 'Der Windows-Starteintrag ist deaktiviert.', fr: 'L’entrée de démarrage Windows est désactivée.', es: 'La entrada de arranque de Windows está desactivada.', 'zh-TW': 'Windows 開機項目已停用。'},
  'Administrator permission is required.': {ja: '管理者権限が必要です。', ko: '관리자 권한이 필요합니다.', de: 'Administratorrechte sind erforderlich.', fr: 'Les droits administrateur sont requis.', es: 'Se requieren permisos de administrador.', 'zh-TW': '需要管理員權限。'},
  'UEFI variables are unavailable.': {ja: 'UEFI 変数にアクセスできません。', ko: 'UEFI 변수에 접근할 수 없습니다.', de: 'UEFI-Variablen sind nicht verfügbar.', fr: 'Les variables UEFI sont indisponibles.', es: 'No se puede acceder a las variables UEFI.', 'zh-TW': '無法存取 UEFI 變數。'},
  'A reboot request is already running.': {ja: '再起動要求をすでに処理しています。', ko: '이미 다시 시작 요청을 처리 중입니다.', de: 'Eine Neustartanforderung wird bereits verarbeitet.', fr: 'Une demande de redémarrage est déjà en cours.', es: 'Ya hay una solicitud de reinicio en curso.', 'zh-TW': '已在處理重新啟動要求。'},
  'Boot entry changed. Refresh and try again.': {ja: '起動項目が変更されました。更新して再試行してください。', ko: '부팅 항목이 변경되었습니다. 새로 고침한 후 다시 시도하세요.', de: 'Der Starteintrag wurde geändert. Aktualisiere und versuche es erneut.', fr: 'L’entrée de démarrage a changé. Actualisez et réessayez.', es: 'La entrada de arranque cambió. Actualiza y vuelve a intentarlo.', 'zh-TW': '開機項目已變更，請重新整理後再試。'},
  'BootNext verification failed.': {ja: 'BootNext の検証に失敗しました。', ko: 'BootNext 검증에 실패했습니다.', de: 'BootNext konnte nicht verifiziert werden.', fr: 'La vérification de BootNext a échoué.', es: 'Falló la verificación de BootNext.', 'zh-TW': 'BootNext 驗證失敗。'},
  'Request timed out. Refresh the Windows boot status.': {ja: '要求がタイムアウトしました。Windows の起動状態を更新してください。', ko: '요청 시간이 초과되었습니다. Windows 부팅 상태를 새로 고침하세요.', de: 'Zeitlimit überschritten. Aktualisiere den Windows-Startstatus.', fr: 'La demande a expiré. Actualisez l’état du démarrage Windows.', es: 'La solicitud agotó el tiempo de espera. Actualiza el estado de arranque de Windows.', 'zh-TW': '要求逾時，請重新整理 Windows 開機狀態。'},
  'Confirm the restart first.': {ja: '先に再起動を確認してください。', ko: '먼저 다시 시작을 확인하세요.', de: 'Bestätige zuerst den Neustart.', fr: 'Confirmez d’abord le redémarrage.', es: 'Confirma primero el reinicio.', 'zh-TW': '請先確認重新啟動。'},
  'Reboot was not completed.': {ja: '再起動は完了しませんでした。', ko: '다시 시작이 완료되지 않았습니다.', de: 'Der Neustart wurde nicht abgeschlossen.', fr: 'Le redémarrage n’a pas abouti.', es: 'No se completó el reinicio.', 'zh-TW': '未能完成重新啟動。'},
  'Reboot failed; BootNext restoration also failed.': {ja: '再起動に失敗し、BootNext も復元できませんでした。', ko: '다시 시작과 BootNext 복원에 모두 실패했습니다.', de: 'Der Neustart und die Wiederherstellung von BootNext sind fehlgeschlagen.', fr: 'Le redémarrage et la restauration de BootNext ont échoué.', es: 'Fallaron el reinicio y la restauración de BootNext.', 'zh-TW': '重新啟動失敗，BootNext 也無法還原。'},
};
const tr = pluginTranslation(messages);
const t = (zh: string, en: string) => tr(en, zh);

function bootMessage(raw: string) {
  const separator = raw.indexOf(' / ');
  if (separator < 0) return raw;
  const english = raw.slice(0, separator);
  if (!Object.prototype.hasOwnProperty.call(messages, english)) return raw;
  const chinese = raw.slice(separator + 3);
  const detailStart = chinese.indexOf('：');
  return detailStart < 0
    ? tr(english, chinese)
    : tr(english, chinese.slice(0, detailStart)) + ' ' + chinese.slice(detailStart + 1);
}

function unwrap<T>(reply: Reply<T>): T {
  if (!reply.ok || reply.data === undefined) throw new Error(reply.error || t('操作失败', 'Operation failed'));
  return reply.data;
}

function Content() {
  const [info, setInfo] = useState<Status | null>(null);
  const [managerInfo, setManagerInfo] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const lock = useRef(false);
  const confirming = useRef(false);
  const requested = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; }; }, []);
  async function task(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setMessage('');
    try { await action(); }
    catch (error) { if (mounted.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = requested.current; if (mounted.current) setBusy(requested.current); }
  }
  async function refresh() {
    await task(async () => {
      setInfo(null);
      setManagerInfo(null);
      const [windowsReply, managerReply] = await Promise.all([status(), managerStatus()]);
      if (mounted.current) {
        setInfo(windowsReply.ok ? unwrap(windowsReply) : {
          ready: false, target: null, reason: windowsReply.error || t('操作失败', 'Operation failed'),
        });
        setManagerInfo(managerReply.ok ? unwrap(managerReply) : {
          ready: false, target: null, reason: managerReply.error || t('操作失败', 'Operation failed'),
        });
      }
    });
  }
  function confirm(destination: 'windows' | 'manager') {
    const selected = destination === 'windows' ? info : managerInfo;
    if (lock.current || confirming.current || !selected?.ready || !selected.target) return;
    confirming.current = true;
    const target = selected.target;
    showModal(<ConfirmModal
      bDestructiveWarning
      strTitle={destination === 'windows' ? t('重启进入 Windows？', 'Restart into Windows?')
        : t('重启到启动管理器？', 'Restart into boot manager?')}
      strDescription={destination === 'windows'
        ? t('将关闭当前会话。请先保存游戏和文件。仅下一次启动进入 Windows。',
          'Your session will close. Save games and files first. Only the next boot will enter Windows.')
        : t('将关闭当前会话。请先保存游戏和文件。下一次启动进入 SteamOS 启动项对应的启动管理器。',
          'Your session will close. Save games and files first. The next boot will use the boot manager at the SteamOS entry.')}
      strOKButtonText={t('重启', 'Restart')}
      strCancelButtonText={t('取消', 'Cancel')}
      onOK={() => void task(async () => {
        unwrap(await (destination === 'windows' ? reboot(target, true) : rebootManager(target, true)));
        requested.current = true;
        if (mounted.current) setMessage(destination === 'windows'
          ? t('已请求重启，正在进入 Windows…', 'Restart requested. Starting Windows…')
          : t('已请求重启，正在进入启动管理器…', 'Restart requested. Starting boot manager…'));
      })}
    />, undefined, { fnOnClose: () => { confirming.current = false; } });
  }
  return <PanelSection title={t('系统切换', 'Switch system')}>
    <PanelSectionRow><div role="status" aria-live="polite" style={{ overflowWrap: 'anywhere' }}>
      {busy ? t('正在处理…', 'Working…') : info?.ready
        ? t('Windows 启动项已就绪', 'Windows boot entry is ready')
        : info?.reason ? bootMessage(info.reason) : t('请刷新启动状态', 'Refresh the boot status')}
    </div></PanelSectionRow>
    <ButtonItem layout="below" disabled={busy || !info?.ready} onClick={() => confirm('windows')}>
      {t('重启进入 Windows', 'Restart into Windows')}
    </ButtonItem>
    <PanelSectionRow><div role="status" aria-live="polite" style={{ overflowWrap: 'anywhere' }}>
      {busy ? t('正在处理…', 'Working…') : managerInfo?.ready
        ? t('SteamOS 启动管理器入口已就绪', 'SteamOS boot-manager entry is ready')
        : managerInfo?.reason ? bootMessage(managerInfo.reason) : t('请刷新启动状态', 'Refresh the boot status')}
    </div></PanelSectionRow>
    <ButtonItem layout="below" disabled={busy || !managerInfo?.ready} onClick={() => confirm('manager')}>
      {t('重启到启动管理器', 'Restart into boot manager')}
    </ButtonItem>
    <ButtonItem layout="below" disabled={busy} onClick={() => void refresh()}>
      {t('刷新启动状态', 'Refresh boot status')}
    </ButtonItem>
    {message && <PanelSectionRow><div role="status" aria-live="polite" style={{ overflowWrap: 'anywhere' }}>{bootMessage(message)}</div></PanelSectionRow>}
    <PanelSectionRow>{t('仅影响下一次启动，之后按原默认顺序启动。', 'Only affects the next boot. Later boots follow your default order.')}</PanelSectionRow>
  </PanelSection>;
}

export default definePlugin(() => ({
  name: 'Restart into Windows',
  titleView: <div className={staticClasses.Title}>{t('重启进入 Windows', 'Restart into Windows')}</div>,
  content: <Content />,
  icon: <svg width="1em" height="1em" style={{ width: '1em', height: '1em', display: 'block' }}
    viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M20 7v5h-5M20 12a8 8 0 1 0-2 6M20 12l-4-4" />
  </svg>,
  onDismount() {},
}));
