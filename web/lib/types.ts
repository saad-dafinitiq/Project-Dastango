// Shapes of what the FastAPI backend returns. Kept to what the interface reads.

export type User = {
  id: string;
  email: string;
  role: "user" | "admin";
};

export type AuthStatus = {
  authenticated: boolean;
  user: User | null;
  needs_setup: boolean;
  signups_allowed: boolean;
  min_password_length: number;
  /** GitHub sign-in: set up on this server, and linked to this account. */
  github?: { enabled: boolean; connected: boolean };
};

export type Line = {
  line_id: string;
  character: string;
  voice: string;
  text: string;
};

/** One shot a scene is cut into (DST-36). Times are estimates until the voices exist. */
export type CardShot = {
  shot_id: string;
  framing: "wide" | "medium" | "close" | "cutaway";
  /** Which picture it is cut from: the scene's wide one, a character's portrait, or a
   *  detail drawn when the film is rendered. */
  source: "wide" | "portrait" | "cutaway";
  move: string;
  character_id: string | null;
  character: string | null;
  start_ms: number;
  end_ms: number;
  /** For a medium shot: the part of the wide picture it keeps. */
  crop: { w: number; x: number; y: number } | null;
  /** Which of the scene's lines this shot carries, by the estimated cut times. */
  line_ids: string[];
  /** How this shot comes in (DST-39): null for the first shot of the film. `between` is a cut
   *  between scenes; `chosen` means the creator set it, otherwise it is the default. */
  transition: { kind: string; ms: number; chosen: boolean; between: boolean } | null;
  /** The camera move's id (`pan_left`), where `move` is the same in words. */
  move_id: string;
  /** The creator chose this move or framing, over what the plan picked. */
  overridden: boolean;
  /** A close-up needs a face: only a shot someone speaks in can have one. */
  can_close: boolean;
};

/** The editing operations the server offers, and the choices of the transition one (GET /api/edit/ops). */
export type TransitionStyle = { id: string; label: string; about: string; main: boolean };
export type EditCatalogue = {
  ops: { name: string; summary: string; options: { styles?: TransitionStyle[]; speeds?: Record<string, number>; effects?: { id: string; label: string; about: string }[]; strengths?: string[] } }[];
};

export type ShotOptions = {
  moves: { id: string; label: string; about: string }[];
  framings: { id: "wide" | "medium" | "close"; about: string }[];
};

/** One scene card. Streamed during planning, read back from the storyboard. */
export type Card = {
  scene_id: string;
  index: number;
  title: string;
  setting?: string;
  tone: string;
  move: string;
  estimated_ms: number;
  /** The shots the scene is cut into, in order. */
  shots?: CardShot[];
  lines: Line[];
  /** Who speaks in the scene, in the order they first do. */
  characters?: { character_id: string; name: string }[];
  preview_url?: string | null;
  visual_prompt?: string;
};

export type CastMember = {
  character_id: string;
  name: string;
  role: string;
  voice: string;
  /** One sentence about who they are. */
  description?: string;
  /** Absent while the portrait is still being drawn. */
  portrait_url?: string | null;
  /** False when the picture is only a stand-in (nothing could be drawn yet). */
  drawn?: boolean;
};

export type ImageBudget = { plan: number; render: number };

/** What the script's length check found: planned length against the target. */
export type LengthCheck = {
  planned_s: number;
  target_s: number;
  within: boolean;
  repairs: number;
};

/** What the creator chose before planning (DST-34), and whether the script held to the scene count. */
export type FilmSpec = {
  format: "16:9" | "9:16" | "1:1";
  style: "" | "realistic" | "anime";
  scene_check?: { asked: number; got: number; repairs: number; merged: number; within: boolean } | null;
};

export type Storyboard = {
  project_id: string;
  prompt: string;
  title: string;
  logline: string;
  frames: Card[];
  cast: CastMember[];
  /** Set when a character the creator named had no lines and was left out of the cast. */
  cast_note?: string | null;
  images: ImageBudget;
  length: LengthCheck | null;
  stage: "draft" | "storyboard" | "rendered";
  version: number;
  spec?: FilmSpec;
  /** Present on the answer to a scene edit that asked for a new picture. When `ok`
   *  is false the words are saved but the picture could not be redrawn. */
  redraw?: { scene_id: string; ok: boolean; message?: string };
};

export type Film = {
  project_id: string;
  title: string;
  prompt: string;
  version: number;
  updated_at: string;
  created_at?: string;
  format?: "16:9" | "9:16" | "1:1";
  video_url: string | null;
  poster_url: string | null;
  stage: string;
  scene_count: number;
  duration_ms: number | null;
};

export type Chapter = {
  scene_id: string;
  index: number;
  title: string;
  tone: string;
  start_ms: number | null;
  end_ms: number | null;
  poster_url: string | null;
};

export type SubtitleTrack = {
  language: string;
  code: string;
  url: string;
  burned_in: boolean;
};

export type FilmPlayback = {
  project_id: string;
  title: string;
  logline: string;
  video_url: string;
  duration_ms: number;
  width: number;
  height: number;
  fps: number;
  version: number;
  /** Which cut this is for the creator: the first render is 1. */
  cut: number;
  format: "16:9" | "9:16" | "1:1";
  style: "" | "realistic" | "anime";
  /** Scenes drawn with a placeholder because every image service was at its limit. */
  stand_in_scenes: string[];
  chapters: Chapter[];
  subtitles: SubtitleTrack[];
  /** A finishing effect per scene that has one (DST-39). */
  effects?: Record<string, { kind: string; strength: string }>;
  /** Which titles are on the picture (DST-39), and their own words if the creator set any. */
  titles?: {
    title_card: boolean;
    title_text: string | null;
    scene_titles: boolean;
    lower_thirds: boolean;
    end_card: boolean;
    end_text: string | null;
    credits_text: string | null;
  } | null;
  /** The mix (DST-39); empty for a film with no sound recorded. Levels are percent of how it was made. */
  sound?: {
    music_on: boolean;
    music_pct: number;
    voices_pct: number;
    ambience_pct: number;
    music_track: string | null;
    scene_music: Record<string, string>;
    moods: Record<string, string>;
    ambience: Record<string, string>;
    uploads: { id: string; title: string; seconds: number }[];
  };
};

/** GET /api/edit/music: the library, the ambience kinds and what an upload may be. */
export type MusicCatalogue = {
  tracks: { id: string; title: string; mood: string | null; seconds: number; licence: string; upload: boolean }[];
  moods: string[];
  ambience: { id: string; label: string; about: string }[];
  limits: { max_mb: number; min_seconds: number; max_seconds: number; extensions: string[] };
};

export type ProgressEvent = {
  id: number;
  job_id: string;
  project_id: string;
  phase: string;
  status: string;
  message: string;
  progress: number;
  payload: Record<string, unknown> | null;
  created_at: string | null;
};

/** One version of a finished film, in the creator's words. */
export type FilmVersion = {
  version: number;
  /** What the creator calls it: the first render is cut 1 (`version` counts the storyboard). */
  number: number;
  created_at: string;
  current: boolean;
  kind: "render" | "edit" | "revert" | "rerun" | "other";
  label: string;
  detail: string;
  restored?: number | null;
};

export type JobSnapshot = {
  project_id: string;
  job_id?: string;
  kind?: "plan" | "render" | "run_full" | "rerun_phase" | "edit" | "revert" | "shot" | "respec" | "op";
  prompt?: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  phase?: string;
  progress?: number;
  message?: string;
  error?: string | null;
};

export type RunResponse = {
  project_id: string;
  job_id: string;
  status: string;
  websocket: string;
};

export type Voice = { id: string; label: string; gender: string };

export type VoiceEngine = {
  name: string;
  label: string;
  summary: string;
  open_source: boolean;
  needs_network: boolean;
  available: boolean;
  unavailable_reason: string | null;
  is_default: boolean;
  voices: Voice[];
};

export type VoiceCatalogue = { default: string; engines: VoiceEngine[] };
