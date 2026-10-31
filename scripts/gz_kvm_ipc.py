"""Check real DWARF member offsets after consuming the GKI IPC padding."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parents[1] / "patches/gz-kvm"


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def members(text, top_level=False):
    result = {}
    for line in text.splitlines():
        if top_level and not re.match(r'^\t[^\t]', line):
            continue
        match = re.search(r'\b(\w+)(?:\[[^\]]+\])*;\s*/\*\s*(\d+)\s+(\d+)\s*\*/', line)
        if match:
            name, offset, size = match.groups()
            value = {'offset': int(offset), 'bytes': int(size)}
            # pahole also prints anonymous union member offsets relative to zero.
            if name not in result or value['offset'] > result[name]['offset']:
                result[name] = value
    return result


def compare(structure, before_text, after_text):
    before, after = members(before_text, True), members(after_text, True)
    consumed = {'task_struct': {f'android_kabi_reserved{n}' for n in (6, 7, 8)},
                'user_struct': {'android_kabi_reserved1'}}[structure]
    required = set(before) - consumed
    differences = {name: {'before': before[name], 'after': after.get(name)}
                   for name in sorted(required) if before[name] != after.get(name)}
    size_before = int(re.search(r'/\* size: (\d+)', before_text)[1])
    size_after = int(re.search(r'/\* size: (\d+)', after_text)[1])
    all_before, all_after = members(before_text), members(after_text)
    padding = ({'sysvsem': ('android_kabi_reserved6', 8),
                'sysvshm': ('android_kabi_reserved7', 16)} if structure == 'task_struct'
               else {'mq_bytes': ('android_kabi_reserved1', 8)})
    placements = {}
    for name, (slot, size) in padding.items():
        expected = {'offset': all_before[slot]['offset'], 'bytes': size}
        placements[name] = {'expected': expected, 'actual': all_after.get(name)}
    passed = (not differences and size_before == size_after and
              all(row['expected'] == row['actual'] for row in placements.values()))
    assert len(required) >= (150 if structure == 'task_struct' else 10), 'Incomplete pahole output'
    return {'passed': passed, 'baseline_bytes': size_before, 'integrated_bytes': size_after,
            'compared_top_level_members': len(required), 'differences': differences,
            'ipc_fields_in_reserved_padding': placements}


def main(image, report):
    archive = HERE / 'abi'
    recorded = json.loads((archive / 'manifest.json').read_text())['sha256']
    result = {'schema': 'gz-kvm-ipc-dwarf-layout-v1', 'vmlinux_sha256': digest(image),
              'structures': {}, 'device_tested': False,
              'scope': 'DWARF sizes, ordinary top-level member offsets, and IPC reserved-slot placement; '
                       'the separate full export CRC audit and hardware vendor tests remain required.'}
    for structure in ('task_struct', 'user_struct'):
        baseline = archive / f'baseline-{structure}.txt'
        assert digest(baseline) == recorded[baseline.name], 'Baseline layout archive changed'
        actual = subprocess.check_output(['pahole', '-C', structure, str(image)], text=True)
        (report.parent / f'integrated-{structure}.txt').write_text(actual)
        result['structures'][structure] = compare(structure, baseline.read_text(), actual)
    result['passed'] = all(row['passed'] for row in result['structures'].values())
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    assert result['passed'], 'IPC layout differs from the original GKI; deployment is prohibited'
