"""Native SteamOS Manager BatteryChargeLimit1 remote over the system bus."""
import importlib.util
import pwd
import signal
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

spec=importlib.util.spec_from_file_location('battery','/var/lib/oxp3-battery/battery_control.py')
battery=importlib.util.module_from_spec(spec);spec.loader.exec_module(battery)
BUS='org.onexplayer.SteamOSManager.Battery'
PATH='/org/onexplayer/SteamOSManager/Battery'
IFACE='com.steampowered.SteamOSManager1.BatteryChargeLimit1'
PROPS='org.freedesktop.DBus.Properties'
DBusGMainLoop(set_as_default=True)
bus=dbus.SystemBus()
uids={0,pwd.getpwnam('deck').pw_uid}

class Denied(dbus.DBusException): _dbus_error_name='org.freedesktop.DBus.Error.AccessDenied'
class Invalid(dbus.DBusException): _dbus_error_name='org.freedesktop.DBus.Error.InvalidArgs'
class Failed(dbus.DBusException): _dbus_error_name='org.freedesktop.DBus.Error.Failed'
def authorize(sender):
    if sender is None or bus.get_unix_user(sender) not in uids: raise Denied('Only root and deck')

class Peer(dbus.service.Object):
    @dbus.service.method('org.freedesktop.DBus.Peer',sender_keyword='sender')
    def Ping(self,sender=None): authorize(sender)

class Battery(Peer):
    def value(self,interface,name):
        if interface!=IFACE: raise Invalid('Unknown interface')
        if name=='SuggestedMinimumLimit': return dbus.Int32(60,variant_level=1)
        if name!='MaxChargeLevel': raise Invalid('Unknown property')
        try: return dbus.Int32(int(battery.read('charge_control_end_threshold')),variant_level=1)
        except Exception as e: raise Failed(str(e))

    @dbus.service.method(PROPS,in_signature='ss',out_signature='v',sender_keyword='sender')
    def Get(self,interface,name,sender=None):
        authorize(sender);return self.value(interface,name)

    @dbus.service.method(PROPS,in_signature='s',out_signature='a{sv}',sender_keyword='sender')
    def GetAll(self,interface,sender=None):
        authorize(sender);return {n:self.value(interface,n) for n in ('MaxChargeLevel','SuggestedMinimumLimit')}

    @dbus.service.method(PROPS,in_signature='ssv',sender_keyword='sender')
    def Set(self,interface,name,value,sender=None):
        authorize(sender)
        if interface!=IFACE or name!='MaxChargeLevel' or not isinstance(value,dbus.Int32): raise Invalid('Expected writable MaxChargeLevel int32')
        try: battery.set_value('charge_control_end_threshold',int(value))
        except ValueError as e: raise Invalid(str(e))
        except Exception as e: raise Failed(str(e))
        finally: self.PropertiesChanged(IFACE,{},['MaxChargeLevel'])

    @dbus.service.signal(PROPS,signature='sa{sv}as')
    def PropertiesChanged(self,interface,changed,invalidated): pass

    @dbus.service.method('org.freedesktop.DBus.Introspectable',out_signature='s',sender_keyword='sender')
    def Introspect(self,sender=None):
        authorize(sender)
        return f'''<node><interface name="{IFACE}"><property name="MaxChargeLevel" type="i" access="readwrite"><annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="invalidates"/></property><property name="SuggestedMinimumLimit" type="i" access="read"><annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="const"/></property></interface><interface name="org.freedesktop.DBus.Peer"><method name="Ping"/></interface><interface name="{PROPS}"><method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method><method name="GetAll"><arg type="s" direction="in"/><arg type="a{{sv}}" direction="out"/></method><method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/></method><signal name="PropertiesChanged"><arg type="s"/><arg type="a{{sv}}"/><arg type="as"/></signal></interface></node>'''

battery.guard()
name=dbus.service.BusName(BUS,bus=bus,do_not_queue=True)
root=Peer(name,'/');obj=Battery(name,PATH)
last=[None]
def poll():
    try: current=battery.read('charge_control_end_threshold')
    except OSError: current=None
    if current!=last[0]: last[0]=current;obj.PropertiesChanged(IFACE,{},['MaxChargeLevel'])
    return True
loop=GLib.MainLoop();GLib.timeout_add_seconds(2,poll)
for sig in (signal.SIGTERM,signal.SIGINT): GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,sig,lambda:(loop.quit(),False)[1])
loop.run()
