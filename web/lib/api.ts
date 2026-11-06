// The only place the interface talks to the server. Same origin in production
// (FastAPI serves this app), so cookies ride along and there is no CORS.

import type {
  AuthStatus,
  EditCatalogue,
  MusicCatalogue,
  Card,
  Film,
  FilmPlayback,
  FilmVersion,
  JobSnapshot,
  RunResponse,
  ShotOptions,
  Storyboard,
  User,
  VoiceCatalogue,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    // No answer at all: the server is down or restarting. Say so, rather than
    // letting every caller guess with its own vague "couldn't…".
    throw new ApiError(0, "Can't reach the server. Check it's running, then try again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* not JSON — keep the status text */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export type PlanOptions = {
  prompt: string;
  target_duration_s: number;
  scene_count: number;
  format: "16:9" | "9:16" | "1:1";
  style: "realistic" | "anime";
};

export type RenderOptions = {
  with_bgm?: boolean;
  with_subtitles?: boolean;
  subtitle_language?: string;
  burn_subtitles?: boolean;
  tts_engine?: string | null;
};

export type SceneEdit = {
  title?: string;
  visual_prompt?: string;
  dialogue?: Record<string, string>;
};

export type ShotChange = {
  move?: string;
  framing?: "wide" | "medium" | "close";
  reset?: boolean;
};

export const api = {
  // accounts
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (email: string, password: string) =>
    post<{ user: User }>("/api/auth/login", { email, password }),
  register: (email: string, password: string) =>
    post<{ user: User }>("/api/auth/register", { email, password }),
  logout: () => post<{ ok: boolean }>("/api/auth/logout"),

  // library
  films: () => request<Film[]>("/api/projects/"),

  // the loop: plan -> storyboard -> render -> watch
  plan: (opts: PlanOptions) => post<RunResponse>("/api/pipeline/plan", { ...opts, with_preview: true }),
  storyboard: (pid: string, engine?: string | null) =>
    request<Storyboard>(
      `/api/pipeline/storyboard/${pid}${engine ? `?engine=${encodeURIComponent(engine)}` : ""}`,
    ),
  editScene: (pid: string, sceneId: string, edit: SceneEdit) =>
    request<Storyboard>(`/api/pipeline/storyboard/${pid}/${sceneId}`, {
      method: "PATCH",
      body: JSON.stringify(edit),
    }),
  /** Change a storyboard's format and/or style: every picture is drawn again, so it is a job. */
  respec: (pid: string, change: { format?: string; style?: string }) =>
    request<RunResponse>(`/api/pipeline/spec/${pid}`, { method: "PATCH", body: JSON.stringify(change) }),
  /** What can be edited, and the choices (transition styles and speeds). */
  editOps: () => request<EditCatalogue>("/api/edit/ops"),
  /** The music library, the ambience kinds and the upload limits. */
  musicCatalogue: () => request<MusicCatalogue>("/api/edit/music"),
  /** One shared editing operation (DST-39): what a click does, and what a typed sentence turns
   *  into. Before a render it answers with the storyboard; on a finished film the film is cut
   *  again, so it answers with a job to follow. */
  applyOp: (pid: string, op: string, params: Record<string, unknown>) =>
    request<Storyboard | RunResponse>(`/api/edit/${pid}/op`, {
      method: "POST",
      body: JSON.stringify({ op, params }),
    }),
  shotOptions: () => request<ShotOptions>("/api/pipeline/shot-options"),
  /** Before a render this answers with the storyboard; on a finished film the film
   *  is cut again, so it answers with a job to follow. */
  editShot: (pid: string, sceneId: string, shotId: string, change: ShotChange) =>
    request<Storyboard | RunResponse>(
      `/api/pipeline/storyboard/${pid}/${sceneId}/shots/${shotId}`,
      { method: "PATCH", body: JSON.stringify(change) },
    ),
  render: (pid: string, opts: RenderOptions) =>
    post<RunResponse>(`/api/pipeline/render/${pid}`, opts),
  film: (pid: string) => request<FilmPlayback>(`/api/pipeline/film/${pid}`),
  jobStatus: (pid: string) => request<JobSnapshot>(`/api/pipeline/status/${pid}`),
  cancel: (jobId: string) => post<{ status: string }>(`/api/jobs/${jobId}/cancel`),

  // after the render: change it in a sentence, or go back
  edit: (pid: string, query: string) => post<RunResponse>(`/api/edit/${pid}`, { query }),
  versions: (pid: string) => request<FilmVersion[]>(`/api/history/${pid}/film`),
  goBack: (pid: string, version: number) =>
    post<{ version: number }>(`/api/history/${pid}/revert/${version}`),

  // options
  languages: () => request<string[]>("/api/pipeline/languages"),
  voices: () => request<VoiceCatalogue>("/api/voices/"),
  previewVoice: (engine: string, voice: string) =>
    post<{ url: string; engine: string; fell_back: boolean }>("/api/voices/preview", {
      engine,
      voice,
    }),
};

/** A file that keeps its name across versions, made distinct per version so
 * the browser doesn't play the cached cut after an edit. */
export function versioned(url: string, version: number): string {
  return `${url}${url.includes("?") ? "&" : "?"}v=${version}`;
}

export type { Card };
