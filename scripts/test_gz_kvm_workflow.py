"""Check the optional route without changing the original disabled workflow.

Usage: python3 scripts/test_gz_kvm_workflow.py --baseline <original Git commit>
Requires PyYAML for structural workflow comparison; runs no builds or network calls.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import yaml

ROOT=Path(__file__).resolve().parents[1]
class Loader(yaml.SafeLoader):pass
Loader.yaml_implicit_resolvers={k:[(tag,rx) for tag,rx in v if tag!='tag:yaml.org,2002:bool']
                                for k,v in Loader.yaml_implicit_resolvers.items()}
Loader.add_implicit_resolver('tag:yaml.org,2002:bool',re.compile(r'^(?:true|false)$',re.I),list('tTfF'))
def load(text):return yaml.load(text,Loader=Loader)
def original(commit,name):
    return load(subprocess.check_output(['git','-C',str(ROOT),'show',commit+':.github/workflows/'+name],text=True))
def current(name):return load((ROOT/'.github/workflows'/name).read_text())

def main(commit):
    entry=current('kernel-a12-5-10.yml');base=original(commit,'kernel-a12-5-10.yml')
    for event in ('workflow_dispatch','workflow_call'):
        option=entry['on'][event]['inputs']['use_gz_kvm']
        assert option['type']=='boolean' and option['default'] is False and option['required'] is False
    normal=entry['jobs']['build-kernels-a12-5-10']
    assert normal['if']=='${{ !inputs.use_gz_kvm }}'
    assert len(normal['strategy']['matrix']['include'])==22
    special=entry['jobs']['build-gz-kvm'];assert special['if']=='${{ inputs.use_gz_kvm }}'
    assert special['uses']==normal['uses'] and special['secrets']==normal['secrets']
    forwarded={**normal['with'],'sub_level':'226','os_patch_level':'2024-11','revision':'r8','use_gz_kvm':True}
    assert special['with']==forwarded,'GZ route changed another input choice'
    off=copy.deepcopy(entry)
    for event in ('workflow_dispatch','workflow_call'):del off['on'][event]['inputs']['use_gz_kvm']
    del off['jobs']['build-gz-kvm'];del off['jobs']['build-kernels-a12-5-10']['if']
    assert off==base,'Original Android 12 entry changed'

    build=current('build.yml');base_build=original(commit,'build.yml')
    assert build['on']['workflow_call']['inputs']['use_gz_kvm']['default'] is False
    off=copy.deepcopy(build);del off['on']['workflow_call']['inputs']['use_gz_kvm']
    baseline_names={s['name'] for s in base_build['jobs']['build-kernel']['steps']}
    original_steps=[];added=[]
    for step in off['jobs']['build-kernel']['steps']:
        if step['name'] not in baseline_names:
            assert 'inputs.use_gz_kvm' in step['if'] and '!inputs.use_gz_kvm' not in step['if']
            added.append(step);continue
        if step['name']=='编译内核':
            assert step.pop('if')=='${{ !inputs.use_gz_kvm }}'
        elif step['name']=='构建 Boot 镜像 (Android 12)':
            assert step['if']=="inputs.android_version == 'android12' && !inputs.use_gz_kvm"
            step['if']="inputs.android_version == 'android12'"
        original_steps.append(step)
    off['jobs']['build-kernel']['steps']=original_steps
    assert off==base_build,'An existing reusable build step or setting changed'
    names={s['name']:i for i,s in enumerate(build['jobs']['build-kernel']['steps'])}
    assert names['初始化并同步内核源码']<names['应用 GZ/KVM 精确版本补丁']<names['应用 Stock Config 伪装']
    assert names['添加 SUSFS 配置']<names['配置 GZ/KVM 与 IPC ABI']<names['编译 GZ/KVM 内核']<names['核对 GZ/KVM 成品']<names['准备 Boot 镜像']
    gz_compile=next(s for s in added if s['name']=='编译 GZ/KVM 内核')
    assert 'LTO=full' in gz_compile['with']['command']
    assert 'LTO=thin' in next(s for s in original_steps if s['name']=='编译内核')['with']['command']
    # All local reusable workflow calls resolve and supply valid required inputs.
    for p in (ROOT/'.github/workflows').glob('*.yml'):
        doc=load(p.read_text())
        for job in doc.get('jobs',{}).values():
            target=job.get('uses','')
            if target.startswith('./.github/workflows/'):
                called=current(Path(target).name)['on']['workflow_call']['inputs']
                supplied=job.get('with',{})
                assert set(supplied)<=set(called),(p,set(supplied)-set(called))
                assert {k for k,v in called.items() if v.get('required') and 'default' not in v}<=set(supplied),p
    # Parse every run block in the changed workflows; do not execute it.
    shell_count=0
    for doc in (entry,build):
        for job in doc['jobs'].values():
            for step in job.get('steps',[]):
                command=step.get('run') or step.get('with',{}).get('command')
                if not isinstance(command,str):continue
                command=re.sub(r'\$\{\{.*?\}\}','test-value',command)
                subprocess.run(['bash','-n'],input=command,text=True,check=True,capture_output=True)
                shell_count+=1
    changed=subprocess.check_output(['git','-C',str(ROOT),'diff','--name-only',commit],text=True).splitlines()
    assert set(p for p in changed if p.startswith('.github/workflows/'))=={
        '.github/workflows/build.yml','.github/workflows/kernel-a12-5-10.yml'}
    print(json.dumps({'original_entry_preserved_when_disabled':True,'original_build_steps_preserved_when_disabled':True,
          'original_versions':22,'gz_versions':['5.10.226/2024-11'],'other_workflows_unchanged':True,
          'shell_blocks_validated':shell_count,'new_gz_steps':len(added),'github_actions_run':False},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',required=True)
    main(p.parse_args().baseline)
