"""Explicit target maintenance, with durable SteamOS live-root protection."""
import argparse
import json
import sys
from protocol import load_json
from . import Engine


def main(argv=None):
    parser=argparse.ArgumentParser(prog="python -m maintenance")
    parser.add_argument("--root",required=True); parser.add_argument("--state-dir")
    commands=parser.add_subparsers(dest="command",required=True)
    command=commands.add_parser("apply"); command.add_argument("manifest"); command.add_argument("payload")
    # Script hooks deliberately have no casual CLI flag: privileged integration
    # must establish package trust, selection and target compatibility first.
    command=commands.add_parser("verify"); command.add_argument("component")
    command=commands.add_parser("restore"); command.add_argument("transaction")
    commands.add_parser("recover")
    command=commands.add_parser("inventory",help="Read installed ownership and actual target compatibility")
    command.add_argument("--device-id")
    command=commands.add_parser("discover",help="Discover updates in a verified signed registry cache")
    command.add_argument("index_cache"); command.add_argument("--identity"); command.add_argument("--device-id")
    command=commands.add_parser("plan-update",help="Review an authenticated update without modifying the target")
    command.add_argument("index_cache"); command.add_argument("package_dir"); command.add_argument("identity"); command.add_argument("version")
    command.add_argument("--component",action="append"); command.add_argument("--device-id")
    command=commands.add_parser("execute-update",help="Execute an unchanged, explicitly reviewed update plan")
    command.add_argument("plan"); command.add_argument("index_cache"); command.add_argument("package_dir"); command.add_argument("--reviewed-sha256",required=True)
    command=commands.add_parser("inspect-legacy"); command.add_argument("state"); command.add_argument("public_key")
    command=commands.add_parser("adopt"); command.add_argument("receipt")
    model=commands.add_parser('models',help='Pinned model guides and explicitly reviewed on-device adaptations')
    actions=model.add_subparsers(dest='model_command',required=True)
    command=actions.add_parser('list'); command.add_argument('--offline',action='store_true')
    command=actions.add_parser('options'); command.add_argument('model_id'); command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('plan'); command.add_argument('model_id')
    command.add_argument('--action',choices=('apply','verify','restore'),default='apply')
    command.add_argument('--repair',action='append',default=[]); command.add_argument('--plugin',action='append',default=[])
    command.add_argument('--trust-local-test',action='store_true'); command.add_argument('--acknowledge-update-hold',action='store_true')
    command.add_argument('--installer-language',choices=('zh-CN','en-US'),default='en-US')
    command.add_argument('--kde-language',default='system',help='system or an authenticated package-declared KDE locale')
    command=actions.add_parser('execute'); command.add_argument('plan'); command.add_argument('--reviewed-sha256',required=True)
    command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('shared-options'); command.add_argument('model_id'); command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('shared-plan'); command.add_argument('model_id')
    command.add_argument('--action',choices=('apply','verify','restore'),default='apply')
    command.add_argument('--repair',action='append',default=[]); command.add_argument('--plugin',action='append',default=[])
    command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('shared-execute'); command.add_argument('plan'); command.add_argument('--reviewed-sha256',required=True)
    command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('keyboard-options'); command.add_argument('model_id'); command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('keyboard-plan'); command.add_argument('model_id')
    command.add_argument('--action',choices=('apply','verify','restore','recover'),default='apply')
    command.add_argument('--repair',action='append',default=[]); command.add_argument('--plugin',action='append',default=[])
    command.add_argument('--trust-local-test',action='store_true')
    command=actions.add_parser('keyboard-execute'); command.add_argument('plan'); command.add_argument('--reviewed-sha256',required=True)
    command.add_argument('--trust-local-test',action='store_true')
    command=commands.add_parser("plugins",help="Maintain known Boiler Decky/plugin transactions without a payload cache")
    plugins=command.add_subparsers(dest="plugin_command",required=True)
    plugins.add_parser("list")
    for action in ("verify","restore"):
        plugin=plugins.add_parser(action); plugin.add_argument("plugin",choices=("decky","steamgriddb","protondb"))
    from . import resource_cli
    resource_cli.add_parser(commands)
    args=parser.parse_args(argv)
    try:
        if args.command=='resources':
            result=resource_cli.dispatch(args)
            print(json.dumps({'ok':True,'result':result})); return 0
        from .live_root import check_state_directory,pending
        check_state_directory(args.root,args.state_dir)
        if args.command=='models':
            from . import models
            if args.model_command.startswith('keyboard-'):
                from . import keyboard_models
                if args.model_command=='keyboard-options': result=keyboard_models.options(args.root,args.model_id,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
                elif args.model_command=='keyboard-plan': result=keyboard_models.plan(args.root,args.model_id,args.repair,args.plugin,action=args.action,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
                else: result=keyboard_models.execute(args.root,args.plan,reviewed_sha256=args.reviewed_sha256,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
            elif args.model_command.startswith('shared-'):
                from . import shared_models
                if args.model_command=='shared-options': result=shared_models.options(args.root,args.model_id,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
                elif args.model_command=='shared-plan': result=shared_models.plan(args.root,args.model_id,args.repair,args.plugin,action=args.action,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
                else: result=shared_models.execute(args.root,args.plan,reviewed_sha256=args.reviewed_sha256,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
            elif args.model_command=='list': result=models.catalog(online=not args.offline)
            elif args.model_command=='options': result=models.options(args.root,args.model_id,trust_local_test=args.trust_local_test,state_dir=args.state_dir)
            elif args.model_command=='plan':
                result=models.plan_model(args.root,args.model_id,args.repair,args.plugin,action=args.action,
                    trust_local_test=args.trust_local_test,acknowledge_update_hold=args.acknowledge_update_hold,
                    installer_language=args.installer_language,kde_language=args.kde_language,state_dir=args.state_dir)
            else: result=models.execute_model(args.root,args.plan,reviewed_sha256=args.reviewed_sha256,
                    trust_local_test=args.trust_local_test,state_dir=args.state_dir)
            print(json.dumps({'ok':True,'result':result})); return 0
        if args.command in ("inventory","discover","plan-update","execute-update"):
            from . import updates
            if args.command=="inventory": result=updates.inventory(args.root,args.state_dir,device_id=args.device_id)
            elif args.command=="discover": result=updates.discover(args.root,args.index_cache,args.state_dir,identity=args.identity,device_id=args.device_id)
            elif args.command=="plan-update": result=updates.plan_update(args.root,args.index_cache,args.package_dir,args.identity,args.version,args.component,args.state_dir,device_id=args.device_id)
            else: result=updates.execute_plan(args.root,args.plan,args.index_cache,args.package_dir,reviewed_sha256=args.reviewed_sha256,state_dir=args.state_dir)
            print(json.dumps({"ok":True,"result":result})); return 0
        if args.command=="plugins":
            from .plugins import PluginMaintenance
            adapter=PluginMaintenance(args.root)
            if args.plugin_command=='restore':
                engine=Engine(args.root,args.state_dir)
                with engine._lock():
                    engine._recover()
                    result=adapter.restore(args.plugin)
            else:
                result=adapter.list() if args.plugin_command=='list' else adapter.verify(args.plugin)
            print(json.dumps({"ok":True,"result":result})); return 0
        if args.command=='verify':
            if pending(args.root,args.state_dir):
                raise RuntimeError('Interrupted root protection window requires explicit recover')
            result=Engine(args.root,args.state_dir,read_only=True).verify_readonly(args.component)
            print(json.dumps({'ok':True,'result':result})); return 0
        engine=Engine(args.root,args.state_dir)
        if args.command=="apply": result=engine.apply(load_json(args.manifest),args.payload)
        elif args.command=="restore": result=engine.restore(args.transaction)
        elif args.command=="inspect-legacy": result=engine.inspect_legacy(args.state,args.public_key)
        elif args.command=="adopt": result=engine.adopt(args.receipt)
        else: result={"recovered":engine.recover()}
        print(json.dumps({"ok":True,"result":result})); return 0
    except Exception as exc:
        print(json.dumps({"ok":False,"error":str(exc)})); return 2


if __name__=="__main__": sys.exit(main())
