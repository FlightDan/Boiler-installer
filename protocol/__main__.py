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
    command=commands.add_parser("plan"); command.add_argument("request",help="JSON {components,selected,environment,mode}")
    command=commands.add_parser("extract"); command.add_argument("archive"); command.add_argument("destination")
    command=commands.add_parser("fetch-index"); command.add_argument("url"); command.add_argument("signature_url"); command.add_argument("cache")
    command.add_argument("--public-key"); command.add_argument("--openssl",default="openssl")
    command=commands.add_parser("fetch-package"); command.add_argument("cache"); command.add_argument("identity"); command.add_argument("version"); command.add_argument("destination")
    command.add_argument("--public-key"); command.add_argument("--openssl",default="openssl")
    command=commands.add_parser("fetch-github"); command.add_argument("url"); command.add_argument("destination")
    command.add_argument("--ref",default="HEAD"); command.add_argument("--package-path"); command.add_argument("--developer",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="cached-index":
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
