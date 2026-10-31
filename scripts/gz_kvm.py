"""Exact kit-09 source/config/ABI checks, invoked only by the optional GZ job."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PATCHES=ROOT/'patches/gz-kvm'
REQUIRED_CONFIG={
    'CONFIG_VIRTUALIZATION':'y','CONFIG_KVM':'y','CONFIG_ARM64_VHE':'y',
    'CONFIG_ARM64_4K_PAGES':'y','CONFIG_ARM64_VA_BITS':'39',
    'CONFIG_LTO_CLANG_FULL':'y','CONFIG_CFI_CLANG':'y','CONFIG_CMDLINE_EXTEND':'y',
    'CONFIG_SYSVIPC':'y','CONFIG_POSIX_MQUEUE':'y','CONFIG_IPC_NS':'y',
    'CONFIG_PID_NS':'y','CONFIG_DEVTMPFS':'y',
}
IPC_PATCHES=('001.GKI-below-6.12-fix_sysvipc_kabi_6_7_8.patch',
             '002.5.10_or_lower_use_android_abi_padding_for_posix_mqueue.patch')

def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def git(common,*args):
    return subprocess.check_output(['git','-C',str(common),*args],text=True).strip()

def manifest():
    r=json.loads((PATCHES/'manifest.json').read_text())
    assert digest(PATCHES/r['patch'])==r['patch_sha256'],'GZ patch hash mismatch'
    return r

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2)+'\n')

def configurations(path):
    r={}
    for line in path.read_text().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            k,v=line.split('=',1);r[k]=v
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            r[line[2:-11]]='n'
    return r

def check_sources(common,patched):
    r=manifest();key='patched_sha256' if patched else 'base_sha256'
    for name,entry in r['files'].items():
        p=common/name;expected=entry[key]
        assert (not p.exists() if expected is None else p.is_file() and digest(p)==expected), 'Unexpected GZ source: '+name

def prepare(common,report):
    r=manifest()
    assert not git(common,'status','--porcelain'),'GZ source pin requires a clean checkout'
    if git(common,'rev-parse','HEAD')!=r['base_commit']:
        subprocess.run(['git','-C',str(common),'fetch','--depth=1',
                        'https://android.googlesource.com/kernel/common',r['base_commit']],check=True)
        subprocess.run(['git','-C',str(common),'checkout','--detach',r['base_commit']],check=True)
    assert git(common,'rev-parse','HEAD')==r['base_commit']
    check_sources(common,False)
    subprocess.run(['git','-C',str(common),'apply','--check',str(PATCHES/r['patch'])],check=True)
    subprocess.run(['git','-C',str(common),'apply',str(PATCHES/r['patch'])],check=True)
    check_sources(common,True)
    write(report/'source-validation.json',{'base_commit':r['base_commit'],
          'patch_sha256':r['patch_sha256'],'files_checked':len(r['files']),
          'firmware_abi':r['firmware_abi'],'device_tested':False})

def configure(common,report):
    check_sources(common,True)
    abi=json.loads((PATCHES/'abi/manifest.json').read_text())['sha256']
    states={}
    for name in IPC_PATCHES:
        p=PATCHES/'abi'/name;assert digest(p)==abi[name]
        command=['git','-C',str(common),'apply']
        if subprocess.run(command+['--check',str(p)],capture_output=True).returncode==0:
            subprocess.run(command+[str(p)],check=True);states[name]='applied'
        else:
            subprocess.run(command+['--reverse','--check',str(p)],check=True)
            states[name]='already_applied'
    config=common/'arch/arm64/configs/gki_defconfig'
    values=configurations(config)
    tokens=json.loads(values.get('CONFIG_CMDLINE','""')).split()
    tokens=[t for t in tokens if not t.startswith(('kvm-arm.gz=','kvm-arm.mode='))]
    args=[]
    for key,value in REQUIRED_CONFIG.items():
        args+=['--enable',key[7:]] if value=='y' else ['--set-val',key[7:],value]
    for key in ('LTO_CLANG_THIN','LTO_NONE','ARM64_16K_PAGES','ARM64_64K_PAGES','ARM64_VA_BITS_48','CMDLINE_FORCE'):
        args+=['--disable',key]
    args+=['--enable','ARM64_VA_BITS_39','--set-str','CMDLINE',
           ' '.join(['kvm-arm.gz=shared','kvm-arm.mode=nvhe']+tokens)]
    subprocess.run([str(common/'scripts/config'),'--file',str(config),*args],check=True)
    write(report/'config-request.json',{'required':REQUIRED_CONFIG,'defconfig_sha256':digest(config),
          'ipc_padding':states,'device_tested':False})

def verify(work,report,ksu,susfs,bbg):
    # A failed verification must never leave a success gate from a previous run.
    gate=report/'build-validation.json'
    if gate.exists():gate.unlink()
    r=manifest();common=work/'common';check_sources(common,True)
    assert git(common,'rev-parse','HEAD')==r['base_commit']
    output=work/'out/android12-5.10/common';dist=work/'out/android12-5.10/dist'
    generated=output/'.config';actual=configurations(generated)
    required={**REQUIRED_CONFIG,'CONFIG_KSU':'y' if ksu else 'n',
              'CONFIG_KSU_SUSFS':'y' if susfs else 'n','CONFIG_BBG':'y' if bbg else 'n'}
    for key,value in required.items():
        assert actual.get(key,'n')==value,'Generated config mismatch: '+key
    tokens=json.loads(actual['CONFIG_CMDLINE']).split()
    assert [t for t in tokens if t.startswith(('kvm-arm.gz=','kvm-arm.mode='))]==['kvm-arm.gz=shared','kvm-arm.mode=nvhe']
    assert tokens.index('kvm-arm.gz=shared')<tokens.index('kvm-arm.mode=nvhe')
    system=(output/'System.map').read_text()
    symbols=['__kvm_nvhe___gz_kvm_entry','__kvm_nvhe_gz_shared_stage1',
             'gz_shared_stage1','kvm_gz_bind','kvm_gz_cpu_init','kvm_gz_map','kvm_gz_probe']
    names={row[2] for line in system.splitlines() if len(row:=line.split())>=3}
    assert set(symbols)<=names,'Missing linked GZ/KVM backend'
    abi=PATCHES/'abi';hashes=json.loads((abi/'manifest.json').read_text())['sha256']
    for name,expected in hashes.items():assert digest(abi/name)==expected,'ABI fixture modified: '+name
    def crcs(p):return {row[1]:row[0] for line in p.read_text().splitlines() if len(row:=line.split())>=2}
    before,after=crcs(abi/'Module.symvers'),crcs(output/'Module.symvers')
    changed={name:{'before':crc,'after':after.get(name)} for name,crc in before.items() if after.get(name)!=crc}
    write(report/'export-crc-validation.json',{'original_exports':len(before),'differences':changed,'passed':not changed})
    assert len(before)==7219 and not changed,'Original GKI exported ABI changed'
    from gz_kvm_ipc import main as verify_ipc
    verify_ipc(output/'vmlinux',report/'ipc-layout-validation.json')
    files={}
    for name,src in [('Image',dist/'Image'),('generated.config',generated),
                     ('System.map',output/'System.map'),('Module.symvers',output/'Module.symvers')]:
        shutil.copyfile(src,report/name);files[name]={'sha256':digest(src),'bytes':src.stat().st_size}
    dependencies={}
    for name,path in [('KernelSU',work/'KernelSU'),('susfs4ksu',ROOT/'susfs4ksu'),
                      ('Baseband-guard',work/'Baseband-guard')]:
        if path.exists():
            try:dependencies[name]=git(path,'rev-parse','HEAD')
            except subprocess.CalledProcessError:dependencies[name]='not recorded'
    write(gate,{'schema':'gz-kvm-build-validation-v1','base_commit':r['base_commit'],
          'patch_sha256':r['patch_sha256'],'files':files,'required_config':required,
          'linked_symbols':symbols,'original_export_crcs_verified':len(before),
          'ipc_layout_validation_sha256':digest(report/'ipc-layout-validation.json'),
          'dependencies':dependencies,'firmware_abi':r['firmware_abi'],'device_tested':False,
          'limits':'New CI artifact; requires matching shared/native-pin TEE and its own device tests.'})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','configure','verify'))
    p.add_argument('--work',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    for flag in ('ksu','susfs','bbg'):p.add_argument('--'+flag,choices=('true','false'),default='false')
    a=p.parse_args();a.report.mkdir(parents=True,exist_ok=True)
    if a.action=='verify':verify(a.work,a.report,a.ksu=='true',a.susfs=='true',a.bbg=='true')
    else:globals()[a.action](a.work/'common',a.report)

if __name__=='__main__':main()
