import type {StyleDef} from '../engine';
import {glide} from './glide';
import {glass} from './glass';
import {whisper} from './whisper';
import {terminal} from './terminal';
import {keyword} from './keyword';
import {accent} from './accent';
import {chip} from './chip';
import {title} from './title';
import {hype} from './hype';
import {block} from './block';
import {impact} from './impact';
import {bottom} from './bottom';
import {stack} from './stack';
import {serif} from './serif';
import {shine} from './shine';
import {karaoke} from './karaoke';
import {sticker} from './sticker';
import {ticket} from './ticket';
import {highlighter} from './highlighter';
import {haiku} from './haiku';
import {duo} from './duo';
import {editorial} from './editorial';
import {downbeat} from './downbeat';
import {singalong} from './singalong';
import {chalkline} from './chalkline';
import {billboard} from './billboard';
import {recipe} from './recipe';
import {cover} from './cover';
import {outline} from './outline';
import {poster} from './poster';
import {rise} from './rise';
import {magazine} from './magazine';
import {flip} from './flip';
import {numeral} from './numeral';
import {split} from './split';

/** Maps a style's `anim.type` (or its `name`) to the component that draws it. One line per style. */
export const REGISTRY: Record<string, StyleDef> = {
  glide,
  glass,
  whisper,
  terminal,
  keyword,
  accent,
  chip,
  title,
  hype,
  block,
  impact,
  bottom,
  stack,
  serif,
  shine,
  karaoke,
  sticker,
  ticket,
  highlighter,
  haiku,
  duo,
  editorial,
  downbeat,
  singalong,
  chalkline,
  billboard,
  recipe,
  cover,
  outline,
  poster,
  rise,
  magazine,
  flip,
  numeral,
  split,
};
