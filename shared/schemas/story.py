"""Phase 1 schemas — story, characters, and scene-by-scene script."""
from __future__ import annotations
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator


class Character(BaseModel):
    """A named character with voice and visual identity."""
    id: str = Field(..., description="stable identifier, e.g. 'char_narrator'")
    name: str
    role: Literal["protagonist", "antagonist", "supporting", "narrator"] = "supporting"
    description: str = Field(..., description="one-sentence personality summary")
    visual_description: str = Field(
        ..., description="appearance details for image generation"
    )
    voice_style: str = Field(
        default="neutral",
        description="warm/whispered/deep/cheerful/anxious/etc — drives TTS",
    )
    voice_gender: Literal["male", "female", "neutral"] = "neutral"
    voice_age: Literal["child", "young", "adult", "elderly"] = "adult"
    # Locked look: the canonical description + fixed seed every image prompt uses,
    # so the character doesn't change face between shots (agents/story_agent/appearance.py).
    appearance_lock: str = ""
    image_seed: Optional[int] = None


class CharacterRoster(BaseModel):
    """Full cast for the short film."""
    characters: List[Character]

    def get(self, char_id: str) -> Optional[Character]:
        return next((c for c in self.characters if c.id == char_id), None)


class DialogueLine(BaseModel):
    """A single spoken line within a scene."""
    line_id: str
    character_id: str = Field(..., description="must match a Character.id")
    text: str
    emotion: str = Field(default="neutral", description="happy/sad/angry/curious/...")
    duration_ms: int = Field(default=2500, ge=500)


class TransitionSpec(BaseModel):
    """How one cut comes in (DST-39): a style from `shared.transitions` and how long it takes.
    `ms` None means that style's normal length; a cut has none."""
    kind: str
    ms: Optional[int] = None


class ShotOverride(BaseModel):
    """What the creator chose for one shot, over what the coverage plan picked (DST-38).

    Both fields are optional: an absent one means "as planned". Keyed on the scene
    by shot id (`<scene>_est`, `<scene>_s2`, ...), which is stable while the scene
    keeps the same number of shots; an override whose shot no longer exists is
    ignored rather than applied to a different one.
    """
    move: Optional[str] = None       # a key of agents.video_agent.camera.MOVES
    framing: Optional[Literal["wide", "medium", "close"]] = None
    # How this shot comes in from the one before it (a cut inside a scene).
    transition: Optional[TransitionSpec] = None


class EffectSpec(BaseModel):
    """A finishing effect drawn over a whole scene (DST-39, shared/finish.py)."""
    kind: str
    strength: str = "normal"


class Scene(BaseModel):
    """One narrative scene — visual + dialogue + timing."""
    scene_id: str
    index: int = Field(..., ge=0)
    title: str
    setting: str = Field(..., description="where + when")
    tone: str = Field(default="neutral")
    visual_prompt: str = Field(
        ..., description="prompt-engineered description for image generation"
    )
    camera: str = Field(
        default="medium shot, eye level",
        description="cinematography guidance: close-up / wide / aerial / etc",
    )
    duration_ms: int = Field(default=5000, ge=1000)
    dialogue: List[DialogueLine] = Field(default_factory=list)
    music_mood: str = Field(default="ambient", description="mood for BGM selection")
    transition_in: str = Field(default="fade", description="cut/fade/slide/zoom")
    # How the scene's first shot comes in from the previous scene's last (a cut between
    # scenes). None = the default. The older `transition_in` is what the story model wrote
    # and is not read: it was never applied, and starting to would change old films.
    cut_in: Optional[TransitionSpec] = None
    effect: Optional[EffectSpec] = None
    shot_overrides: Dict[str, ShotOverride] = Field(
        default_factory=dict,
        description="the creator's own camera move / framing per shot, by shot id")

    @field_validator("dialogue")
    @classmethod
    def _check_durations(cls, v: List[DialogueLine]) -> List[DialogueLine]:
        return v


class GraphicsSpec(BaseModel):
    """Titles drawn over the film (DST-39), all off until the creator asks. They are overlays on
    the timeline, so none of them changes how long the film is (shared/graphics.py)."""
    title_card: bool = False
    title_text: Optional[str] = Field(default=None, description="replaces the story's title on the title card")
    scene_titles: bool = False
    lower_thirds: bool = False
    end_card: bool = False
    end_text: Optional[str] = Field(default=None, description="replaces 'The End'")
    credits_text: Optional[str] = Field(default=None, description="a line under the end card")


class StoryOutput(BaseModel):
    """Top-level narrative produced by the story agent."""
    project_id: str
    title: str
    logline: str = Field(..., description="one-line pitch")
    synopsis: str = Field(..., description="paragraph summary")
    genre: str = Field(default="drama")
    visual_style: str = Field(
        default="",
        description="how the film should look (medium, palette, lighting, lens); "
                    "drives every image prompt, see agents/story_agent/visual_style.py",
    )
    themes: List[str] = Field(default_factory=list)
    arc: str = Field(
        default="three-act",
        description="three-act / hero-journey / loop / experimental",
    )
    target_duration_s: int = Field(default=45, ge=15, le=600)
    # DST-34: chosen before planning, kept with the script so every image, shot and
    # edit that reads the story reads them. A film made before has the defaults:
    # 16:9, and style "" (the look its genre gave it).
    format: Literal["16:9", "9:16", "1:1"] = "16:9"
    style: Literal["", "realistic", "anime"] = ""
    graphics: GraphicsSpec = Field(default_factory=GraphicsSpec)


class ScriptOutput(BaseModel):
    """Complete pipeline-1 output: story + roster + scenes."""
    story: StoryOutput
    characters: CharacterRoster
    scenes: List[Scene]

    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.scenes)
