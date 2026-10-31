"""Render only owned telemetry blocks in the fixed MangoHud preset file."""
import re
BEGIN=b'# BEGIN BOILER OVERLAY TELEMETRY\n'
END=b'# END BOILER OVERLAY TELEMETRY\n'
WRAPPER=b'/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py overlay-show '
BLOCK=(BEGIN+b'cpu_power=0\ngpu_power=0\nproc_vram\n'+b'custom_text=CPU / GPU\nexec='+WRAPPER+b'domains\n'+b'custom_text=APU / PL1\nexec='+WRAPPER+b'limits\n'+b'custom_text=Fan\nexec='+WRAPPER+b'fan\n'+END)
SECTION=re.compile(br'(?m)^[ \t]*\[([^\r\n]*)\][ \t]*(?:\r?\n|$)')
def render(original):
    if not isinstance(original,bytes):raise TypeError('preset content must be bytes')
    if BEGIN.rstrip(b'\n') in original or END.rstrip(b'\n') in original:raise ValueError('owned block already present')
    sections=list(SECTION.finditer(original)); targets={}
    for match in sections:
        label=match.group(1)
        number=int(label[-1:]) if label in (b'preset 2',b'preset 3',b'preset 4') else None
        if number is not None:
            if number in targets:raise ValueError('duplicate target preset')
            targets[number]=match
    newline=b'\r\n' if b'\r\n' in original else b'\n'
    block=BLOCK.replace(b'\n',newline); result=original
    for number,match in sorted(targets.items(),key=lambda item:item[1].start(),reverse=True):
        end=next((m.start() for m in sections if m.start()>match.start()),len(original))
        prefix=b'' if end==0 or result[end-1:end]==b'\n' else newline
        result=result[:end]+prefix+block+result[end:]
    for number in (2,3,4):
        if number not in targets:
            if result and not result.endswith(b'\n'):result+=newline
            result+=b'[preset '+str(number).encode()+b']'+newline+b'inherit'+newline+block
    return result
