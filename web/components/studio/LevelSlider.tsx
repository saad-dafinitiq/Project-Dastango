"use client";

import { useEffect, useRef } from "react";

import WakeSlider from "@/vendor/react-bits/WakeSlider";

import { useIsDark } from "@/components/useTheme";

/** A level you drag, as a row of bars that rise in the wake of the pointer (React Bits' Wake Slider, wrapped). It reports every value
 *  as it moves; `onSettle` is called once the pointer has been still for a moment, which is when a change is worth making. */
export function LevelSlider({
  value,
  min,
  max,
  step,
  onChange,
  onSettle,
  label,
  format,
  disabled,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  onSettle: (v: number) => void;
  label: string;
  format: (v: number) => string;
  disabled?: boolean;
}) {
  const dark = useIsDark();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <WakeSlider
      value={value}
      min={min}
      max={max}
      step={step}
      bars={28}
      height={40}
      restHeight={8}
      gap={3}
      fillColor={dark ? "#ebedf1" : "#352118"}
      trackColor={dark ? "#2f3034" : "#cbb9a6"}
      crestColor={dark ? "#e39a45" : "#923d17"}
      showValue
      formatValue={format}
      ariaLabel={label}
      disabled={disabled}
      onChange={(v: number) => {
        onChange(v);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => onSettle(v), 450);
      }}
    />
  );
}
