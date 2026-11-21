"""Validate native vs vertical output against FFprobe for 360p, 1080p and 4K."""
import json
from pathlib import Path

from source_env import QUALITY
from app import media

out = QUALITY/'tests/media-formats'
out.mkdir(exist_ok=False)
results = []
for width,height in [(640,360),(1920,1080),(3840,2160)]:
    folder = out/str(height)
    folder.mkdir()
    source = folder/'source.mp4'
    media.run([media.tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
               '-f','lavfi','-i',f'testsrc2=size={width}x{height}:rate=24',
               '-f','lavfi','-i','sine=frequency=480:sample_rate=48000',
               '-t','2','-c:v','libx264','-preset','ultrafast','-threads','4','-c:a','aac',str(source)])
    actual = media.probe(source)
    assert (actual['width'],actual['height']) == (width,height) and actual['has_audio']
    result = {'source':actual,'output':{}}
    for aspect in ['original','vertical']:
        info = media.export_clip(source,folder/aspect,
                                 {'start':.25,'end':1.25,'aspect':aspect,'caption_style':'none','crop_x':.5},
                                 None,actual)
        expected=(width,height) if aspect=='original' else (1080,1920)
        assert (info['width'],info['height'])==expected and info['video_codec']=='h264' and info['audio_codec']=='aac'
        assert abs(info['duration']-1)<.08
        result['output'][aspect]=info
    results.append(result)
    print(f'PASS {height}p source → original + vertical MP4', flush=True)
(out/'report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
