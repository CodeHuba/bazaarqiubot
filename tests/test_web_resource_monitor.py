import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_web_resources', ROOT/'tools/check_web_resources.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def healthy(**extra):
    return dict(available_mb=1600, swap_used_percent=60, memory_full_avg10=0,
                load1=.2, services=['active','active'], website_status=200) | extra


def test_low_memory_and_io_stall_alert_and_recovery():
    first = probe.record(healthy(available_mb=40, swap_used_percent=99.9,
                                memory_full_avg10=90, load1=88), [], {})
    assert first['severity'] == 'critical'
    assert {'memory_exhaustion','memory_stall','swap_headroom_low','high_load'} <= set(first['alerts'])
    again = probe.record(first['metrics'], [], first)
    assert again['consecutive_unhealthy'] == 2 and not again['changed']
    recovered = probe.record(healthy(), [], again)
    assert recovered['severity'] == 'ok'
    assert recovered['changed'] and recovered['consecutive_unhealthy'] == 0


def test_full_swap_alone_does_not_claim_current_memory_exhaustion():
    assert probe.evaluate(healthy(swap_used_percent=100)) == []


def test_probe_failure_and_service_and_https_failure_are_visible():
    result = probe.record({}, ['system:FileNotFoundError'], {})
    assert result['severity'] == 'critical'
    assert {'memory_probe_failed','service_unavailable','website_unavailable','probe_incomplete'} <= set(result['alerts'])


def test_meminfo_units_and_swap_calculation(tmp_path):
    path = tmp_path/'meminfo'
    path.write_text('MemAvailable: 524288 kB\nSwapTotal: 1048576 kB\nSwapFree: 262144 kB\n')
    assert probe.read_memory(path) == {'available_mb':512, 'swap_used_percent':75}
