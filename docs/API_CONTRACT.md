# PODCUT AI local API contract

Frontend uses relative `/api` requests, proxied by a streaming Next.js Node route to `http://127.0.0.1:8000`. It forwards uploads/downloads without buffering or cloning the entire body. No external rewrite is used (Next 16's cloned rewrite body truncates large uploads).
All endpoints return JSON except named media downloads. Error bodies use FastAPI `detail`.

- `GET /api/health`: `{status, ffmpeg:bool, ffprobe:bool, whisper:bool, openai:bool, whisper_model:string, max_upload_mb:number, storage_limit_gb:number}`.
- `GET /api/jobs`: `Job[]`, latest first.
- `POST /api/jobs` JSON `{title?:string,youtube_url?:string}`: Job, awaiting upload. Only valid YouTube reference URLs allowed. No URL downloading.
- `GET /api/jobs/{id}`: Job.
- `POST /api/jobs/{id}/source` multipart `file`: Job. File streamed to disk, validated with FFprobe. Does not analyze yet.
- `POST /api/jobs/{id}/transcript` multipart `file`: Job. Import timestamped JSON or SRT, in place of automatic transcription. Requires source first.
- `GET /api/jobs/{id}/transcript`: `{segments: Segment[], language:string, word_timestamps:bool}`.
- `POST /api/jobs/{id}/analyze` JSON `{clip_count:3..10, min_duration:20..90, max_duration:20..90, target_duration:20..90, language:'auto'|'id'|'en', manual_start?:number, manual_end?:number}`: Job. Queue worker, polls show progress.
- `POST /api/jobs/{id}/retry`: Job. Retry previous analysis or export without removing previous candidates.
- `POST /api/jobs/{id}/cancel`: Job.
- `DELETE /api/jobs/{id}`: `{deleted:true}`. Refuses active jobs; removes local files.
- `GET /api/jobs/{id}/media`: source video with HTTP range support.
- `GET /api/jobs/{id}/poster`: JPEG if available.
- `GET /api/jobs/{id}/captions.vtt`: full source timed captions.
- `POST /api/jobs/{id}/exports` JSON `{start:number,end:number,aspect:'original'|'vertical',crop_x:0..1,caption_style:'none'|'clean'|'bold'}`: Job. Queue export. Default crop_x .5. No face tracking claim; manual crop.
- `GET /api/jobs/{id}/exports/{export_id}/file`: verified MP4 attachment.
- `GET /api/jobs/{id}/exports/{export_id}/captions.srt`: relative-time SRT attachment.

Job fields: `id,title,youtube_url:string|null,source_name:string|null,status,stage,progress:number|null,message,error:string|null,warnings:string[],created_at,updated_at,media:Media|null,candidates:Candidate[],exports:Export[],analysis_mode:'ai'|'heuristic'|null,transcript_ready:bool,settings:AnalysisSettings|null`.

Status: `awaiting_upload|uploaded|queued|probing|transcribing|analyzing|ready|exporting|blocked|failed|cancelled`.
Media: `{duration,width,height,fps,video_codec,audio_codec:string|null,has_audio:bool}`.
Export: `{id,start,end,aspect,width,height,status:'queued'|'processing'|'ready'|'failed'|'cancelled',error:string|null,caption_style,created_at,filename:string|null,has_captions?:bool}`. SRT downloads require real captions overlapping the exported interval.
Candidate: `{id,start,end,duration,title,hook_exact_quote,category,reason,warning:string|null,scores:{hook_strength,content_payoff,standalone_clarity,emotional_impact,edit_readiness},score:number,source_evidence:[{start,end,text}],review_mode:'ai'|'heuristic'}`. Scores are 0..100. Weighted score .30/.25/.20/.15/.10, rounded 1 decimal.
Segment: `{start:number,end:number,text:string,words?:[{start,end,word}],speaker?:string|null}`.

No fabricated clips/results in initial production state. UI explains heuristic fallback, YouTube source requirements, transcription model download, and crop limitations. No client API key fields.
# Active Speaker M2 extension

`POST /api/jobs/{id}/reframe/analyze` accepts `mode: "active_speaker"` on the
existing selected `start`/`end` interval (at most 600 seconds). It queues local
analysis and reuses matching M1 shots/tracks. Missing optional runtimes/weights
return 409 with setup guidance. This endpoint never runs semantic clipping or ASR.
Job `reframe.status/message` and job progress report work; the existing cancel
endpoint stops the isolated model/FFmpeg process tree on Windows.

`GET /api/health` exposes `reframe.active_speaker` and
`reframe.active_speaker_details` (availability, device, experimental status,
uncalibrated score semantics). No credentials or model filesystem paths are returned.

Plans add mode `active_speaker`; shots add focus `active`, decision
`active_speaker`, `speaker_intervals`, and `speaker_overrides`.
Model intervals cover the entire shot and contain source-global `start/end`,
`track_id`, `focus`, actual ASD `confidence`/raw `score`, `source`, `reason`, and
`review_required`. Fallback/manual confidence and score are null. Raw AV evidence
is cached separately under the project. Track IDs remain local to each camera shot.

`PUT /api/jobs/{id}/reframe` can edit `speaker_overrides`: nonoverlapping,
shot-bounded `{start,end,focus: left|right|both|locked,track_id?:number|null}`.
Interval overrides precede M1 manual keys and model decisions. Saving an override
does not run either model. Source identity, shot boundaries, face/model evidence,
model confidence and analysis remain server-owned even if the client changes them.
The existing export request uses `reframe_id` and freezes the plan for FFmpeg.
See [measured M2 verification and remaining gaps](ACTIVE_SPEAKER_M2_VERIFICATION.md).
