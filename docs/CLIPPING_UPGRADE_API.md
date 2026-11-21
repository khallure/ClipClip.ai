# V1 clipping-focused upgrade — API contract

V1 regression checks pass, original data is preserved, and V2 motion code is removed. These additions are implemented on the existing upload, analysis, preview and export API. See `V1_UPGRADE_VERIFICATION.md` for final test results and remaining prerequisites.

## Duration

AnalysisSettings adds `duration_preset:'20'|'30'|'45'|'60'|'auto'|'custom'` default`auto` forbackwardscompatibility. Numeric presets derived onserver:20→min8,max20,target18;30→min10,max30,target27;45→min10,max45,target40;60→min12,max60,target54. Auto preservesV1 min20/max90/target45 and APIinvalidmin19stillfails unlesspresetisnumeric. Custom usesvalidated5<=min<=target<=max<=90. Analysisagent _settings appliespresets independentlysoUIcannotbypasscaps. ExportSettings adds same duration_preset optionalNone; ifomittedroot usesjobsettingspreset, elseexplicit. Numericpreset end-startcannotexceedcap; mediaverificationallowsatmostoneoutputframeforcontainerduration. Ifno coherent20sclip, returnemptyorqualifiedwarning, nevertruncate mid-sentence. Candidate fieldsV1unchanged.

## Caption module

Style Pydantic `CaptionStyle` fields: font_weight600|700 default600, font_size_pct2..9 default4.5 (% ofmin(width,height)), position_y_pct20..85 default70, active_color#D7F35C default, stroke_px0..3 default1.0, shadow_px0..3 default.5, max_words2..5 default4. Whiteinactivewords, noopaqueplate/noanimations. BundletruePoppins600/700 +OFL: backend/assets/fonts/{Poppins-SemiBold.ttf,Poppins-Bold.ttf,OFL.txt}; frontend/public/fonts sameTTFs +OFL. ASS600 usesfamily'Poppins SemiBold' peractualfontprobe;700Poppins.

Canonical doc: `{version:'1.0',start:number,end:number,duration:number,width:int,height:int,style:CaptionStyle,words:[{id:int,start:number,end:number,text:string}],groups:[{id:int,word_ids:int[],lines:int[][],start:number,end:number}],warnings:string[]}`. Timesclip-relative; ids GLOBAL canonicalflattenedwordIDs (so correctionstable). Groupslines explicitfromsamealgorithmforbrowser/ASS; usually2–5words,1or2lines, naturalpunctuation/gaps. Ifwordtimingunavailable doNOTinterpolatetimestamps; emptywords+warning.

ModuleAPI: `build_caption_document(segments:list[dict],start:float,end:float,width:int,height:int,style:dict|CaptionStyle|None=None,corrections:dict|None=None)->dict`; `to_ass(document:dict)->str`; `to_srt(document)->str`; `to_vtt(document)->str`; `font_directory()->Path`. InputsegmentsV1{start,end,text,words:[{start,end,word}]}; correctionsmapsglobalid-string→singlewordtext preservingtimes. ExposesCaptionStylefromcaptions.py.

CaptionAPIroot:
- POST `/api/jobs/{id}/caption-preview` JSON `{start,end,aspect:'original'|'vertical',style:CaptionStyle}` returnscanonicaldoc; actualdimensionssameexport(originalevenpad or1080×1920).
- PATCH `/api/jobs/{id}/caption-words` JSON `{changes:[{word_id:int,text:string}]}` persistscaption-onlycorrections, returns`{saved:true}`. Word textsingleatomnonempty<=80; originaltranscript/analysisuntouched.
- POST `/api/jobs/{id}/captions/align` queuesactualWhisperwordalignment ifexistingtranscriptlacksrealwords; storesseparatecaption_alignment.json; neveroverwritesanalysistranscript.
- GET exports/{export_id}/captions.ass addsstyledsidecar; SRTstillworks. Documentincludesrealwordtimings/corrections; previewusesvideo.currentTime-doc.start, displayonlyactivegroup; exactlyoneactivewordifinwordinterval,noneingaps.
- ExportSettings `caption_style` enumadds`poppins_highlight`; legacy`none|clean|bold`retainedAPIcompat butUIonlyOFF/PoppinsHighlight. `subtitle_style:CaptionStyle` field; defaultfrontPoppinsON. `duration_preset`asabove. media.export_clip acceptsoptional`corrections` keyword (rootownsmedia) andusescaptionsmod forASSsidecars/burn-in. Crop/resizefirst,captionsthen; accuratePoppinsfontslibassfontsdirprovided, neverloadfontfromclientURL.

## One-link ingestion

`VideoSourceIngestor.ingest(url, job_directory, *, cancelled=None, progress=None, ensure_capacity=None)` returns `{source_path:Path, media:dict, title, video_id, cache_hit, warnings, metadata, source_size_bytes}` after real FFprobe validation of audio and video. The adapter accepts only validated single-video YouTube URLs, selects the best supported direct HTTPS video up to the configured height plus audio, and uses FFmpeg to merge local tracks. It never sends remote media manifests to FFmpeg. No arbitrary-URL downloader, authenticated cookies, login, proxy rotation, DRM or access bypass is exposed.

`YOUTUBE_INGEST_ENABLED` is the runtime feature switch. The deprecated `YOUTUBE_APPROVAL_REFERENCE` and `YOUTUBE_APPROVED_VIDEO_IDS` variables are no longer read or checked. They were application-created gates, not yt-dlp parameters, API keys or verification of platform permission. Removing them does not grant access or usage rights. Source availability and applicable permissions still matter; see [yt-dlp embedding](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp) and [YouTube terms](https://www.youtube.com/static?template=terms). YouTube API developer policies should not be represented as configuration requirements of this standalone adapter.

POST `/api/jobs/{id}/ingest` accepts `IngestSettings`: all unchanged `AnalysisSettings` fields plus:

```json
{
  "duration_preset": "20",
  "auto_export": true,
  "output": {
    "aspect": "original",
    "crop_x": 0.5,
    "caption_style": "poppins_highlight",
    "subtitle_style": {"font_weight": 600, "active_color": "#D7F35C"}
  }
}
```

`auto_export` defaults to `false` for existing API clients that expect to review recommendations first. The URL workflow in the UI sends `true`, chains source acquisition → cached or actual Whisper transcription → unchanged V1 analysis → export of the highest-ranked validated candidate, and downloads its verified MP4 automatically. It retains all other recommendations for preview, editing and additional exports. `output.aspect` is `original|vertical`; `caption_style` is `none|poppins_highlight`. `subtitle_style` uses the existing caption style model. Poppins requires actual word timing; the existing alignment path supplies it when needed. Analysis settings and caches exclude export-only options.

The job's `auto_export` field describes `queued|processing|ready|failed|skipped` and includes an `export_id` once created. If no complete candidate passes validation, it is `skipped` with an explanation; no filler clip is generated. Failure/cancellation retains the source and existing results. Retrying a failed export retries that export without reacquiring media or retranscribing it.

### Progress and recovery

The callback is `progress(fraction, message, details=None)`; legacy two-argument callbacks remain supported. Fractions are `0..1` or `None` when indeterminate. Stages include `validating`, `getting_metadata`, `retrieving_source`, `merging_media` and `verifying_media`.

Public `job.ingestion` contains an allowlisted `phase`, `downloaded_bytes`, `total_bytes`, `total_bytes_estimate`, `speed_bps`, `eta_seconds`, `percent`, `cache_hit` and sanitized `metadata`. Unknown statistics are `null`, and speed/ETA are cleared outside download. Metadata contains title, duration, dimensions, FPS and at most 80 format descriptions: ID, dimensions, codecs, extension, size and audio/video presence. Signed CDN URLs, local paths, credentials and raw provider diagnostics are excluded.

`job.source_error` supplies a safe `code`, `retryable` flag and recovery action. Network/timeouts support retry; access denial, unsupported formats and unavailable media remain distinct reasons with upload recovery. The downloader has bounded transfer retries, cancellation and a hard timeout. It does not repeatedly change clients or credentials after an access denial. The existing project URL and settings remain available when acquisition fails.

Health returns `youtube_ingest:{enabled, available, reason}` without credentials. Readiness means dependencies/configuration are ready, not that every source can be fetched. The verified local source cache checks content hashes and media, obeys retention and storage limits, and copies a source into each project independently. Changing clip duration reuses the project's source and transcript; another project can reuse an eligible cache entry. Cache cleanup never removes a project's source or completed export.

Owned-media integration fixtures exercise provider transport, failure recovery, cache reuse, real FFmpeg encoding/probing, range playback, duration caps and audio/video synchronization. Fixture ASR uses explicitly authored timestamps and is not a live speech-recognition or YouTube-access claim. Report actual live checks separately.
