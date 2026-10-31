"""Check config preservation and failure gates using an exact prepared worktree."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import gz_kvm as gz

FIXTURE=None
class GZChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='gz-optional-check-')
        self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name);self.common=self.work/'common';self.report=self.work/'report'
        shutil.copytree(FIXTURE/'common',self.common,ignore=shutil.ignore_patterns('.git'))
        self.report.mkdir()
        self.config=self.common/'arch/arm64/configs/gki_defconfig'

    def test_user_features_and_cmdline_preserved(self):
        with self.config.open('a') as f:
            f.write('\nCONFIG_KSU=y\nCONFIG_KSU_SUSFS=y\nCONFIG_BBG=y\nCONFIG_ZRAM=y\n')
            f.write('CONFIG_CMDLINE="console=ttyS0 custom=123 kvm-arm.mode=protected"\n')
        gz.configure(self.common,self.report)
        r=gz.configurations(self.config)
        for key in ('CONFIG_KSU','CONFIG_KSU_SUSFS','CONFIG_BBG','CONFIG_ZRAM'):self.assertEqual(r[key],'y')
        tokens=json.loads(r['CONFIG_CMDLINE']).split()
        self.assertEqual(tokens,['kvm-arm.gz=shared','kvm-arm.mode=nvhe','console=ttyS0','custom=123'])
        self.assertEqual(r['CONFIG_LTO_CLANG_FULL'],'y')
        self.assertEqual(r['CONFIG_LTO_CLANG_THIN'],'n')

    def test_config_and_padding_idempotent(self):
        gz.configure(self.common,self.report)
        before={p: p.read_bytes() for p in (self.config,self.common/'include/linux/sched.h',self.common/'include/linux/sched/user.h')}
        gz.configure(self.common,self.report)
        self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))
        record=json.loads((self.report/'config-request.json').read_text())
        self.assertEqual(set(record['ipc_padding'].values()),{'already_applied'})

    def test_wrong_source_rejected_before_config_changes(self):
        before=self.config.read_bytes()
        p=self.common/'arch/arm64/kvm/sys_regs.c';p.write_text(p.read_text()+'\n/* wrong */\n')
        with self.assertRaisesRegex(AssertionError,'Unexpected GZ source'):gz.configure(self.common,self.report)
        self.assertEqual(before,self.config.read_bytes())

    def test_wrong_patch_contract_rejected(self):
        directory=self.work/'patches';shutil.copytree(gz.PATCHES,directory)
        p=directory/gz.manifest()['patch'];p.write_bytes(p.read_bytes()+b'\n')
        with patch.object(gz,'PATCHES',directory):
            with self.assertRaisesRegex(AssertionError,'GZ patch hash mismatch'):gz.manifest()

    def verify_fixture(self):
        output=self.work/'out/android12-5.10/common';output.mkdir(parents=True)
        gz.configure(self.common,self.report)
        shutil.copyfile(self.config,output/'.config')
        (output/'System.map').write_text('\n'.join('0000 T '+s for s in (
            '__kvm_nvhe___gz_kvm_entry','__kvm_nvhe_gz_shared_stage1','gz_shared_stage1',
            'kvm_gz_bind','kvm_gz_cpu_init','kvm_gz_map','kvm_gz_probe'))+'\n')
        (self.report/'build-validation.json').write_text('{"old_success":true}\n')
        return output

    def test_generated_config_failure_clears_stale_gate(self):
        output=self.verify_fixture();p=output/'.config'
        p.write_text(p.read_text()+'\n# CONFIG_KVM is not set\n')
        with patch.object(gz,'git',return_value=gz.manifest()['base_commit']):
            with self.assertRaisesRegex(AssertionError,'Generated config mismatch: CONFIG_KVM'):
                gz.verify(self.work,self.report,False,False,False)
        self.assertFalse((self.report/'build-validation.json').exists())

    def test_export_crc_change_blocks_gate(self):
        output=self.verify_fixture();text=(gz.PATCHES/'abi/Module.symvers').read_text()
        crc=text.split()[0];(output/'Module.symvers').write_text(text.replace(crc,'0xdeadbeef',1))
        with patch.object(gz,'git',return_value=gz.manifest()['base_commit']):
            with self.assertRaisesRegex(AssertionError,'Original GKI exported ABI changed'):
                gz.verify(self.work,self.report,False,False,False)
        self.assertFalse((self.report/'build-validation.json').exists())
        self.assertFalse(json.loads((self.report/'export-crc-validation.json').read_text())['passed'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fixture',type=Path,required=True)
    FIXTURE=p.parse_args().fixture.resolve();unittest.main(argv=['test_gz_kvm.py'])
