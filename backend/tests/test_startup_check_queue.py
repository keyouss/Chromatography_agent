import fnmatch
import runpy
from pathlib import Path


def test_startup_idle_counts_live_delayed_and_inflight_but_not_dead_letters():
    queue_counts = runpy.run_path(str(Path(__file__).parents[2] / "deploy/v1/startup-check.py"))["queue_counts"]
    values = {
        "dramatiq:accept": (b"list", 2),
        "dramatiq:accept.DQ": (b"list", 1),
        "dramatiq:accept-assistant": (b"list", 1),
        "dramatiq:accept.XQ": (b"zset", 9),
        "dramatiq:accept-assistant.XQ": (b"zset", 9),
        "dramatiq:__acks__.worker.accept": (b"set", 2),
    }

    class ReadOnlyRedis:
        def type(self, key):
            return values.get(key, (b"none", 0))[0]

        def llen(self, key):
            return values[key][1]

        zcard = llen
        scard = llen

        def scan_iter(self, *, match, count):
            return (key for key in values if fnmatch.fnmatchcase(key, match))

    assert queue_counts(ReadOnlyRedis(), "accept") == (4, 2)
    del values["dramatiq:accept"]
    del values["dramatiq:accept.DQ"]
    del values["dramatiq:accept-assistant"]
    del values["dramatiq:__acks__.worker.accept"]
    assert queue_counts(ReadOnlyRedis(), "accept") == (0, 0)
