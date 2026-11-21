import type { ReframeKeyframe, ReframePlan, ReframeShot, SpeakerInterval, SpeakerOverride } from "./types";

export const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const even = (value: number) => Math.max(2, Math.floor(value / 2) * 2);
type ReframePose = ReframeKeyframe & {focus:ReframeShot["focus"];shot_id:string};

export function shotAt(plan: ReframePlan, time: number): ReframeShot {
  return plan.shots.find(shot => time >= shot.start && time < shot.end) || (time < plan.start ? plan.shots[0] : plan.shots[plan.shots.length - 1]);
}

function interpolate(keys: ReframeKeyframe[], time: number, smoothing: number): ReframeKeyframe {
  if (!keys.length) return { time, x: .5, y: .5, zoom: 1, manual: false };
  const sorted = [...keys].sort((a, b) => a.time - b.time);
  if (time <= sorted[0].time) return { ...sorted[0], time };
  if (time >= sorted[sorted.length - 1].time) return { ...sorted[sorted.length - 1], time };
  const after = sorted.findIndex(key => key.time > time);
  const a = sorted[after - 1], b = sorted[after];
  const r = clamp((time - a.time) / (b.time - a.time), 0, 1), s = clamp(smoothing, 0, 1);
  const f = (1 - s) * r + s * r * r * (3 - 2 * r);
  return { time, x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f,
    zoom: a.zoom + (b.zoom - a.zoom) * f, manual: a.manual || b.manual };
}

function basePose(plan: ReframePlan, shot: ReframeShot, time: number) {
  const manual = shot.keyframes.filter(key => key.manual);
  let keys = manual.length ? manual : shot.keyframes;
  if (!manual.length && (shot.focus === "left" || shot.focus === "right")) {
    const samples = [...shot.face_samples].sort((a, b) => a.time - b.time);
    const seed = samples.find(sample => sample.faces.length >= 2) || samples.find(sample => sample.faces.length);
    if (seed) {
      const ordered = [...seed.faces].sort((a, b) => (a.bbox[0] + a.bbox[2] / 2) - (b.bbox[0] + b.bbox[2] / 2));
      const target = shot.focus === "left" ? ordered[0] : ordered[ordered.length - 1];
      const baseHeight = Math.min(plan.source_height, plan.source_width * 16 / 9);
      keys = samples.flatMap(sample => {
        const face = sample.faces.find(item => item.track_id === target.track_id);
        if (!face) return [];
        const zoom = interpolate(shot.keyframes, sample.time, 0).zoom;
        return [{ time: sample.time, x: face.bbox[0] + face.bbox[2] / 2,
          y: clamp(face.bbox[1] - .5 * face.bbox[3] + .5 * (baseHeight / zoom) / plan.source_height, 0, 1),
          zoom, manual: false }];
      });
    } else keys = [];
    if (!keys.length) keys = [{ time: shot.start, x: shot.focus === "left" ? .25 : .75, y: .5, zoom: shot.keyframes[0]?.zoom || 1, manual: false }];
  }
  const pose = shot.focus === "locked" && keys.length ? { ...keys[0], time } : interpolate(keys, time, plan.smoothing);
  return { ...pose, x: clamp(pose.x, 0, 1), y: clamp(pose.y, 0, 1), zoom: clamp(pose.zoom, 1, 3), focus: shot.focus, shot_id: shot.id };
}

type PreparedPath = {keys:ReframeKeyframe[];times:number[]};
type PreparedSegment = {item:SpeakerInterval;anchor:ReframePose|null;ease:boolean};
type PreparedShot = {baseline:PreparedPath;manual:boolean;paths:Map<number,PreparedPath>;
  focusPaths:Map<string,PreparedPath>;segments:PreparedSegment[];starts:number[]};

// Editor plans are immutable snapshots. Repeated animation frames reuse their
// face paths and compiled boundary compositions; replaced plans are collectable.
const preparedPlans = new WeakMap<ReframePlan,Map<ReframeShot,PreparedShot>>();
const preparePath = (keys:ReframeKeyframe[]):PreparedPath => {
  const sorted=[...keys].sort((a,b)=>a.time-b.time);
  return {keys:sorted,times:sorted.map(key=>key.time)};
};
function upperBound(times:number[],time:number) {
  let low=0,high=times.length;
  while(low<high) {const middle=(low+high)>>>1;if(times[middle]<=time)low=middle+1;else high=middle;}
  return low;
}
function pathPose(path:PreparedPath,time:number,smoothing:number):ReframeKeyframe {
  const {keys,times}=path;
  if(!keys.length)return {time,x:.5,y:.5,zoom:1,manual:false};
  if(time<=times[0])return {...keys[0],time};
  if(time>=times[times.length-1])return {...keys[keys.length-1],time};
  const index=upperBound(times,time)-1,a=keys[index],b=keys[index+1];
  const r=(time-a.time)/(b.time-a.time),s=clamp(smoothing,0,1);
  const ease=(1-s)*r+s*r*r*(3-2*r);
  return {time,x:a.x+(b.x-a.x)*ease,y:a.y+(b.y-a.y)*ease,zoom:a.zoom+(b.zoom-a.zoom)*ease,manual:false};
}
function prepareShot(plan:ReframePlan,shot:ReframeShot):PreparedShot {
  let shots=preparedPlans.get(plan);
  if(!shots) {shots=new Map();preparedPlans.set(plan,shots);}
  const cached=shots.get(shot);if(cached)return cached;
  const manualKeys=shot.keyframes.filter(key=>key.manual);
  const prepared:PreparedShot={baseline:preparePath(manualKeys.length?manualKeys:shot.keyframes),manual:!!manualKeys.length,
    paths:new Map(),focusPaths:new Map(),segments:[],starts:[]};
  const intervals=shot.focus==="active"&&!prepared.manual?shot.speaker_intervals||[]:[];
  const choices=[...intervals,...shot.speaker_overrides||[]];
  const tracks=new Set(choices.flatMap(item=>item.track_id!=null?[item.track_id]:[]));
  const grouped=new Map<number,ReframeKeyframe[]>([...tracks].map(id=>[id,[]]));
  const baseHeight=Math.min(plan.source_height,plan.source_width*16/9);
  for(const sample of shot.face_samples)for(const face of sample.faces) {
    const path=grouped.get(face.track_id);if(!path)continue;
    const zoom=pathPose(prepared.baseline,sample.time,0).zoom;
    path.push({time:sample.time,x:clamp(face.bbox[0]+face.bbox[2]/2,0,1),
      y:clamp(face.bbox[1]-.5*face.bbox[3]+.5*baseHeight/zoom/plan.source_height,0,1),zoom,manual:false});
  }
  for(const [track,keys] of grouped)prepared.paths.set(track,preparePath(keys));
  for(const focus of new Set(choices.map(item=>item.focus)))if(focus==="left"||focus==="right") {
    const candidates=shot.face_samples.filter(sample=>sample.faces.length);
    const initial=candidates.find(sample=>sample.faces.length>=2)||candidates[0];
    const ordered=initial?[...initial.faces].sort((a,b)=>(a.bbox[0]+a.bbox[2]/2)-(b.bbox[0]+b.bbox[2]/2)):[];
    const track=ordered.length?ordered[focus==="left"?0:ordered.length-1].track_id:null;
    const keys=track==null?[]:candidates.flatMap(sample=>{
      const face=sample.faces.find(value=>value.track_id===track);if(!face)return [];
      const zoom=pathPose(prepared.baseline,sample.time,0).zoom;
      return [{time:sample.time,x:clamp(face.bbox[0]+face.bbox[2]/2,0,1),
        y:clamp(face.bbox[1]-.5*face.bbox[3]+.5*baseHeight/zoom/plan.source_height,0,1),zoom,manual:false}];
    });
    prepared.focusPaths.set(focus,preparePath(keys.length?keys:[{time:shot.start,x:focus==="left"?.25:.75,y:.5,zoom:prepared.baseline.keys[0]?.zoom||1,manual:false}]));
  }
  // Iteration carries the actual composed pose through arbitrarily many
  // fallback reason changes, including a switch interrupted midway by a hold.
  for(const item of intervals) {
    const previous=prepared.segments[prepared.segments.length-1];
    const anchor=previous?segmentPose(plan,shot,prepared,previous,item.start):null;
    prepared.segments.push({item,anchor,ease:!!previous&&item.source==="model"&&
      (previous.item.track_id!==item.track_id||previous.item.focus==="locked")});
    prepared.starts.push(item.start);
  }
  shots.set(shot,prepared);
  return prepared;
}
function speakerPose(plan:ReframePlan,shot:ReframeShot,prepared:PreparedShot,item:SpeakerInterval|SpeakerOverride,time:number,manual:boolean):ReframePose {
  if(manual&&item.focus==="locked"&&item.track_id==null)return {...shotPose(plan,shot,prepared,item.start,false),manual:true};
  let pose:ReframeKeyframe;
  if(item.focus==="left"||item.focus==="right")pose=pathPose(prepared.focusPaths.get(item.focus)!,time,plan.smoothing);
  else if(item.track_id!=null) {
    const path=prepared.paths.get(item.track_id)!,index=upperBound(path.times,time);
    const distance=Math.min(...[index-1,index].filter(i=>i>=0&&i<path.times.length).map(i=>Math.abs(path.times[i]-time)));
    if(distance>1)return {time,x:.5,y:.5,zoom:1,manual,focus:"both",shot_id:shot.id};
    pose=pathPose(path,item.focus==="locked"?item.start:time,plan.smoothing);
  } else pose=pathPose(prepared.baseline,item.focus==="locked"?item.start:time,plan.smoothing);
  return {...pose,manual,focus:item.focus==="both"?"both":"auto",shot_id:shot.id};
}
function segmentPose(plan:ReframePlan,shot:ReframeShot,prepared:PreparedShot,segment:PreparedSegment,time:number):ReframePose {
  const {item,anchor}=segment;
  if(item.focus==="locked"&&anchor)return {...anchor,time};
  const pose=speakerPose(plan,shot,prepared,item,time,false),duration=.2+.6*plan.smoothing;
  if(segment.ease&&anchor&&anchor.focus!=="both"&&pose.focus!=="both"&&time<item.start+duration) {
    const r=clamp((time-item.start)/duration,0,1),ease=r*r*(3-2*r);
    pose.x=anchor.x+(pose.x-anchor.x)*ease;pose.y=anchor.y+(pose.y-anchor.y)*ease;pose.zoom=anchor.zoom+(pose.zoom-anchor.zoom)*ease;
  }
  return pose;
}
function shotPose(plan:ReframePlan,shot:ReframeShot,prepared:PreparedShot,time:number,overrides=true):ReframePose {
  time=clamp(time,shot.start,shot.end);
  const override=overrides?shot.speaker_overrides?.find(item=>item.start<=time&&time<item.end):null;
  if(override)return speakerPose(plan,shot,prepared,override,time,true);
  if(shot.focus==="active"&&!prepared.manual) {
    if(!prepared.segments.length)return {time,x:.5,y:.5,zoom:1,manual:false,focus:"both",shot_id:shot.id};
    const index=clamp(upperBound(prepared.starts,time)-1,0,prepared.segments.length-1);
    return segmentPose(plan,shot,prepared,prepared.segments[index],time);
  }
  return basePose(plan,shot.focus==="active"?{...shot,focus:"auto"}:shot,time);
}

export function poseAt(plan:ReframePlan,time:number):ReframePose {
  const shot=shotAt(plan,time);
  return shotPose(plan,shot,prepareShot(plan,shot),time);
}

export function cropAt(plan: ReframePlan, time: number) {
  const pose = poseAt(plan, time), width = plan.source_width, height = plan.source_height;
  if (pose.focus === "both") return { x: 0, y: 0, width: even(width), height: even(height), focus: pose.focus, shot_id: pose.shot_id };
  const cropHeight = Math.min(even(height), even(Math.min(height, width * 16 / 9) / pose.zoom));
  const cropWidth = Math.min(even(width), even(cropHeight * 9 / 16));
  return { x: Math.floor(clamp(pose.x * width - cropWidth / 2, 0, width - cropWidth) / 2) * 2,
    y: Math.floor(clamp(pose.y * height - cropHeight / 2, 0, height - cropHeight) / 2) * 2,
    width: cropWidth, height: cropHeight, focus: pose.focus, shot_id: pose.shot_id };
}
