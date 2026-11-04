"""JSON-only CLI boundary for WPF and guest automation. Errors exit with code 2."""
import argparse
import json
import sys
from . import (load_json, validate_manifest, validate_package, verify_signed_index,
               resolve_plan, safe_extract_archive, DeveloperTrustSession)


def main(argv=None):
    parser=argparse.ArgumentParser(prog="python -m protocol")
    commands=parser.add_subparsers(dest="command",required=True)
    command=commands.add_parser("validate"); command.add_argument("manifest")
    command=commands.add_parser("package"); command.add_argument("directory")
    command.add_argument("--manifest-sha256"); command.add_argument("--developer",action="store_true")
    command=commands.add_parser("index"); command.add_argument("manifest"); command.add_argument("signature")
    command.add_argument("--public-key"); command.add_argument("--openssl",default="openssl")
    command=commands.add_parser("cached-index"); command.add_argument("cache")
    command=commands.add_parser("resource-catalog")
    command=commands.add_parser("resource-selection"); command.add_argument("model"); command.add_argument("catalog_sha256"); command.add_argument("components",nargs="+"); command.add_argument("--trust-local-test",action="store_true")
    command=commands.add_parser("import-resource"); command.add_argument("source"); command.add_argument("destination"); command.add_argument("--trust-local-test",action="store_true")
    command=commands.add_parser("model-catalog"); command.add_argument("--offline",action="store_true")
    command=commands.add_parser("bundled-model-package"); command.add_argument("identity")
    command.add_argument("--trust-local-test",action="store_true")
    command=commands.add_parser("plan"); command.add_argument("request",help="JSON {components,selected,environment,mode}")
    command=commands.add_parser("extract"); command.add_argument("archive"); command.add_argument("destination")
    command=commands.add_parser("fetch-index"); command.add_argument("url"); command.add_argument("signature_url"); command.add_argument("cache")
    command.add_argument("--public-key"); command.add_argument("--openssl",default="openssl")
    command=commands.add_parser("load-index"); command.add_argument("url"); command.add_argument("signature_url"); command.add_argument("cache")
    command.add_argument("--public-key"); command.add_argument("--openssl",default="openssl")
    command=commands.add_parser("stage-brand"); command.add_argument("source"); command.add_argument("destination")
    command.add_argument("--developer",action="store_true")
    command=commands.add_parser("import-brand"); command.add_argument("source"); command.add_argument("destination")
    command.add_argument("identity"); command.add_argument("manifest_sha256"); command.add_argument("--developer",action="store_true")
    command=commands.add_parser("import-device"); command.add_argument("source"); command.add_argument("destination")
    command.add_argument("--developer",action="store_true")
    command=commands.add_parser("fetch-package"); command.add_argument("cache"); command.add_argument("identity"); command.add_argument("version"); command.add_argument("destination")
    command.add_argument("--public-key"); command.add_argument("--openssl",default="openssl")
    command=commands.add_parser("fetch-github"); command.add_argument("url"); command.add_argument("destination")
    command.add_argument("--ref",default="HEAD"); command.add_argument("--package-path"); command.add_argument("--developer",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="resource-catalog":
            from .resource_selection import resource_catalog
            result=resource_catalog()
        elif args.command=="resource-selection":
            from .resource_selection import select_resources
            result=select_resources(args.model,args.components,args.catalog_sha256,trust_local_test=args.trust_local_test)
        elif args.command=="model-catalog":
            from .model_catalog import load_models
            result=load_models(online=not args.offline)
        elif args.command=="bundled-model-package":
            from .model_catalog import bundled_model_package
            result=bundled_model_package(args.identity,trust_local_test=args.trust_local_test)
        elif args.command=="import-resource":
            from .resource_import import import_resource_pool
            result=import_resource_pool(args.source,args.destination,trust_local_test=args.trust_local_test)
        elif args.command=="stage-brand":
            from .brand_bundle import stage_brand
            result=stage_brand(args.source,args.destination,developer=args.developer)
        elif args.command=="import-brand":
            from .brand_bundle import import_brand
            result=import_brand(args.source,args.destination,args.identity,args.manifest_sha256,developer=args.developer)
        elif args.command=="import-device":
            from .local import import_local
            result=import_local(args.source,args.destination,developer=args.developer)
        elif args.command=="load-index":
            from .fetch import load_index
            result=load_index(args.url,args.signature_url,args.cache,public_key=args.public_key,openssl=args.openssl)
        elif args.command=="cached-index":
            from .fetch import cached_index
            result=cached_index(args.cache)
        elif args.command=="validate": result=validate_manifest(load_json(args.manifest))
        elif args.command=="package":
            if args.developer:
                session=DeveloperTrustSession(); session.grant_local(args.directory)
                result=session.validate_local(args.directory)
            else: result=validate_package(args.directory,expected_manifest_sha256=args.manifest_sha256)
        elif args.command=="index": result=verify_signed_index(args.manifest,args.signature,args.public_key,openssl=args.openssl)
        elif args.command=="plan":
            request=load_json(args.request)
            result=resolve_plan(request["components"],request["selected"],request["environment"],mode=request.get("mode","normal"))
        elif args.command=="extract": result={"directory":str(safe_extract_archive(args.archive,args.destination))}
        else:
            from .fetch import fetch_index,fetch_package,fetch_github
            if args.command=="fetch-index": result=fetch_index(args.url,args.signature_url,args.cache,public_key=args.public_key,openssl=args.openssl)
            elif args.command=="fetch-package": result=fetch_package(args.cache,args.identity,args.version,args.destination,public_key=args.public_key,openssl=args.openssl)
            else: result=fetch_github(args.url,args.destination,ref=args.ref,package_path=args.package_path,developer=args.developer)
        print(json.dumps({"ok":True,"result":result},ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False)); return 2


if __name__=="__main__": sys.exit(main())
