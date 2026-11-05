#!/usr/bin/env python3
"""Verify typed image archive -> manifest -> config linkage, without extracting tar."""
import hashlib,json,re,tarfile
DIGEST=re.compile(r'sha256:[0-9a-f]{64}\Z')
def digest(value):
    if not isinstance(value,str) or not DIGEST.fullmatch(value): raise ValueError('Expected typed sha256 image digest')
    return value

def verify_archive(path, image):
    config=digest(image['configDigest']); manifest=image.get('manifestDigest')
    if manifest is not None: digest(manifest)
    with tarfile.open(path,'r:*') as archive:
        members=archive.getmembers();names=[m.name for m in members]
        if len(names)!=len(set(names)): raise ValueError('Duplicate archive members')
        def data(name,limit=2*1024*1024):
            member=archive.getmember(name)
            if not member.isfile() or member.size>limit: raise ValueError('Invalid/oversized image metadata')
            return archive.extractfile(member).read()
        def blob(ref):
            digest(ref); name='blobs/sha256/'+ref[7:];member=archive.getmember(name)
            if not member.isfile(): raise ValueError('Image blob is not a file')
            h=hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            if 'sha256:'+h.hexdigest()!=ref: raise ValueError('Image blob checksum mismatch')
            return name
        if manifest:
            index=json.loads(data('index.json'))
            if not any(m.get('digest')==manifest for m in index.get('manifests',[])):
                raise ValueError('Declared image manifest is not a root archive manifest')
            document=json.loads(data(blob(manifest)))
            if document.get('config',{}).get('digest')!=config: raise ValueError('Manifest/config identity mismatch')
            blob(config)
            for layer in document.get('layers',[]):blob(layer['digest'])
        else:
            # Legacy docker-save archives expose a config identity, not an OCI manifest ID.
            entries=json.loads(data('manifest.json'))
            found=False
            for entry in entries:
                raw=data(entry['Config'])
                if 'sha256:'+hashlib.sha256(raw).hexdigest()==config:
                    if image['ref'] not in entry.get('RepoTags',[]):continue
                    found=True
                    for layer in entry['Layers']:
                        if not archive.getmember(layer).isfile():raise ValueError('Invalid layer member')
            if not found:raise ValueError('Legacy config/ref is not present in archive')
    return {config} | ({manifest} if manifest else set())

def admit_installed(actual, verified):
    digest(actual)
    if actual not in verified:raise ValueError('Loaded Docker identity is not the verified config or linked manifest')
    return actual

def describe(path,ref):
    with tarfile.open(path,'r:*') as archive:
        try:
            index=json.load(archive.extractfile('index.json'))
            leaves=index['manifests']
            matches=[m for m in leaves if m.get('annotations',{}).get('io.containerd.image.name') in (ref,'docker.io/'+ref)]
            leaf=(matches or leaves)
            if len(leaf)!=1:raise ValueError('Select a single platform image archive before publication')
            md=digest(leaf[0]['digest']);manifest=json.load(archive.extractfile('blobs/sha256/'+md[7:]));cd=digest(manifest['config']['digest'])
        except KeyError:
            entries=json.load(archive.extractfile('manifest.json'));matches=[m for m in entries if ref in m.get('RepoTags',[])]
            if len(matches)!=1:raise ValueError('Archive tag does not uniquely identify an image')
            cd='sha256:'+hashlib.sha256(archive.extractfile(matches[0]['Config']).read()).hexdigest();md=None
    item=dict(ref=ref,configDigest=cd,archive={'source':str(path)})
    if md:item['manifestDigest']=md
    verify_archive(path,item)
    return item

if __name__=='__main__':
    import sys
    try:
        if len(sys.argv)!=3:raise ValueError('Usage: isle update describe-image <archive> <image-ref>')
        print(json.dumps(describe(sys.argv[1],sys.argv[2]),indent=2))
    except (OSError,ValueError,KeyError,tarfile.TarError) as e:print(str(e),file=sys.stderr);sys.exit(1)
