import type { CSSProperties, ComponentType } from "react";

export type ParticlesProps = {
  particleCount?: number;
  particleSpread?: number;
  speed?: number;
  particleColors?: string[];
  moveParticlesOnHover?: boolean;
  particleHoverFactor?: number;
  alphaParticles?: boolean;
  particleBaseSize?: number;
  sizeRandomness?: number;
  cameraDistance?: number;
  disableRotation?: boolean;
  pixelRatio?: number;
  shape?: string;
  glow?: number;
  twinkle?: number;
  depthOfField?: number;
  connections?: boolean;
  connectionDistance?: number;
  hoverEffect?: string;
  hoverRadius?: number;
  clickBurst?: boolean;
  drift?: string;
  driftSpeed?: number;
  variation?: number;
  intro?: boolean;
  opacity?: number;
  lightMode?: boolean;
  paused?: boolean;
  className?: string;
  style?: CSSProperties;
};

declare const Particles: ComponentType<ParticlesProps>;
export default Particles;
