import { useEffect, useState } from "react";

interface Props {
  value: number | null;
  onChange: (v: number) => void;
  step?: number | "any";
  min?: number;
  max?: number;
  id?: string;
  "aria-label"?: string;
}

/** Number input that keeps what the user typed and only reports parsable values. */
export function NumInput({ value, onChange, step = "any", min, max, id, "aria-label": aria }: Props) {
  const [text, setText] = useState(value === null ? "" : String(value));
  useEffect(() => {
    if (value !== null && Number(text) !== value) setText(String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <input
      id={id}
      aria-label={aria}
      type="number"
      inputMode="decimal"
      value={text}
      step={step}
      min={min}
      max={max}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isFinite(n)) onChange(n);
      }}
    />
  );
}
