from pathlib import Path
from urllib.request import urlopen
import json,hashlib
root=Path(__file__).resolve().parent
with urlopen('https://zenodo.org/api/records/4032561',timeout=60) as r:meta=json.load(r)
(root/'zenodo_metadata.json').write_text(json.dumps(meta,indent=2))
(root/'raw').mkdir(exist_ok=True)
for f in meta['files']:
 with urlopen(f['links']['self'],timeout=60) as r:b=r.read()
 assert 'md5:'+hashlib.md5(b).hexdigest()==f['checksum']
 (root/'raw'/f['key']).write_bytes(b)
 print(f['key'],'checksum verified')
