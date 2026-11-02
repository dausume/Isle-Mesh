"""Small read-only resource inventory; no benchmarks, allocations or disk writes."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET


def cpu_set(text):
    result = set()
    for token in (text or '').split(','):
        token = token.strip()
        if not token: continue
        excluded = token.startswith('^')
        token = token.lstrip('^')
        try:
            lo, hi = (token.split('-', 1) + [token])[:2] if '-' in token else (token, token)
            lo, hi = int(lo), int(hi)
            if lo < 0 or hi < lo or hi > 65535: continue
            ids = set(range(lo, hi + 1))
            result = result - ids if excluded else result | ids
        except ValueError: continue
    return sorted(result)


def vm_allocation(name, xml):
    root = ET.fromstring(xml)
    vcpu = root.find('vcpu')
    affinity = [{'vcpu':p.get('vcpu'), 'cpus':cpu_set(p.get('cpuset'))}
                for p in root.findall('./cputune/vcpupin')][:512]
    return {'name':name, 'kind':'vm', 'version':'libvirt-config:'+hashlib.sha256(xml.encode()).hexdigest(), 'vcpus':int(vcpu.get('current') or vcpu.text or 0) if vcpu is not None else 0,
            'cpus':cpu_set(vcpu.get('cpuset')) if vcpu is not None else [], 'pins':affinity,
            'exclusive':False, 'scope':'running domain XML'}


def cpu_ticks(path='/proc/stat'):
    result = {}
    for line in Path(path).read_text().splitlines():
        parts=line.split()
        if not parts or not parts[0].startswith('cpu') or not parts[0][3:].isdigit(): continue
        # guest times are already included in user/nice; do not count twice.
        values=list(map(int,parts[1:9]))
        if len(values)>=5: result[int(parts[0][3:])] = (sum(values),values[3]+values[4])
    return result


def activity(before, after):
    total=after[0]-before[0]; idle=after[1]-before[1]
    return round(100*max(0,min(total,total-idle))/total,1) if total>0 and idle>=0 else None


def storage_inventory(tree):
    volumes={}; drives={}
    def walk(node, tier='unclassified', transport='', backing=''):
        kind=node.get('type'); name=str(node.get('name',''))
        if kind in ('loop','rom'): return
        if kind=='disk':
            transport=node.get('tran') or ''
            tier='rotational' if node.get('rota') is True or node.get('rota')==1 else ('nvme' if transport=='nvme' else 'solid-state') if node.get('rota') is False or node.get('rota')==0 else 'unclassified'
            backing=name
            drives[name]={'name':name,'tier':tier,'transport':transport,'sizeMiB':int(node.get('size') or 0)//1048576}
        mounts=[m for m in node.get('mountpoints',[]) if m and m.startswith('/')]
        key=str(node.get('maj:min') or name)
        if mounts and kind in ('disk','part','crypt','lvm','raid0','raid1','raid5','raid6','raid10','md'):
            if key in volumes:
                v=volumes[key]
                if backing not in v['backing']: v['backing'].append(backing)
                if tier!=v['tier']:v['tier']='mixed'
            else:
                v={'name':name,'mounts':mounts,'backing':[backing] if backing else [],'tier':tier,'transport':transport}
                try:
                    use=shutil.disk_usage(mounts[0]);v.update(totalMiB=use.total//1048576,availableMiB=use.free//1048576,usedMiB=use.used//1048576)
                except OSError:v['capacityStatus']='not readable'
                volumes[key]=v
        for child in node.get('children',[]): walk(child,tier,transport,backing)
    for node in tree.get('blockdevices',[]):walk(node)
    return {'drives':list(drives.values())[:64],'volumes':list(volumes.values())[:128],
            'performanceStatus':'Device class only; retrieval latency and throughput have not been measured.'}


def collect(run, vms, vm_status):
    detail={'schemaVersion':1,'cpus':[],'allocations':vms,'vmStatus':vm_status,
            'allocationNote':'Affinity constrains placement; it does not establish exclusive ownership. Unpinned workloads share host CPUs.'}
    try:
        first=cpu_ticks();time.sleep(.15);second=cpu_ticks()
        code,out,_=run(['lscpu','-J','-e=CPU,CORE,SOCKET,ONLINE'])
        topology=json.loads(out).get('cpus',[]) if code==0 else []
        if not topology:topology=[{'cpu':i,'online':True} for i in second]
        isolated=cpu_set(Path('/sys/devices/system/cpu/isolated').read_text())
        for row in topology[:512]:
            i=int(row['cpu']);online=row.get('online') in (True,'yes','Y',1)
            detail['cpus'].append({'id':i,'core':row.get('core'),'socket':row.get('socket'),'online':online,'isolated':i in isolated,
                                   'busyPercent':activity(first[i],second[i]) if online and i in first and i in second else None})
        detail['cpuStatus']='observed';detail['sampleSeconds']=.15
    except (OSError,ValueError,TypeError,KeyError,AttributeError):detail['cpuStatus']='not readable'
    code,out,_=run(['lsblk','-J','-b','-o','NAME,MAJ:MIN,TYPE,ROTA,TRAN,SIZE,MOUNTPOINTS'])
    try:
        detail['storage']=storage_inventory(json.loads(out)) if code==0 else {}
        detail['storageStatus']='observed' if code==0 else 'not readable'
    except (ValueError,TypeError,AttributeError):detail['storageStatus']='not readable'
    code,out,_=run(['docker','ps','-q'])
    ids=out.splitlines()[:64] if code==0 else []
    detail['containerStatus']='observed (up to 64 running containers)' if code==0 else 'not readable'
    if ids:
        code,out,_=run(['docker','inspect',*ids])
        try:
            if code!=0:raise ValueError('inspect failed')
            rows=json.loads(out)
            detail['appUsage']=container_usage(rows)
            for row in rows:
                host=row.get('HostConfig',{});quota=host.get('NanoCpus',0)/1e9
                if not quota and host.get('CpuQuota',0)>0 and host.get('CpuPeriod',0)>0:quota=host['CpuQuota']/host['CpuPeriod']
                detail['allocations'].append({'name':row.get('Name','').lstrip('/'),'kind':'container','cpus':cpu_set(host.get('CpusetCpus')),
                    'quotaCpus':quota or None,'exclusive':False,'scope':'running container HostConfig'})
        except (ValueError,TypeError,AttributeError):detail['containerStatus']='not readable'
    detail.setdefault('appUsage',[]).extend(vm_usage(run,vms))
    return detail


def container_usage(rows):
    """One bounded cgroup v2 sample, all containers in the same 150ms window."""
    samples=[]
    def read_cpu(path):
        return int(dict(line.split() for line in (path/'cpu.stat').read_text().splitlines())['usage_usec'])
    for row in rows[:64]:
        try:
            pid=int(row.get('State',{}).get('Pid',0))
            if pid<=0:continue
            relative=next(line[3:] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::'))
            path=(Path('/sys/fs/cgroup')/relative.lstrip('/')).resolve()
            if not path.is_relative_to('/sys/fs/cgroup'):continue
            labels=row.get('Config',{}).get('Labels') or {};project=labels.get('com.docker.compose.project','');service=labels.get('com.docker.compose.service','')
            if project=='polari-isle':app={'frontend':'polari','backend':'polari-api'}.get(service,'')
            elif project=='isle-sample-app':app='sample'
            elif project.startswith('isle-') and project not in ('isle-agent','isle-remote-agent'):app=project[5:]
            else:app=''
            if not app:continue
            memory=int((path/'memory.current').read_text())/1048576
            samples.append((path,read_cpu(path),{'app':app,'instance':row['Id'][:12],'version':row.get('Image',''),'ramMiB':round(memory,3),'source':'cgroup-v2 memory.current / cpu.stat; includes container cache'}))
        except (OSError,ValueError,KeyError,StopIteration):continue
    start=time.monotonic()
    if samples:time.sleep(.15)
    result=[]
    for path,previous,item in samples:
        try:
            item['cpus']=round(max(0,read_cpu(path)-previous)/1e6/(time.monotonic()-start),4)
            item['sampleSeconds']=round(time.monotonic()-start,3);result.append(item)
        except (OSError,ValueError,KeyError):continue
    return result


def vm_usage(run,vms):
    if not vms:return []
    def read():
        code,out,_=run(['virsh','-c','qemu:///system','domstats','--list-running','--cpu-total','--balloon'])
        if code!=0:return {}
        data={};current=None
        for line in out.splitlines():
            if line.startswith('Domain: '):
                current=line[8:].strip().strip("'");data[current]={}
            elif current and '=' in line:
                key,value=line.strip().split('=',1)
                try:data[current][key]=int(value)
                except ValueError:pass
        return data
    first=read();start=time.monotonic();time.sleep(.15);second=read();elapsed=time.monotonic()-start;result=[]
    for vm in vms[:32]:
        name=vm['name'];a=first.get(name,{});b=second.get(name,{})
        item={'app':'openwrt' if name=='openwrt-isle-router' else name,'version':vm.get('version','libvirt-config-unreported'),'source':'libvirt balloon.rss host process memory / cpu.time; not guest reserved RAM','sampleSeconds':round(elapsed,3)}
        if 'balloon.rss' in b:item['ramMiB']=b['balloon.rss']/1024
        if 'cpu.time' in a and 'cpu.time' in b:item['cpus']=round(max(0,b['cpu.time']-a['cpu.time'])/1e9/elapsed,4)
        if 'ramMiB' in item or 'cpus' in item:result.append(item)
    return result
