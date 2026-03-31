#!/bin/bash
# Export current snapshots and machines data for static GitHub Pages
# Run while the server is active on localhost:3030

set -e

API="http://127.0.0.1:3030"
OUT="docs"
SNAP_DIR="$OUT/snapshots"

mkdir -p "$SNAP_DIR"

# 1. Export machines.json
echo "Exporting machines.json..."
curl -s "$API/api/machines" | python3 -c "import sys,json; json.dump(json.load(sys.stdin), sys.stdout, indent=2, ensure_ascii=False)" > "$OUT/machines.json"

# 2. Export snapshots metadata and images
echo "Exporting snapshots..."
SNAP_JSON=$(curl -s "$API/api/teamwork/snapshots")
echo "$SNAP_JSON" | python3 -c "
import sys, json

data = json.load(sys.stdin)
snaps = data.get('snapshots', {})
static_snaps = {}

for machine_id, snap in snaps.items():
    entry = dict(snap)
    t = snap.get('type')
    if t == 'image':
        filename = f'{machine_id}.jpg'
        entry['image'] = f'snapshots/{filename}'
        entry['_download'] = snap['image']
    elif t == 'images':
        filenames = []
        downloads = []
        for i, img_path in enumerate(snap.get('images', [])):
            key = img_path.split('/')[-1]
            filename = f'{key}.jpg'
            filenames.append(f'snapshots/{filename}')
            downloads.append(img_path)
        entry['images'] = filenames
        entry['_downloads'] = downloads
    static_snaps[machine_id] = entry

json.dump(static_snaps, sys.stdout, indent=2, ensure_ascii=False)
" > "$OUT/snapshots.json"

# 3. Download screenshot images
python3 -c "
import json, urllib.request, sys, os

api = '$API'
out = '$SNAP_DIR'

with open('$OUT/snapshots.json') as f:
    snaps = json.load(f)

for machine_id, snap in snaps.items():
    if '_download' in snap:
        url = api + snap['_download']
        dest = os.path.join(out, f'{machine_id}.jpg')
        print(f'  {machine_id} -> {dest}')
        urllib.request.urlretrieve(url, dest)
    if '_downloads' in snap:
        for i, dl in enumerate(snap['_downloads']):
            key = dl.split('/')[-1]
            url = api + dl
            dest = os.path.join(out, f'{key}.jpg')
            print(f'  {machine_id}[{i}] -> {dest}')
            urllib.request.urlretrieve(url, dest)
"

# 4. Clean _download keys from snapshots.json
python3 -c "
import json
with open('$OUT/snapshots.json') as f:
    snaps = json.load(f)
for v in snaps.values():
    v.pop('_download', None)
    v.pop('_downloads', None)
with open('$OUT/snapshots.json', 'w') as f:
    json.dump(snaps, f, indent=2, ensure_ascii=False)
"

# 5. Copy only styles (shared). HTML/JS in docs/ are adapted for static mode — don't overwrite.
cp public/styles.css "$OUT/"

echo ""
echo "Export complete -> $OUT/"
ls -la "$OUT/"
