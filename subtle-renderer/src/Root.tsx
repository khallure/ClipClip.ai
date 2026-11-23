import React from 'react';
import {Composition} from 'remotion';
import {Captions} from './Captions';
import type {CaptionsFile, Style} from './engine';

const emptyCaptions: CaptionsFile = {
  version: 1,
  video: {width: 720, height: 1280, fps: 30, durationMs: 1000},
  style: 'glide',
  pages: [],
};

export const Root: React.FC = () => (
  <Composition
    id="Captions"
    component={Captions as React.FC<any>}
    width={720}
    height={1280}
    fps={30}
    durationInFrames={30}
    defaultProps={{captions: emptyCaptions, style: {} as Style, layer: 'all'}}
    calculateMetadata={({props}: {props: any}) => {
      const v = (props.captions as CaptionsFile).video;
      return {
        width: Math.round(v.width),
        height: Math.round(v.height),
        fps: v.fps,
        durationInFrames: Math.max(1, Math.ceil((v.durationMs * v.fps) / 1000 - 1e-6)),
      };
    }}
  />
);
