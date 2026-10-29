#!/usr/bin/python3
"""Mirror supported microphone source mute to its ALSA Capture Switch.

Only Pulse source/card/server events trigger queries. No stream mute, volume,
default-source or boost settings are changed. Run as the logged-in audio user.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import threading

LOG = logging.getLogger("oxp-microphone")
DRIVERS = {"snd_soc_skl_hda_dsp", "snd_hda_intel"}
CONTROL = "name='Capture Switch'"
EVENT = re.compile(r"^Event '(?:new|change|remove)' on (?:source|card|server)(?: #\d+)?\s*$")


class SynchronizationError(RuntimeError):
    """A hardware failure requires an audio-server reconnect and retry."""


def child_environment():
    return dict(os.environ, LC_ALL="C")


def command(args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=10,
                          env=child_environment()).stdout


def card_id(index, sysfs=Path("/sys/class/sound")):
    if not str(index).isdigit():
        raise ValueError("invalid ALSA card index")
    result = (sysfs / ("card" + str(index)) / "id").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", result):
        raise ValueError("invalid ALSA card ID")
    return result


def discover(sources, objects, resolve=card_id):
    """Return stable-card-ID -> source states; None means incomplete evidence."""
    if not isinstance(sources, list) or not isinstance(objects, list):
        raise ValueError("audio server returned invalid object lists")
    by_name = {s.get("name"): s for s in sources if isinstance(s, dict)}
    devices = {str(o.get("id")): o.get("info", {}).get("props", {})
               for o in objects if isinstance(o, dict)
               and o.get("type") == "PipeWire:Interface:Device"}
    cards = {}
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        node = obj.get("info", {}).get("props", {})
        name = node.get("node.name", "")
        if (not name.startswith("alsa_input.") or name.endswith(".monitor")
                or node.get("media.class") != "Audio/Source"):
            continue
        props = dict(devices.get(str(node.get("device.id")), {}))
        props.update(node)
        driver = props.get("alsa.driver_name", props.get("api.alsa.card.driver"))
        if driver not in DRIVERS:
            continue
        index = props.get("alsa.card", props.get("api.alsa.card"))
        try:
            stable = resolve(index)
        except (OSError, ValueError):
            LOG.warning("Cannot resolve ALSA card for %s", name)
            continue
        # Valve's visible loopback owns the user-facing mute while it exists.
        source = by_name.get("alsa_loopback_device." + name, by_name.get(name))
        mute = source.get("mute") if source else None
        monitor = source.get("monitor_of_sink") if source else None
        if monitor not in (None, 4294967295, "4294967295"):
            mute = None
        cards.setdefault(stable, []).append(mute if isinstance(mute, bool) else None)
    return cards


def parse_switch(output):
    if not re.search(r"\btype=BOOLEAN\b", output):
        raise ValueError("Capture Switch is not BOOLEAN")
    match = re.search(r"^\s*: values=([a-zA-Z0-9,]+)\s*$", output, re.MULTILINE)
    if not match:
        raise ValueError("Capture Switch values missing")
    values = match.group(1).split(",")
    if not values or any(v not in ("on", "off", "1", "0") for v in values):
        raise ValueError("invalid Capture Switch values")
    return tuple(v in ("on", "1") for v in values)


class Bridge:
    def __init__(self, execute=command, resolve=card_id):
        self.execute = execute
        self.resolve = resolve
        self.original = {}
        self.owned = {}

    def read(self, stable):
        return parse_switch(self.execute(["amixer", "-D", "hw:" + stable, "cget", CONTROL]))

    def write(self, stable, values):
        self.execute(["amixer", "-D", "hw:" + stable, "cset", CONTROL,
                      ",".join("on" if v else "off" for v in values)])
        if self.read(stable) != values:
            raise ValueError("Capture Switch readback mismatch")

    def snapshot(self):
        sources = json.loads(self.execute(["pactl", "-f", "json", "list", "sources"]))
        objects = json.loads(self.execute(["pw-dump"]))
        return discover(sources, objects, self.resolve)

    def sync(self):
        cards = self.snapshot()
        failed = []
        for stable, states in cards.items():
            # Incomplete discovery cannot justify either opening or closing a mic.
            if not states or any(state is None for state in states):
                continue
            try:
                current = self.read(stable)
                self.original.setdefault(stable, current)
                desired = (not all(states),) * len(current)
                if current != desired:
                    # A successful cset can be followed by a failed cget. Keep
                    # conditional ownership so a later stop can still restore.
                    self.owned[stable] = desired
                    self.write(stable, desired)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                LOG.warning("Cannot synchronize %s: %s", stable, exc)
                failed.append(stable)
        if failed:
            raise SynchronizationError("Capture Switch synchronization failed: " + ", ".join(failed))
        return cards

    def restore(self):
        for stable, last in self.owned.items():
            try:
                if self.read(stable) == last:
                    self.write(stable, self.original[stable])
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                LOG.warning("Cannot restore %s: %s", stable, exc)


def relevant_event(line):
    return EVENT.fullmatch(line.strip()) is not None


def run(bridge):
    stopped = threading.Event()
    def stop(_signum, _frame):
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    backoff = 1
    try:
        while not stopped.is_set():
            process = None
            try:
                # Subscribe first so initial snapshot cannot miss a later change.
                process = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, env=child_environment())
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    bridge.sync()
                    pending = b""
                    while not stopped.is_set():
                        ready = selector.select(timeout=0.5)
                        if not ready:
                            if process.poll() is not None:
                                raise ConnectionError("Pulse subscription ended")
                            continue
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            raise ConnectionError("Pulse subscription disconnected")
                        pending += data
                        lines = pending.split(b"\n")
                        pending = lines.pop()
                        if any(relevant_event(line.decode("utf-8", "replace")) for line in lines):
                            bridge.sync()
                            backoff = 1
            except (OSError, ValueError, ConnectionError, SynchronizationError,
                    subprocess.SubprocessError) as exc:
                LOG.warning("Audio server unavailable: %s", exc)
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    if process.stdout:
                        process.stdout.close()
            if not stopped.is_set():
                stopped.wait(backoff)
                backoff = min(backoff * 2, 30)
    finally:
        bridge.restore()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "run"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(levelname)s: %(message)s")
    bridge = Bridge()
    if args.action == "status":
        cards = bridge.snapshot()
        result = {}
        for stable, states in cards.items():
            try:
                values = bridge.read(stable)
                result[stable] = {"source_mutes": states, "capture_switch": values}
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result[stable] = {"source_mutes": states, "error": str(exc)}
        print(json.dumps(result, indent=2))
    else:
        if os.geteuid() == 0:
            parser.error("run must use the logged-in audio user, not root")
        run(bridge)


if __name__ == "__main__":
    main()
