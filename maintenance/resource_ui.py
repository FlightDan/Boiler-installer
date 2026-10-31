"""KDE front end for installed shared-resource verification and recovery."""
import json,re,subprocess
from .model_ui import BackendError,MAINTENANCE,PKEXEC

LABELS={
 'common-slot-switch':('系统槽切换工具','System slot switcher'),
 'common-partition-mount':('分区挂载工具','Partition mounting'),
 'common-windows-reboot':('重启到 Windows','Restart to Windows'),
 'onexplayer-light':('灯光控制','Lighting'),
 'common-serial-console-policy':('串口启动修复','Serial console policy'),
 'onexplayer-battery-common':('电池与休眠支持','Battery and suspend support'),
 'onexplayer-battery-charge-backend':('充电控制','Charge controls'),
 'common-charge-compatibility-guard':('充电兼容检查','Charging compatibility'),
 'onexplayer-microphone':('麦克风静音同步','Microphone mute synchronization'),
 'oxp3-speaker-route':('扬声器修复','Speaker routing'),
 'intel-xevmafix-724':('Intel 显示修复','Intel display fix'),
 'intel-be201-firmware':('Intel 无线与蓝牙固件','Intel wireless and Bluetooth'),
 'intel-g3e-public-cpu-controls':('CPU 控制','CPU controls'),
 'gamma22-hdr-game':('游戏模式 HDR','Game HDR'),
 'onexplayer-input':('手柄与多功能键','Controller and multifunction keys'),
 'bosch-bmi260-gyro':('陀螺仪','Gyroscope'),
 'onexplayer-fan':('风扇控制','Fan controls'),
 'onexplayer-ec-fan-driver':('EC 风扇驱动','EC fan driver'),
 'common-kde-keyboard':('桌面触摸键盘','Desktop touch keyboard'),
 'common-flathub-mirror':('Flathub 镜像','Flathub mirror'),
 'common-telemetry-overlay':('性能监测叠加层','Performance overlay'),
 'gamma22-hdr-desktop':('桌面模式 HDR','Desktop HDR'),
 'common-opti2xmx':('Opti2XMX 游戏增强','Opti2XMX game enhancements'),
 'common-waydroid':('Waydroid 安卓环境','Waydroid Android environment'),
}
class ResourceBackend:
 def __init__(self,runner=subprocess.run):self.runner=runner
 def call(self,action,identity=None,reviewed_sha256=None):
  if action not in ('status','verify','retry','plan-restore','restore'):raise ValueError('Unknown maintenance action')
  if identity is not None and (not isinstance(identity,str) or not re.fullmatch(r'resource:[a-z][a-z0-9-]*',identity)):raise ValueError('Invalid installed resource')
  if (identity is not None)!=(action in ('plan-restore','restore')):raise ValueError('Invalid resource action arguments')
  args=[PKEXEC,MAINTENANCE,'--root','/','resource-lifecycle',action]
  if identity is not None:args.append(identity)
  if action=='restore':
   if not isinstance(reviewed_sha256,str) or not re.fullmatch('[0-9a-f]{64}',reviewed_sha256):raise ValueError('A reviewed restore plan is required')
   args+=['--reviewed-sha256',reviewed_sha256]
  elif reviewed_sha256 is not None:raise ValueError('Unexpected restore digest')
  response=self.runner(args,capture_output=True,text=True,check=False)
  try:value=json.loads(response.stdout)
  except (ValueError,TypeError):raise BackendError(response.stderr.strip() or 'Maintenance returned invalid output')
  if response.returncode or not isinstance(value,dict) or value.get('ok') is not True:raise BackendError(str(value.get('error') if isinstance(value,dict) else 'Maintenance failed'))
  if not isinstance(value.get('result'),dict):raise BackendError('Maintenance returned invalid state')
  return value['result']

class ResourceUI:
 def __init__(self,dialog,backend=None,*,feedback=None,failure=None):
  self.dialog=dialog;self.backend=backend or ResourceBackend();self.feedback=feedback;self.failure=failure
 def offer_failure(self,code):
  if self.failure:
   try:self.failure(code)
   except Exception:pass # Reporting must preserve the original operation result.
 def tr(self,zh,en):return self.dialog.text(zh,en)
 def label(self,identity):
  pair=LABELS.get(identity.removeprefix('resource:'))
  return self.tr(*pair) if pair else identity
 def describe(self,row):
  if row.get('resume_original_pending') is True:return self.tr('文件已恢复，仍需完成恢复操作','Files restored; recovery still needs to finish')
  if row.get('status') in ('restored','activation_restored'):return self.tr('已恢复','Restored')
  if row.get('status') in ('failed','recovery_required'):return self.tr('需要处理：','Needs attention: ')+str(row.get('error',''))
  if row.get('runtime')=='pending-user-session':return self.tr('已配置，等待桌面会话启用','Configured; starts in the desktop session')
  if row.get('runtime')=='initialized-awaiting-user-launch':return self.tr('安卓镜像已初始化，等待用户启动','Android images initialized; launch Waydroid to continue')
  return self.tr('已启用','Active') if row.get('status')=='active' else self.tr('已配置','Configured') if row.get('status')=='configured' else self.tr('等待处理','Pending')
 def restore(self,identity):
  review=self.backend.call('plan-restore',identity)
  if not isinstance(review.get('plan'),dict) or not isinstance(review.get('sha256'),str):raise BackendError('Restore review is unavailable')
  summary=review['plan'].get('summary') or self.tr('恢复此项目管理的文件和运行状态，并保留不属于它的用户更改。','Restore this item’s managed files and runtime state while preserving unrelated user changes.')
  if self.dialog.confirm(self.label(identity)+'\n'+str(summary)):
   result=self.backend.call('restore',identity,reviewed_sha256=review['sha256']);self.dialog.message(self.tr('恢复完成。','Restore finished.')+ ('\n'+self.tr('需要重启后完成驱动卸载。','Restart to finish unloading the driver.') if result.get('reboot_required') else ''))
 def pending_restore(self,state):
  pending=state.get('maintenance_restore',{})
  identity=pending.get('identity') if pending.get('status') not in (None,'restored') else None
  if identity is None:
   identity=next((i for i,r in state['resources'].items() if r.get('status')=='restore_failed' or r.get('resume_original_pending') is True),None)
  if identity is not None and identity not in state['resources']:raise BackendError('Interrupted restore resource is unavailable')
  return identity
 def run(self):
  offered_existing=False
  while True:
   try:
    state=self.backend.call('status');resources=state.get('resources')
    if not isinstance(resources,dict):raise BackendError('Installed resource records are unavailable')
    if not offered_existing and any(r.get('status') in ('failed','recovery_required') for r in resources.values()):
     offered_existing=True;self.offer_failure('MAINTENANCE_OPERATION_FAILED')
    detail='\n'.join(self.label(identity)+'：'+self.describe(row) for identity,row in resources.items())
    choice=self.dialog.menu(self.tr('已安装的机型适配\n','Installed device support\n')+detail,[
     ('verify',self.tr('检查已安装适配','Verify installed support')),('retry',self.tr('重试未完成项目','Retry incomplete items')),
     ('restore',self.tr('恢复所选适配','Restore selected support')),('feedback',self.tr('反馈','Feedback'))])
    if choice is None:return 0
    if choice=='feedback':
     if self.feedback:self.feedback()
     continue
    if choice in ('verify','retry'):
     if choice=='retry':
      interrupted=self.pending_restore(state)
      if interrupted is not None:
       self.restore(interrupted)
       continue
     result=self.backend.call(choice)
     pending=[i for i,r in result.get('resources',{}).items() if r.get('ok') is False or r.get('status') in ('failed','recovery_required')]
     message=self.tr('仍有项目需要处理：','Some items still need attention: ')+', '.join(self.label(i) for i in pending) if pending else self.tr('检查完成。','Checks finished.') if choice=='verify' else self.tr('已重新处理未完成项目。','Incomplete items were retried.')
     self.dialog.message(message,error=bool(pending))
     if pending:self.offer_failure('MODEL_VERIFICATION_FAILED' if choice=='verify' else 'MAINTENANCE_OPERATION_FAILED')
     continue
    identity=self.dialog.menu(self.tr('选择要恢复的适配','Choose support to restore'),[(i,self.label(i)) for i in resources])
    if identity is None:continue
    self.restore(identity)
   except (BackendError,OSError,ValueError,KeyError,TypeError) as error:
    self.dialog.message(self.tr('操作未完成：\n','The operation did not complete:\n')+str(error),error=True)
    if self.failure:self.offer_failure('MAINTENANCE_OPERATION_FAILED')
    elif self.feedback and self.dialog.confirm(self.tr('是否打开反馈？','Open feedback?')):self.feedback()
    return 1
